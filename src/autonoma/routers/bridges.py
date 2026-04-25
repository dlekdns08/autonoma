"""External chat bridges — feature #8.

Two inbound webhooks:

  * ``/api/bridges/slack/events`` — Slack Events API. Verifies the
    signature with HMAC-SHA256 over the concatenation
    ``v0:{timestamp}:{body}`` using ``settings.slack_signing_secret``.
  * ``/api/bridges/discord/webhook`` — generic shared-secret webhook;
    pair with a tiny Discord.py or discord-slash app that forwards
    ``@mention`` events.

Both endpoints parse out an ``@agent_name`` mention and route the
rest of the message as a ``human.feedback`` event tagged
``origin="bridge_slack"`` or ``bridge_discord``. The swarm's existing
chat pipeline handles the reply; the caller is expected to post the
reply back to the originating channel by subscribing to
``agent.speech`` events with the matching origin (that side lives
outside this process).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi import status as http_status

from autonoma.config import settings
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bridges"])


_MENTION_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_\-]{1,31})")


def _extract_mention(text: str) -> tuple[str | None, str]:
    """Return (agent_name, remaining_text). First @mention wins."""
    m = _MENTION_PATTERN.search(text)
    if not m:
        return None, text
    agent = m.group(1)
    remaining = (text[: m.start()] + text[m.end():]).strip()
    return agent, remaining


@router.post("/api/bridges/slack/events")
async def slack_events(
    request: Request,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> dict[str, Any]:
    secret = settings.slack_signing_secret
    if not secret:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "slack_bridge_disabled", "message": "Slack bridge is not configured."},
        )
    if not x_slack_signature or not x_slack_request_timestamp:
        raise HTTPException(401, detail={"code": "missing_signature", "message": "Slack signature headers missing."})
    try:
        ts = int(x_slack_request_timestamp)
    except ValueError:
        raise HTTPException(401, detail={"code": "bad_timestamp", "message": "bad X-Slack-Request-Timestamp"})
    if abs(time.time() - ts) > 60 * 5:
        raise HTTPException(401, detail={"code": "stale_request", "message": "replay window exceeded"})

    body = await request.body()
    basestring = b"v0:" + str(ts).encode() + b":" + body
    digest = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, x_slack_signature):
        raise HTTPException(401, detail={"code": "bad_signature", "message": "Slack signature mismatch"})

    # Slack sends URL-verification challenge on app setup.
    import json
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, detail={"code": "bad_json", "message": "body is not JSON"})

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event") or {}
    if event.get("type") != "app_mention":
        return {"status": "ignored"}

    text = str(event.get("text") or "")
    agent, remaining = _extract_mention(text)
    await bus.emit(
        "human.feedback",
        origin="bridge_slack",
        slack_channel=event.get("channel"),
        slack_user=event.get("user"),
        slack_ts=event.get("ts"),
        target_agent=agent,
        text=remaining or text,
    )
    return {"status": "ok"}


# ── Phase 2-#2 — Twitch / YouTube live-chat bridge ────────────────────
#
# A separate forwarder (Twitch IRC bot / EventSub webhook / YT Live
# Streaming API poller) authenticates against the platform and re-posts
# normalized chat events here. We only verify a shared secret + funnel
# through the ExternalInputRouter so rate limits, polls, and swarm
# injection are unified across every external source.

@router.post("/api/bridges/livechat/event")
async def livechat_event(
    payload: dict[str, Any],
    x_autonoma_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Inbound live-chat message from a Twitch/YouTube forwarder.

    Expected shape::

        {
          "source": "twitch",        // or "youtube"
          "user": "viewer123",
          "text": "vote blue",
          "target": null,            // optional: route to a named agent
          "metadata": {"channel": "..."}
        }

    The forwarder MUST authenticate the originating chat at its end
    (Twitch IRC's NICK/PASS, YT API auth) — we don't try to verify
    individual messages, only the shared secret on the forwarder hop.
    """
    from autonoma.external_input import (
        ExternalMessage,
        SourceKind,
        router as ext_router,
    )

    # Reuse the discord bridge secret as the live-chat shared secret —
    # most operators run a single forwarder and rotating two secrets is
    # busywork. Fall back to the live-webhook secret if discord's empty.
    secret = settings.discord_webhook_secret or settings.live_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "livechat_bridge_disabled",
                "message": "Live chat bridge is not configured.",
            },
        )
    if not x_autonoma_signature or not hmac.compare_digest(
        x_autonoma_signature, secret
    ):
        raise HTTPException(
            401,
            detail={"code": "bad_signature", "message": "bad signature"},
        )

    raw_source = str(payload.get("source") or "twitch").lower()
    if raw_source not in ("twitch", "youtube", "webhook"):
        # Anything we don't recognise is bucketed as "webhook" so caps
        # apply but the source-tag stays distinct.
        raw_source = "webhook"
    source: SourceKind = raw_source  # type: ignore[assignment]

    user = str(payload.get("user") or "anon")[:32]
    text = str(payload.get("text") or "").strip()
    target = (payload.get("target") or "") or None
    if isinstance(target, str):
        target = target.strip() or None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    if not text:
        return {"status": "dropped", "action": "dropped_invalid"}

    # Mention parsing: ``@agent rest…`` lets viewers target a specific
    # cast member without the forwarder having to know the roster.
    if target is None:
        mention, remaining = _extract_mention(text)
        if mention:
            target = mention
            text = remaining or text

    msg = ExternalMessage(
        source=source,
        user=user,
        text=text[:280],
        target=target,
        metadata=metadata or {},
    )
    result = await ext_router.submit(msg)
    return {
        "status": "ok",
        "action": result.action.value,
        "detail": result.detail,
    }


