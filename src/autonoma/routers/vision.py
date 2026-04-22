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
from autonoma.llm import (
    LLMAuthError,
    LLMConnectionError,
    LLMError,
    create_llm_client,
    llm_config_from_settings,
)

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


_VISION_SYSTEM = (
    "You are a Vision Agent in the Autonoma AI swarm. The user shares a "
    "screenshot or webcam frame of what they are currently doing. Decide "
    "whether an AI teammate should proactively speak up. ONLY act when "
    "you see something the user would genuinely benefit from: a visible "
    "bug, a stuck UI state, a question they wrote down, or a clear "
    "handoff opportunity. Otherwise stay silent (acted=false). Keep "
    "messages to <=2 sentences, friendly and helpful. Respond STRICTLY "
    "as a single JSON object and nothing else, matching:\n"
    "  {\"acted\": boolean, \"agent\": string, \"message\": string, "
    "\"reason\": string}"
)


def _build_multimodal_messages(
    provider: str, image_b64: str, mime: str, prompt_text: str
) -> list[dict[str, Any]]:
    """Shape an image + text turn the way each provider expects.

    Anthropic accepts ``{"type":"image","source":{"type":"base64",...}}``
    inside the user message's content array. OpenAI chat-completions use
    ``{"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}``.
    Kept in one place so the router doesn't branch on provider.
    """
    if provider == "anthropic":
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": image_b64,
                },
            },
            {"type": "text", "text": prompt_text},
        ]
    else:
        # OpenAI + vLLM (OpenAI-compatible) shape.
        content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_b64}"},
            },
            {"type": "text", "text": prompt_text},
        ]
    return [{"role": "user", "content": content}]


async def _decide_reaction(
    image_b64: str, mime: str, hint: str, target_agent: str
) -> dict[str, Any]:
    """Ask a multimodal model whether to say something.

    Uses whichever admin LLM is configured. Returns
    ``{acted, agent, message, reason}``. On any model / parse failure
    we return ``acted=false`` so the endpoint degrades gracefully — a
    missing image model should never surface as a user-visible error.
    """
    import json as _json

    try:
        cfg = llm_config_from_settings()
    except Exception as exc:  # pragma: no cover — bad settings
        return {"acted": False, "reason": f"no_llm_config: {exc}"}
    if not cfg.api_key and cfg.provider != "vllm":
        return {"acted": False, "reason": "no_api_key"}

    prompt_text = (
        f"User hint: {hint or '(none)'}\n"
        f"Preferred agent: {target_agent or '(any)'}\n\n"
        "Look at the attached image and decide."
    )
    messages = _build_multimodal_messages(cfg.provider, image_b64, mime, prompt_text)

    client = create_llm_client(cfg)
    try:
        # Capped output — this is a yes/no + 2-sentence message.
        response = await client.create(
            model=cfg.model,
            max_tokens=256,
            temperature=0.2,
            system=_VISION_SYSTEM,
            messages=messages,
        )
    except (LLMAuthError, LLMConnectionError) as exc:
        logger.warning("[vision] model unreachable: %s", exc)
        return {"acted": False, "reason": f"unreachable: {type(exc).__name__}"}
    except LLMError as exc:
        logger.warning("[vision] llm error: %s", exc)
        return {"acted": False, "reason": f"llm_error: {exc}"}
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("[vision] unexpected model failure")
        return {"acted": False, "reason": f"error: {exc}"}

    raw = (response.text or "").strip()
    if not raw:
        return {"acted": False, "reason": "empty_response"}
    # Strip common wrappers the model sometimes produces (```json ... ```).
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError:
        # Be forgiving — extract the outermost {...} block and retry.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = _json.loads(raw[start:end + 1])
            except _json.JSONDecodeError:
                return {"acted": False, "reason": "unparseable"}
        else:
            return {"acted": False, "reason": "unparseable"}
    if not isinstance(parsed, dict):
        return {"acted": False, "reason": "non_dict_response"}
    parsed.setdefault("acted", False)
    parsed.setdefault("agent", "")
    parsed.setdefault("message", "")
    parsed.setdefault("reason", "")
    # Normalize bool (models sometimes emit "true" as a string).
    if isinstance(parsed["acted"], str):
        parsed["acted"] = parsed["acted"].strip().lower() in ("true", "yes", "1")
    return parsed
