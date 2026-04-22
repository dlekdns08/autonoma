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
