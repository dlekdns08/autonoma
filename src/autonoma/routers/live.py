"""Live / broadcast feature router.

Consolidates three features that live on top of the streaming layer:

* **#1 Autonoma Live** — scheduled ON-AIR window + Twitch/YouTube chat
  bridge. Chat messages become ``human.feedback`` events, scheduled
  windows emit ``live.onair`` / ``live.offair`` on the bus.
* **#2 Donation → WorldEvent** — webhook receives a donation payload,
  fires a ``WorldEventType.DONATION_BLESSING`` (or an equivalent event
  already in the enum) so the world reacts visibly. Superchats can
  spawn quests, boss fights, fortunes.
* **Auto-clip trigger** — when the listed server events fire, emits
  ``live.clip`` with a suggested title. The /obs page keeps a rolling
  MediaRecorder buffer and saves a WebM on receipt.

Webhook auth: ``X-Autonoma-Signature`` must equal
``settings.live_webhook_secret``. We prefer a shared secret over full
Twitch/YouTube OAuth here — most deployments pair this with Streamer.bot
/ Aitum which sit in front and handle the OAuth themselves.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi import status as http_status

from autonoma.config import settings
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["live"])


# ── Auth ──────────────────────────────────────────────────────────────


def _verify_secret(signature: str | None) -> None:
    expected = settings.live_webhook_secret
    if not expected:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "live_disabled", "message": "Live webhook is disabled. Set AUTONOMA_LIVE_WEBHOOK_SECRET."},
        )
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail={"code": "bad_signature", "message": "Invalid X-Autonoma-Signature."},
        )


# ── #1a Chat bridge ──────────────────────────────────────────────────


@router.post("/api/live/chat")
async def live_chat_bridge(
    payload: dict[str, Any],
    x_autonoma_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Ingest a chat message from Twitch/YouTube/Discord.

    Shape::

        {
            "source": "twitch",
            "username": "viewer123",
            "text": "Alice fix the bug!",
            "superchat_amount_cents": 500,  # optional
            "currency": "USD"
        }

    Fires ``human.feedback`` on the bus so the director/swarm can react.
    Superchats route through to the donation handler below.
    """
    _verify_secret(x_autonoma_signature)
    source = str(payload.get("source") or "unknown")
    username = str(payload.get("username") or "viewer")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, detail={"code": "empty_text", "message": "text required"})

    await bus.emit(
        "human.feedback",
        origin="live_chat",
        chat_source=source,
        username=username,
        text=text,
    )
    superchat = payload.get("superchat_amount_cents")
    if superchat:
        await _fire_donation(
            amount_cents=int(superchat),
            currency=str(payload.get("currency") or "USD"),
            username=username,
            message=text,
            source=source,
        )
    return {"status": "ok"}


# ── #2 Donation → WorldEvent ─────────────────────────────────────────


@router.post("/api/live/donation")
async def live_donation(
    payload: dict[str, Any],
    x_autonoma_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Map an external donation event into a world event.

    Shape::

        {
            "amount_cents": 500,
            "currency": "USD",
            "username": "patron42",
            "message": "love the show",
            "source": "twitch|youtube|kofi|toss"
        }
    """
    _verify_secret(x_autonoma_signature)
    amount_cents = int(payload.get("amount_cents") or 0)
    if amount_cents <= 0:
        raise HTTPException(400, detail={"code": "invalid_amount", "message": "amount_cents must be > 0"})
    await _fire_donation(
        amount_cents=amount_cents,
        currency=str(payload.get("currency") or "USD"),
        username=str(payload.get("username") or "anon"),
        message=str(payload.get("message") or ""),
        source=str(payload.get("source") or "unknown"),
    )
    return {"status": "ok"}


async def _fire_donation(
    *, amount_cents: int, currency: str, username: str, message: str, source: str
) -> None:
    """Emit a donation event + map to world-scale blessing.

    Tier mapping (USD-ish, deployment can override client-side):
      * <$5   → ``fortune.given`` (random FortuneCookie)
      * $5-20 → ``quest.spawned`` (side mission)
      * >$20  → ``boss.spawned``  (co-op encounter)
    """
    await bus.emit(
        "donation.received",
        amount_cents=amount_cents,
        currency=currency,
        username=username,
        message=message,
        source=source,
    )
    # Map to a pre-existing world event so the UI/narrative layer
    # already knows how to render it.
    if amount_cents < 500:
        event_name = "fortune.given"
    elif amount_cents < 2000:
        event_name = "quest.spawned"
    else:
        event_name = "boss.spawned"
    await bus.emit(
        event_name,
        origin="donation",
        amount_cents=amount_cents,
        username=username,
        message=message,
    )


# ── #1b Auto-clip control ────────────────────────────────────────────

# Server-side list of events that auto-trigger a clip. Kept in code
# rather than config so adding a new milestone is a one-line PR.
AUTOCLIP_EVENTS: set[str] = {
    "boss.defeated",
    "boss.escaped",
    "achievement.earned",
    "quest.completed",
    "debate.resolved",
    "evolution.triggered",
}


def register_autoclip_hooks() -> None:
    """Attach bus handlers that re-emit a ``live.clip`` trigger.

    Called from ``api.py`` lifespan when ``settings.live_autoclip_enabled``
    is true. The /obs page subscribes to ``live.clip`` and slices its
    rolling MediaRecorder buffer.
    """
    if not settings.live_autoclip_enabled:
        return

    async def _on_event(*, event_name: str, **payload: Any) -> None:
        title = payload.get("title") or payload.get("name") or event_name
        await bus.emit(
            "live.clip",
            source_event=event_name,
            title=str(title),
            seconds_back=settings.live_autoclip_seconds,
            payload=payload,
        )

    for evt in AUTOCLIP_EVENTS:
        bus.on(evt, lambda _e=evt, **kw: _on_event(event_name=_e, **kw))


# ── #1c Schedule endpoint (manual on/off-air control) ────────────────


@router.post("/api/live/schedule")
async def live_schedule(
    payload: dict[str, Any],
    x_autonoma_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Mark the stream as on/off-air. The frontend tint + any other
    viewer-visible affordances key off the ``live.onair`` / ``live.offair``
    events this emits.

    External cron (deployment's scheduler of choice) POSTs this on
    schedule. Shape::

        {"on_air": true, "note": "Daily coding stream"}
    """
    _verify_secret(x_autonoma_signature)
    on_air = bool(payload.get("on_air"))
    note = str(payload.get("note") or "")
    await bus.emit(
        "live.onair" if on_air else "live.offair",
        note=note,
    )
    return {"status": "ok", "on_air": str(on_air).lower()}
