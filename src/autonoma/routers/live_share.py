"""Live-share directory + per-room share metadata (feature: live share).

Hosts opt their running room into the public ``/live`` directory by
calling ``POST /api/live-share/visibility {public: true}``. Once
public, the room appears in:

  * ``GET /api/live-share/sessions``    — directory grid for ``/live``
  * ``GET /api/live-share/sessions/{code}`` — single-room metadata for
    the share landing + Open-Graph preview.

All reads are unauthenticated (a share URL works without login).
Mutations require a cookie session AND room ownership — anyone else
trying to flip visibility is rejected with 403.

Storage is purely in-memory: the source of truth is
``autonoma.api._rooms``. We don't persist visibility because rooms
themselves are runtime state — once the host stops the swarm the room
disappears and so should its public flag.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from autonoma.auth import require_active_user
from autonoma.db.users import User
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live-share", tags=["live-share"])


# ── Directory-feed TTL cache (I9) ─────────────────────────────────────
# The ``GET /api/live-share/sessions`` endpoint is polled aggressively
# from the public ``/live`` directory page; rebuilding a card per room
# on every poll is O(N×M) over swarm internals. We cache the snapshot
# for a tiny window and invalidate proactively on the bus events that
# can mutate the directory shape.
_DIRECTORY_CACHE: dict[str, Any] = {"snapshot": [], "_room_count": 0}
_DIRECTORY_CACHE_TS: float = 0.0
_DIRECTORY_TTL_SEC: float = 2.0


def _invalidate_directory_cache() -> None:
    """Force the next ``_list_public_rooms`` call to rebuild from scratch."""
    global _DIRECTORY_CACHE_TS
    _DIRECTORY_CACHE_TS = 0.0


async def _on_visibility_changed(**_data: Any) -> None:
    _invalidate_directory_cache()


async def _on_swarm_ended(**_data: Any) -> None:
    _invalidate_directory_cache()


# Subscribe at module load so the cache stays consistent without each
# emitter knowing about the directory feed. The handlers are no-ops if
# the bus is reset (e.g. by tests); the length-signature fallback in
# ``_list_public_rooms`` guarantees correctness in that case.
bus.on("live_share.visibility_changed", _on_visibility_changed)
bus.on("swarm.ended", _on_swarm_ended)


# ── Request models ────────────────────────────────────────────────────


class VisibilityBody(BaseModel):
    public: bool
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=400)


# ── Helpers ───────────────────────────────────────────────────────────


def _build_card(room: Any) -> dict[str, Any]:
    """Project a ``RoomState`` into the directory-card payload.

    The shape is intentionally narrow — *no* WS internals leak into the
    public response — and tolerant: missing fields fall back to safe
    defaults so a half-initialised room (between ``start`` and the
    first round) still renders.
    """
    # Local import to avoid a circular import at module load time:
    # ``autonoma.api`` imports every router, including this one.
    from autonoma import api as _api

    project = getattr(room, "project", None)
    swarm = getattr(room, "swarm", None)
    agents_preview: list[dict[str, str]] = []
    if swarm is not None:
        try:
            for name, ag in list(swarm.agents.items())[:6]:
                emoji = getattr(getattr(ag, "persona", None), "emoji", "") or ""
                role = getattr(getattr(ag, "persona", None), "role", "") or ""
                mood = getattr(ag, "mood", None)
                mood_str = (
                    mood.value if mood is not None and hasattr(mood, "value") else str(mood or "")
                )
                agents_preview.append({
                    "name": str(name),
                    "emoji": str(emoji),
                    "role": str(role),
                    "mood": mood_str,
                })
        except Exception:
            # Swarm internals racing with us — better an empty preview
            # than a 500 on the directory page.
            agents_preview = []

    goal = ""
    round_number = 0
    agent_count = 0
    if project is not None:
        goal = str(getattr(project, "description", "") or getattr(project, "name", "") or "")
        round_number = int(getattr(project, "round", 0) or 0)
        agent_count = len(getattr(project, "agents", []) or [])

    viewers = _api._viewers_in_room(room.room_id)
    owner_id = getattr(room, "owner_session_id", 0)
    owner_user_id = ""
    owner_display = ""
    for v in viewers:
        if v.session_id == owner_id:
            owner_user_id = v.owner_user_id or ""
            owner_display = v.display_name or ""
            break

    title = (room.public_title or goal or "Live swarm").strip()
    description = (room.public_description or "").strip()

    return {
        "room_code": room.short_code,
        "room_id": room.room_id,
        "title": title[:120],
        "description": description[:400],
        "goal": goal[:240],
        "host_display_name": owner_display,
        "host_user_id": owner_user_id,
        "viewer_count": max(0, len(viewers) - 1),  # exclude the host
        "agent_count": agent_count,
        "round_number": round_number,
        "started_at": float(getattr(room, "started_at", 0.0) or 0.0),
        "agents": agents_preview,
        "is_public": bool(room.is_public),
    }


def _list_public_rooms() -> list[dict[str, Any]]:
    """Snapshot every room currently flagged ``is_public``.

    Sorted by viewer_count desc, then started_at desc — so the busiest
    + freshest live shows lead the grid. Stable enough for the polled
    directory page; the real-time delta channel is the bus events
    ``live_share.session_started`` / ``live_share.session_ended``.

    Backed by a 2-second TTL cache so a polled directory at 1 Hz only
    rebuilds the card list ~once. Cache is also invalidated by the bus
    handlers above and by a length-signature mismatch (a room dict
    cleared from under us, e.g. by a test fixture, must not return
    stale entries).
    """
    from autonoma import api as _api

    global _DIRECTORY_CACHE_TS
    now = time.monotonic()
    cached = _DIRECTORY_CACHE.get("snapshot")
    if (
        cached is not None
        and (now - _DIRECTORY_CACHE_TS) < _DIRECTORY_TTL_SEC
        and _DIRECTORY_CACHE.get("_room_count") == len(_api._rooms)
    ):
        return cached

    cards: list[dict[str, Any]] = []
    for room in _api._rooms.values():
        if not getattr(room, "is_public", False):
            continue
        try:
            cards.append(_build_card(room))
        except Exception:
            logger.exception("[live_share] dropping malformed room %s", room.room_id)

    cards.sort(key=lambda c: (-c["viewer_count"], -c["started_at"]))

    _DIRECTORY_CACHE["snapshot"] = cards
    _DIRECTORY_CACHE["_room_count"] = len(_api._rooms)  # type: ignore[assignment]
    _DIRECTORY_CACHE_TS = now
    return cards


# ── Public reads ──────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    """Public directory feed — anonymous, cache-busting safe."""
    rooms = _list_public_rooms()
    return {"count": len(rooms), "sessions": rooms}


@router.get("/sessions/{code}")
async def get_session(code: str) -> dict[str, Any]:
    """Single-room metadata for the share landing.

    404 if the code is unknown OR if the room is currently private.
    Both branches return the same response so an attacker can't
    distinguish "wrong code" from "room is private right now".
    """
    from autonoma import api as _api

    room_id = _api._short_codes.get(code)
    if room_id is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "room_not_found"},
        )
    room = _api._rooms.get(room_id)
    if room is None or not room.is_public:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "room_not_found"},
        )
    return {"session": _build_card(room)}


# ── Owner-gated mutation ──────────────────────────────────────────────


@router.post("/visibility")
async def set_visibility(
    body: VisibilityBody,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Flip the caller's owned room between public and private.

    The caller must (a) be cookie-session-authenticated, AND (b) own
    a room currently in ``_rooms``. We resolve the room by walking
    ``_sessions`` for the caller's user_id — the typical caller owns
    exactly one room at a time, so the first match wins. If the caller
    owns no live room, return 404; flipping visibility on a non-existent
    room is meaningless.

    Emits ``live_share.visibility_changed`` so the directory can
    invalidate any local cache without polling.
    """
    from autonoma import api as _api

    target_room: Any | None = None
    for sess in list(_api._sessions.values()):
        if sess.owner_user_id != user.id:
            continue
        room = _api._rooms.get(sess.room_id)
        if room is None:
            continue
        if room.owner_session_id != sess.session_id:
            continue
        target_room = room
        break

    if target_room is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "no_owned_room"},
        )

    target_room.is_public = bool(body.public)
    if body.public:
        # Title/description are intentionally only honoured when going
        # public — flipping back to private wipes them so a future
        # public flip starts fresh.
        target_room.public_title = body.title.strip()[:120]
        target_room.public_description = body.description.strip()[:400]
    else:
        target_room.public_title = ""
        target_room.public_description = ""

    await bus.emit(
        "live_share.visibility_changed",
        room_code=target_room.short_code,
        room_id=target_room.room_id,
        is_public=target_room.is_public,
    )
    return {"session": _build_card(target_room)}
