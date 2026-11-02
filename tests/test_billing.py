"""Billed usage/cost summary from the Admin usage & cost reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from autonoma import billing


def _transport(pages: dict[str, dict[str, Any]], *, status: int = 200) -> httpx2.MockTransport:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if status != 200:
            return httpx2.Response(status, json={"error": "nope"})
        path = request.url.path
        page = request.url.params.get("page")
        key = f"{path}|{page or ''}"
        body = pages[key]
        return httpx2.Response(200, json=body)

    transport = httpx2.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(billing.settings, "anthropic_admin_api_key", "sk-ant-admin01-test")
    billing.clear_cache()
    yield
    billing.clear_cache()


@pytest.mark.asyncio
async def test_summary_aggregates_cost_and_usage_across_pages() -> None:
    cost_p1 = {
        "data": [
            {
                "starting_at": "2026-10-30T00:00:00Z",
                "ending_at": "2026-10-31T00:00:00Z",
                "results": [
                    {
                        "amount": "123.45",
                        "currency": "USD",
                        "model": "claude-haiku-5-5",
                        "description": "Claude Haiku 5.5 Usage - Input",
                    },
                    {
                        "amount": "10",
                        "currency": "USD",
                        "model": "claude-sonnet-5-5",
                        "description": "Claude Sonnet 5.5 Usage - Output",
                    },
                ],
            }
        ],
        "has_more": True,
        "next_page": "page_2",
    }
    cost_p2 = {
        "data": [
            {
                "starting_at": "2026-10-31T00:00:00Z",
                "ending_at": "2026-11-01T00:00:00Z",
                "results": [
                    {"amount": "0.5", "currency": "USD", "description": "Web Search Usage"}
                ],
            }
        ],
        "has_more": False,
        "next_page": None,
    }
    usage = {
        "data": [
            {
                "starting_at": "2026-10-30T00:00:00Z",
                "ending_at": "2026-10-31T00:00:00Z",
                "results": [
                    {
                        "model": "claude-haiku-5-5",
                        "uncached_input_tokens": 1500,
                        "cache_read_input_tokens": 200,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 50,
                            "ephemeral_1h_input_tokens": 25,
                        },
                        "output_tokens": 500,
                    }
                ],
            },
            {
                "starting_at": "2026-10-31T00:00:00Z",
                "ending_at": "2026-11-01T00:00:00Z",
                "results": [],
            },
        ],
        "has_more": False,
        "next_page": None,
    }
    transport = _transport(
        {
            "/v1/organizations/cost_report|": cost_p1,
            "/v1/organizations/cost_report|page_2": cost_p2,
            "/v1/organizations/usage_report/messages|": usage,
        }
    )
    now = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    result = await billing.fetch_summary(days=2, transport=transport, now=now)

    assert result.total_usd == pytest.approx(1.2345 + 0.10 + 0.005)
    assert result.cost_by_day == {
        "2026-10-30": pytest.approx(1.3345),
        "2026-10-31": pytest.approx(0.005),
    }
    assert result.cost_by_model["claude-haiku-5-5"] == pytest.approx(1.2345)
    assert result.cost_by_model["Web Search Usage"] == pytest.approx(0.005)
    assert len(result.usage) == 1
    u = result.usage[0]
    assert (u.day, u.model, u.cache_creation_tokens, u.output_tokens) == (
        "2026-10-30",
        "claude-haiku-5-5",
        75,
        500,
    )
    # Window: two whole days up to the next hour boundary.
    assert result.starting_at == "2026-10-30T00:00:00Z"
    assert result.ending_at == "2026-11-01T06:00:00Z"

    # Admin headers on every call, and the cursor was followed.
    calls = transport.calls  # type: ignore[attr-defined]
    assert all(c.headers["x-api-key"] == "sk-ant-admin01-test" for c in calls)
    assert all(c.headers["anthropic-version"] == billing.API_VERSION for c in calls)
    assert any(c.url.params.get("page") == "page_2" for c in calls)
    assert result.to_dict()["total_usd"] == 1.3395


@pytest.mark.asyncio
async def test_summary_requires_admin_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(billing.settings, "anthropic_admin_api_key", "")
    with pytest.raises(billing.BillingUnavailable):
        await billing.fetch_summary(days=1)

    monkeypatch.setattr(billing.settings, "anthropic_admin_api_key", "sk-ant-api03-regular")
    with pytest.raises(billing.BillingUnavailable, match="refused"):
        await billing.fetch_summary(days=1, transport=_transport({}, status=403))


@pytest.mark.asyncio
async def test_summary_is_cached_for_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = {"n": 0}

    async def fake_fetch(**kwargs: Any) -> billing.BillingSummary:
        hits["n"] += 1
        return billing.BillingSummary("a", "b", 1.0, {}, {})

    monkeypatch.setattr(billing, "fetch_summary", fake_fetch)
    await billing.summary(days=7)
    await billing.summary(days=7)
    assert hits["n"] == 1
    await billing.summary(days=3)
    assert hits["n"] == 2


@pytest.mark.asyncio
async def test_router_maps_unavailable_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from autonoma.routers import billing as router

    async def unavailable(**kwargs: Any) -> billing.BillingSummary:
        raise billing.BillingUnavailable("no key")

    monkeypatch.setattr(billing, "summary", unavailable)
    with pytest.raises(HTTPException) as exc:
        await router.billing_summary(days=7, _admin=None)  # type: ignore[arg-type]
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "billing_unavailable"  # type: ignore[index]
