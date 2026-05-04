"""KSL fingerspelling fallback router — feature #17.

Companion to :mod:`autonoma.routers.sign`. Where ``sign`` does
phrase-book lookup against operator-authored clips, this router
synthesises a per-jamo pose plan for **arbitrary** Korean text using
the Hangul block decomposition implemented in
:mod:`autonoma.voice.fingerspell`.

The endpoint is intentionally a thin HTTP wrapper around
:func:`fingerspell_plan` — the heavy lifting (jamo decomposition,
pose binding) lives in the voice module so it's directly importable
from agents / tests / cli without standing up FastAPI.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from autonoma.auth import User, require_active_user
from autonoma.voice.fingerspell import fingerspell_plan

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sign"])

# Same upper bound as ``/api/sign/translate`` — 2000 chars is plenty
# for a sentence and keeps the per-request plan size predictable
# (each Hangul syllable expands to at most 3 frames, so worst case
# is ~6kB JSON).
_MAX_TEXT_LEN = 2000

# Frontend pose-player crossfade caps — the artist asked for ≤2s holds
# so the playback doesn't feel like a freeze-frame slideshow even if a
# caller passes a huge value by mistake.
_MAX_HOLD_MS = 2000
_MAX_TRANSITION_MS = 2000


@router.post("/api/sign/fingerspell")
async def fingerspell_text(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Decompose ``text`` into jamo and return a frame plan.

    Request::

        {
          "text": "안녕",
          "hold_ms": 320,        # optional, default 320, clamped 0..2000
          "transition_ms": 120,  # optional, default 120, clamped 0..2000
        }

    Response::

        {
          "text": "안녕",
          "frames": [
            {"kind": "jamo", "pose": "ksl-finger-ㅇ", "jamo": "ㅇ",
             "surface": "안", "hold_ms": 320, "transition_ms": 120},
            ...
          ],
          "frame_count": 5
        }
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(
            400,
            detail={"code": "empty_text", "message": "text가 비어 있습니다."},
        )
    if len(text) > _MAX_TEXT_LEN:
        raise HTTPException(
            400,
            detail={
                "code": "text_too_long",
                "message": f"text는 {_MAX_TEXT_LEN}자 이하여야 합니다.",
            },
        )

    raw_hold = payload.get("hold_ms", 320)
    raw_trans = payload.get("transition_ms", 120)
    try:
        hold_ms = int(raw_hold)
        transition_ms = int(raw_trans)
    except (TypeError, ValueError):
        raise HTTPException(
            400,
            detail={
                "code": "invalid_timing",
                "message": "hold_ms / transition_ms는 정수여야 합니다.",
            },
        )
    # Clamp to a sane range. ``fingerspell_plan`` already clamps the
    # lower bound but we also enforce an upper bound here so a single
    # request can't stall the pose-player for tens of seconds.
    hold_ms = max(0, min(_MAX_HOLD_MS, hold_ms))
    transition_ms = max(0, min(_MAX_TRANSITION_MS, transition_ms))

    frames = fingerspell_plan(text, hold_ms=hold_ms, transition_ms=transition_ms)
    return {
        "text": text,
        "frames": frames,
        "frame_count": len(frames),
    }
