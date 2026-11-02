"""Billed usage and cost from the Claude Usage & Cost API.

``autonoma.pricing`` estimates spend from list prices as calls happen —
good enough to enforce a budget mid-run, wrong the day a price changes.
This module reads what Anthropic actually bills, through the Admin API's
usage and cost reports (``/v1/organizations/usage_report/messages`` and
``/v1/organizations/cost_report``). Both need an Admin API credential
(``sk-ant-admin…`` key or an unscoped personal/service-account key);
workspace keys and ordinary ``ANTHROPIC_API_KEY`` values are rejected
with 401/403, which ``BillingUnavailable`` surfaces.

Data lands within ~5 minutes of a request and the API allows polling
once a minute, so ``summary()`` caches for a minute. Costs come back as
decimal strings in cents (USD); they are converted to dollars here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from autonoma.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
USER_AGENT = "Autonoma/1.0 (https://github.com/dlekdns08/autonoma)"
_CACHE_TTL_S = 60.0


class BillingUnavailable(Exception):
    """No admin credential, or the API refused it."""


@dataclass
class DayUsage:
    day: str
    model: str
    uncached_input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0


@dataclass
class BillingSummary:
    starting_at: str
    ending_at: str
    total_usd: float
    cost_by_day: dict[str, float]
    cost_by_model: dict[str, float]
    usage: list[DayUsage] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_at": self.starting_at,
            "ending_at": self.ending_at,
            "total_usd": round(self.total_usd, 4),
            "cost_by_day": {k: round(v, 4) for k, v in sorted(self.cost_by_day.items())},
            "cost_by_model": {k: round(v, 4) for k, v in sorted(self.cost_by_model.items())},
            "usage": [u.__dict__ for u in self.usage],
            "fetched_at": self.fetched_at,
        }


def admin_key() -> str:
    return (settings.anthropic_admin_api_key or "").strip()


def _headers(key: str) -> dict[str, str]:
    return {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "user-agent": USER_AGENT,
    }


async def _get_all(http: Any, path: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Follow ``has_more`` / ``next_page`` until the report is complete."""
    buckets: list[dict[str, Any]] = []
    page: str | None = None
    for _ in range(50):  # hard stop; a month at 1d is one page
        query = list(params) + ([("page", page)] if page else [])
        resp = await http.get(f"{API_BASE}{path}", params=query)
        if resp.status_code in (401, 403):
            raise BillingUnavailable(f"admin API refused the credential (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise BillingUnavailable(f"admin API returned HTTP {resp.status_code}")
        body = resp.json()
        buckets.extend(body.get("data") or [])
        if not body.get("has_more"):
            break
        page = body.get("next_page")
        if not page:
            break
    return buckets


def _cents_to_usd(amount: Any) -> float:
    try:
        return float(str(amount)) / 100.0
    except (TypeError, ValueError):
        return 0.0


async def fetch_summary(
    *, days: int = 7, transport: Any = None, now: datetime | None = None
) -> BillingSummary:
    """Daily billed cost (by model) and token usage for the last ``days``."""
    key = admin_key()
    if not key:
        raise BillingUnavailable("AUTONOMA_ANTHROPIC_ADMIN_API_KEY is not set")
    days = max(1, min(31, int(days)))
    end = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = (end - timedelta(days=days)).replace(hour=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    starting_at, ending_at = start.strftime(fmt), end.strftime(fmt)

    import httpx2

    async with httpx2.AsyncClient(headers=_headers(key), timeout=30.0, transport=transport) as http:
        cost_buckets = await _get_all(
            http,
            "/v1/organizations/cost_report",
            [
                ("starting_at", starting_at),
                ("ending_at", ending_at),
                ("group_by[]", "description"),
                ("bucket_width", "1d"),
                ("limit", "31"),
            ],
        )
        usage_buckets = await _get_all(
            http,
            "/v1/organizations/usage_report/messages",
            [
                ("starting_at", starting_at),
                ("ending_at", ending_at),
                ("group_by[]", "model"),
                ("bucket_width", "1d"),
                ("limit", "31"),
            ],
        )

    cost_by_day: dict[str, float] = {}
    cost_by_model: dict[str, float] = {}
    total = 0.0
    for bucket in cost_buckets:
        day = str(bucket.get("starting_at", ""))[:10]
        for row in bucket.get("results") or []:
            usd = _cents_to_usd(row.get("amount"))
            model = str(row.get("model") or row.get("description") or "other")
            total += usd
            cost_by_day[day] = cost_by_day.get(day, 0.0) + usd
            cost_by_model[model] = cost_by_model.get(model, 0.0) + usd

    usage: list[DayUsage] = []
    for bucket in usage_buckets:
        day = str(bucket.get("starting_at", ""))[:10]
        for row in bucket.get("results") or []:
            cache_creation: Any = row.get("cache_creation")
            if isinstance(cache_creation, dict):
                # {"ephemeral_5m_input_tokens": n, "ephemeral_1h_input_tokens": n}
                cache_creation = sum(int(v or 0) for v in cache_creation.values())
            usage.append(
                DayUsage(
                    day=day,
                    model=str(row.get("model") or "unknown"),
                    uncached_input_tokens=int(row.get("uncached_input_tokens") or 0),
                    cache_read_tokens=int(row.get("cache_read_input_tokens") or 0),
                    cache_creation_tokens=int(cache_creation or 0),
                    output_tokens=int(row.get("output_tokens") or 0),
                )
            )

    return BillingSummary(
        starting_at=starting_at,
        ending_at=ending_at,
        total_usd=total,
        cost_by_day=cost_by_day,
        cost_by_model=cost_by_model,
        usage=usage,
    )


_cache: dict[int, BillingSummary] = {}


async def summary(*, days: int = 7, transport: Any = None) -> BillingSummary:
    """``fetch_summary`` with a one-minute cache per ``days`` window —
    the API's sustained polling allowance."""
    cached = _cache.get(days)
    if cached is not None and time.time() - cached.fetched_at < _CACHE_TTL_S:
        return cached
    result = await fetch_summary(days=days, transport=transport)
    _cache[days] = result
    return result


def clear_cache() -> None:
    _cache.clear()