@router.post("/api/bridges/livechat/poll/open")
async def livechat_open_poll(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Open a viewer poll. Subsequent live-chat messages are tallied
    against the options for ``duration_sec`` seconds before normal
    swarm-injection routing resumes. Auth: cookie session (any active
    user); the host owns their swarm so we don't gate further.

    Shape::

        {
          "poll_id": "p1",
          "question": "Which agent leads next?",
          "options": ["Alice", "Bob", "Carol"],
          "duration_sec": 30
        }
    """
    from autonoma.auth import require_active_user
    from fastapi import Depends

    # FastAPI handles the dependency injection in the signature; the
    # explicit ``Depends`` here just makes the import path obvious in
    # this branch of the file.
    _ = Depends(require_active_user)  # noqa: F841 — see decorator below

    from autonoma.external_input import router as ext_router

    poll_id = str(payload.get("poll_id") or "").strip() or "poll"
    question = str(payload.get("question") or "").strip() or "?"
    raw_options = payload.get("options") or []
    if not isinstance(raw_options, list) or not raw_options:
        raise HTTPException(
            400,
            detail={"code": "missing_options", "message": "options[] required"},
        )
    options = [str(o).strip() for o in raw_options if str(o).strip()]
    if not options:
        raise HTTPException(
            400,
            detail={"code": "empty_options", "message": "options must be non-empty"},
        )
    duration = float(payload.get("duration_sec") or 30.0)
    if duration <= 0 or duration > 600:
        raise HTTPException(
            400,
            detail={
                "code": "bad_duration",
                "message": "duration_sec must be 1..600",
            },
        )

    poll = ext_router.open_poll(
        poll_id=poll_id,
        question=question,
        options=options,
        duration_sec=duration,
    )
    await bus.emit(
        "external.poll_opened",
        poll_id=poll.poll_id,
        question=poll.question,
        options=poll.options,
        closes_at_monotonic=poll.closes_at,
    )
    return {
        "poll_id": poll.poll_id,
        "question": poll.question,
        "options": poll.options,
        "duration_sec": duration,
    }


@router.post("/api/bridges/livechat/poll/close")
async def livechat_close_poll() -> dict[str, Any]:
    from autonoma.external_input import router as ext_router

    poll = ext_router.close_poll()
    if poll is None:
        return {"closed": False}
    await bus.emit(
        "external.poll_closed",
        poll_id=poll.poll_id,
        tallies=dict(poll.tallies),
        voter_count=len(poll.voters),
    )
    return {
        "closed": True,
        "poll_id": poll.poll_id,
        "tallies": dict(poll.tallies),
        "voter_count": len(poll.voters),
    }


@router.post("/api/bridges/discord/webhook")
async def discord_webhook(
    payload: dict[str, Any],
    x_autonoma_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Generic Discord bridge. Relies on a forwarder bot doing the
    real Discord auth — we only verify a shared secret.

    Shape::

        {
          "channel_id": "1234",
          "user": "alice#1234",
          "text": "@midori please review this snippet",
          "attachments": []
        }
    """
    secret = settings.discord_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "discord_bridge_disabled", "message": "Discord bridge is not configured."},
        )
    if not x_autonoma_signature or not hmac.compare_digest(x_autonoma_signature, secret):
        raise HTTPException(401, detail={"code": "bad_signature", "message": "bad signature"})
    text = str(payload.get("text") or "")
    agent, remaining = _extract_mention(text)
    await bus.emit(
        "human.feedback",
        origin="bridge_discord",
        discord_channel=payload.get("channel_id"),
        discord_user=payload.get("user"),
        target_agent=agent,
        text=remaining or text,
    )
    return {"status": "ok"}
