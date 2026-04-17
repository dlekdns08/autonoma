"""Dynamic model discovery for each supported provider.

Each provider exposes a ``list models`` endpoint that we call at runtime so
the frontend dropdown reflects whatever the upstream actually supports,
without us having to hardcode and then chase new model IDs.

Results are cached for ``_TTL_SECONDS`` per (provider, api_key/base_url)
tuple so that repeated UI polls don't hammer the upstream API.

If the remote call fails, we fall back to a small curated list of
known-good IDs so the UI still offers sensible defaults.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Provider = Literal["anthropic", "openai", "vllm"]


@dataclass(frozen=True)
class ModelInfo:
    value: str        # API model ID
    label: str        # Human-readable label for the dropdown

    def as_dict(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label}


# ── Fallback catalogs (used when the remote list call fails) ──────────────
# Ordered newest → oldest; the first entry becomes the default pick.

_FALLBACK: dict[Provider, list[ModelInfo]] = {
    "anthropic": [
        ModelInfo("claude-opus-4-7",            "Claude Opus 4.7"),
        ModelInfo("claude-sonnet-4-6",          "Claude Sonnet 4.6"),
        ModelInfo("claude-haiku-4-5-20251001",  "Claude Haiku 4.5"),
    ],
    "openai": [
        ModelInfo("gpt-4o",       "GPT-4o"),
        ModelInfo("gpt-4o-mini",  "GPT-4o mini"),
        ModelInfo("o1",           "o1"),
        ModelInfo("o1-mini",      "o1-mini"),
    ],
    "vllm": [],
}


# ── Response cache ────────────────────────────────────────────────────────

_TTL_SECONDS = 300  # 5 min
_cache: dict[tuple[str, str], tuple[float, list[ModelInfo]]] = {}


def _cache_get(key: tuple[str, str]) -> list[ModelInfo] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, models = entry
    if time.time() - ts > _TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return models


def _cache_set(key: tuple[str, str], models: list[ModelInfo]) -> None:
    _cache[key] = (time.time(), models)


# ── Label helpers ─────────────────────────────────────────────────────────


def _anthropic_label(model_id: str, display_name: str | None) -> str:
    # Prefer API-supplied display_name; fall back to a pretty version of the ID.
    if display_name:
        return display_name
    pretty = model_id.replace("claude-", "Claude ").replace("-", " ").title()
    return pretty


def _newest_first(models: list[ModelInfo], *, key_order: list[str]) -> list[ModelInfo]:
    """Sort so that newer family/version identifiers surface first.

    We bias toward IDs that contain ``opus``/``sonnet``/``haiku`` then by the
    family version number embedded in the string (e.g. ``4-7`` > ``4-6``).
    """
    def sort_key(m: ModelInfo) -> tuple[int, int, str]:
        family_rank = next(
            (i for i, k in enumerate(key_order) if k in m.value),
            len(key_order),
        )
        # extract trailing numeric version like "4-7" → 47
        digits = [c for c in m.value if c.isdigit()]
        version = -int("".join(digits)) if digits else 0
        return (family_rank, version, m.value)

    return sorted(models, key=sort_key)


# ── Provider-specific fetchers ────────────────────────────────────────────


def _list_anthropic(api_key: str) -> list[ModelInfo]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    page = client.models.list(limit=100)
    items: list[ModelInfo] = []
    for m in page.data:
        model_id = getattr(m, "id", None)
        if not model_id:
            continue
        display_name = getattr(m, "display_name", None)
        items.append(ModelInfo(model_id, _anthropic_label(model_id, display_name)))

    return _newest_first(items, key_order=["opus", "sonnet", "haiku"])


def _list_openai(api_key: str, base_url: str = "") -> list[ModelInfo]:
    from openai import OpenAI

    kwargs: dict = {"api_key": api_key or "dummy"}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    page = client.models.list()
    items: list[ModelInfo] = []
    for m in page.data:
        model_id = getattr(m, "id", None)
        if not model_id:
            continue
        # The /v1/models endpoint lists a lot of non-chat models (embeddings,
        # tts, whisper, moderation). Filter to chat-ish families; vLLM only
        # exposes the served model so the filter is effectively a no-op there.
        if base_url == "" and not _looks_like_chat_model(model_id):
            continue
        items.append(ModelInfo(model_id, model_id))

    items.sort(key=lambda m: m.value)
    return items


def _looks_like_chat_model(model_id: str) -> bool:
    lower = model_id.lower()
    if any(bad in lower for bad in ("embedding", "whisper", "tts", "dall-e", "moderation", "audio")):
        return False
    return any(good in lower for good in ("gpt", "o1", "o3", "chat"))


# ── Public entrypoint ─────────────────────────────────────────────────────


def list_models(
    provider: Provider,
    api_key: str = "",
    base_url: str = "",
) -> tuple[list[dict[str, str]], bool]:
    """Return ``(models, is_live)`` for a given provider.

    ``is_live`` is True when the list came from the upstream API and False
    when we had to fall back to the hardcoded catalog.
    """
    cache_key = (provider, api_key or base_url or "_anon")
    cached = _cache_get(cache_key)
    if cached is not None:
        return [m.as_dict() for m in cached], True

    try:
        if provider == "anthropic":
            if not api_key:
                raise ValueError("anthropic api key required")
            models = _list_anthropic(api_key)
        elif provider == "openai":
            if not api_key:
                raise ValueError("openai api key required")
            models = _list_openai(api_key)
        elif provider == "vllm":
            if not base_url:
                raise ValueError("vllm base_url required")
            models = _list_openai(api_key or "dummy", base_url=base_url)
        else:
            raise ValueError(f"unknown provider: {provider!r}")
    except Exception as exc:
        logger.warning(f"model discovery failed for {provider}: {exc}")
        fallback = _FALLBACK.get(provider, [])
        return [m.as_dict() for m in fallback], False

    if not models:
        fallback = _FALLBACK.get(provider, [])
        return [m.as_dict() for m in fallback], False

    _cache_set(cache_key, models)
    return [m.as_dict() for m in models], True
