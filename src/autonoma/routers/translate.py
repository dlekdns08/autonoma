"""Live translation — Phase 4-B.

Single endpoint, LLM-backed::

    POST /api/translate
    {
      "text": "안녕하세요!",
      "from_lang": "ko",
      "to_lang": "en"
    }
    → {"text": "Hello!", "cached": false, "from_lang": "ko", "to_lang": "en"}

In-memory LRU keeps response time tight when subtitles flash repeats.
The LLM call is server-side so we don't have to ship a translation
model to the browser, and we route through the existing provider so
operators don't need a separate API key.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.llm import create_llm_client, llm_config_from_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["translate"])


# ── Cache ─────────────────────────────────────────────────────────────

# An LRU keyed by (from_lang, to_lang, sha1(text)). Capped so a busy
# stream can't OOM the process. 4096 entries × ~200 bytes per line
# tops out around 1 MB — acceptable.
_CACHE_CAP = 4096
_cache: "OrderedDict[tuple[str, str, str], str]" = OrderedDict()
_cache_lock = asyncio.Lock()


def _key(from_lang: str, to_lang: str, text: str) -> tuple[str, str, str]:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return (from_lang, to_lang, h)


async def _cache_get(key: tuple[str, str, str]) -> str | None:
    async with _cache_lock:
        v = _cache.get(key)
        if v is not None:
            _cache.move_to_end(key)
        return v


async def _cache_put(key: tuple[str, str, str], value: str) -> None:
    async with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_CAP:
            _cache.popitem(last=False)


# ── LLM call ──────────────────────────────────────────────────────────


_TRANSLATE_SYSTEM = (
    "You are a precise live-stream subtitle translator. "
    "Given a single short utterance, output ONLY the translation in "
    "the requested language — no commentary, no quotes, no explanation. "
    "Preserve emoji, names, and inline punctuation. Do not change line "
    "breaks. If the text is already in the target language, return it "
    "unchanged."
)


async def _translate_via_llm(text: str, from_lang: str, to_lang: str) -> str:
    config = llm_config_from_settings()
    client = create_llm_client(config)
    user_msg = (
        f"Translate from {from_lang} to {to_lang}. "
        f"Output only the translation.\n\n{text}"
    )
    response = await client.create(
        model=config.model,
        max_tokens=512,
        temperature=0.0,
        system=_TRANSLATE_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    out = (response.text or "").strip()
    # Defensive trim of accidental code-fence wrapping.
    if out.startswith("```") and out.endswith("```"):
        out = out.strip("`").strip()
    return out


# ── Endpoint ──────────────────────────────────────────────────────────


@router.post("/api/translate")
async def translate(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail={"code": "empty_text", "message": "text is required"},
        )
    if len(text) > 4000:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "text_too_long",
                "message": "text exceeds 4000 characters",
            },
        )
    from_lang = (payload.get("from_lang") or "auto").strip().lower()
    to_lang = (
        payload.get("to_lang")
        or settings.tts_default_language
        or "en"
    ).strip().lower()
    if from_lang == to_lang and from_lang != "auto":
        return {
            "text": text,
            "from_lang": from_lang,
            "to_lang": to_lang,
            "cached": False,
            "skipped": "same_language",
        }

    key = _key(from_lang, to_lang, text)
    cached = await _cache_get(key)
    if cached is not None:
        return {
            "text": cached,
            "from_lang": from_lang,
            "to_lang": to_lang,
            "cached": True,
        }

    try:
        translated = await _translate_via_llm(text, from_lang, to_lang)
    except Exception as exc:
        logger.warning(f"[translate] LLM failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail={"code": "translate_failed", "message": str(exc)},
        ) from exc

    if translated:
        await _cache_put(key, translated)

    return {
        "text": translated,
        "from_lang": from_lang,
        "to_lang": to_lang,
        "cached": False,
    }
