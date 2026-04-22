"""Vision Agent — multimodal screen/webcam observer.

The client captures a screenshot (e.g. ``navigator.mediaDevices.getDisplayMedia``
frame) or webcam still and POSTs it here with an optional context string.
The server calls a multimodal LLM ("what is the user doing?") and, when
the model finds something relevant, emits a proactive agent message.

Kept behind ``settings.vision_agent_enabled`` because multimodal calls
cost tokens — admins opt in.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi import status as http_status

from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vision"])


# ``{user_id: last_observation_monotonic}`` — per-user cooldown so the
# Vision Agent never spams a single workstation faster than configured.
_last_seen: dict[str, float] = {}


@router.post("/api/vision/observe")
async def vision_observe(
    frame: UploadFile = File(...),
    hint: str = Form(""),
    target_agent: str = Form(""),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Accept a screenshot / webcam frame and decide if an agent should
    speak up. Response mirrors what we decide::

        {
          "acted": true,
          "agent": "Midori",
          "message": "Your loop on line 42 will never terminate — mind if I take a look?",
          "reason": "detected infinite loop pattern in visible code"
        }

    or::

        {"acted": false, "reason": "cooldown"}
    """
    if not settings.vision_agent_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "vision_disabled", "message": "Vision Agent is disabled. Set AUTONOMA_VISION_AGENT_ENABLED=true."},
        )

    now = time.monotonic()
    last = _last_seen.get(user.id, 0.0)
    if now - last < settings.vision_agent_cooldown_s:
        return {"acted": False, "reason": "cooldown"}

    raw = await frame.read()
    if not raw:
        raise HTTPException(400, detail={"code": "empty_frame", "message": "frame is empty"})
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(413, detail={"code": "frame_too_large", "message": "frame must be <= 4 MB"})

    mime = frame.content_type or "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    reaction = await _decide_reaction(b64, mime, hint, target_agent)

    if not reaction["acted"]:
        return reaction

    _last_seen[user.id] = now
    await bus.emit(
        "vision.observed",
        user_id=user.id,
        agent=reaction.get("agent"),
        message=reaction.get("message"),
        hint=hint,
    )
    # Also emit as a regular agent message so the chat/VRM spotlight flows.
    await bus.emit(
        "agent.speech",
        agent=reaction.get("agent") or "Observer",
        text=reaction.get("message"),
        mood="curious",
    )
    return reaction


async def _decide_reaction(
    image_b64: str, mime: str, hint: str, target_agent: str
) -> dict[str, Any]:
    """Ask a multimodal model whether to say something.

    Uses whichever admin LLM is configured. Returns ``{acted: bool,
    agent, message, reason}``.
    """
    from autonoma.llm import LLMConfig, call_llm  # type: ignore[attr-defined]

    prompt = (
        "You are a Vision Agent in the Autonoma swarm. A user shared a "
        "screenshot or webcam frame of what they are currently doing. "
        "Decide whether an AI teammate should proactively speak up. "
        "Only act if you see something the user would genuinely benefit "
        "from: a visible bug, a stuck state, a question they wrote down, "
        "or a clear handoff opportunity. Respond STRICTLY as JSON:\n\n"
        "  {\"acted\": bool, \"agent\": \"Alice|Bear|...\", "
        "\"message\": \"<=2 sentences\", \"reason\": \"internal note\"}\n\n"
        f"User hint: {hint or '(none)'}\n"
        f"Preferred agent: {target_agent or '(any)'}"
    )
    try:
        # ``call_llm`` is expected to accept a list of content blocks when
        # doing multimodal. If this project's LLMConfig doesn't have a
        # multimodal path yet, fall back to a text-only heuristic — the
        # caller sees acted=false and the UI simply doesn't announce.
        cfg = LLMConfig.from_settings()
        raw = await call_llm(  # type: ignore[call-arg]
            cfg,
            [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        )
    except (TypeError, AttributeError, NotImplementedError) as exc:
        logger.info("[vision] multimodal path unavailable (%s) — skipping", exc)
        return {"acted": False, "reason": f"unsupported: {exc}"}
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("[vision] model call failed")
        return {"acted": False, "reason": f"error: {exc}"}

    import json
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            raise ValueError("non-dict response")
    except Exception:
        return {"acted": False, "reason": "unparseable model response"}
    parsed.setdefault("acted", False)
    return parsed
