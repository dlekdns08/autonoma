"""Channel-points economy — FastAPI router (MVP).

Endpoints
─────────
::

    POST /api/points/heartbeat       body {session_id}              -> {balance, granted}
    GET  /api/points/balance                                        -> {balance}
    POST /api/points/spend/cookie    body {session_id, agent_name}  -> {balance, agent}

Auth: every endpoint requires an active cookie session
(:func:`require_active_user`) — guest cookies count, but a stranger off
the street with no cookie does not.

Spending the cookie drops a fortune cookie on the named agent's tile by
emitting the same ``fortune.given`` bus event the in-process
``/cookie`` debug command uses (see ``api.py`` around line 2713). The
ContextVar dance is so the event reaches *the session's room* rather
than fanning out to every viewer in the cluster.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from autonoma._session_owner import assert_session_owner_or_admin
from autonoma.auth import User, require_active_user
from autonoma.context import current_session_id as _current_session_id
from autonoma.event_bus import bus
from autonoma.points import (
    COOKIE_COST,
    InsufficientBalance,
    credit_heartbeat,
    get_balance,
    spend,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/points", tags=["points"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ── Request models ────────────────────────────────────────────────────


class HeartbeatBody(BaseModel):
    session_id: int = Field(..., description="live session id the viewer is watching")


class SpendCookieBody(BaseModel):
    session_id: int = Field(..., description="live session id")
    agent_name: str = Field(..., description="target agent (must exist in this session's swarm)")


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatBody,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Record a watch heartbeat. Grants 5 pts at most once per 60s.

    The ``session_id`` payload is validated against the cookie owner so
    a script can't farm points by spraying random session ids — only
    sessions you're actually watching count.
    """
    assert_session_owner_or_admin(body.session_id, user)
    balance, granted = await credit_heartbeat(user.id)
    return {"balance": balance, "granted": granted}


@router.get("/balance")
async def balance_endpoint(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the caller's current channel-points balance."""
    bal = await get_balance(user.id)
    return {"balance": bal}


@router.post("/spend/cookie")
async def spend_cookie(
    body: SpendCookieBody,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Spend 50 pts to drop a fortune cookie on ``agent_name``'s tile.

    Validation order:
      1. ``session_id`` must belong to the cookie session (or admin).
      2. The session must have a live swarm with ``agent_name`` in it.
      3. The viewer must have at least 50 pts. Insufficient balance
         returns ``402 Payment Required`` with code ``insufficient_balance``
         — the inline UI uses that exact code.

    On success the spend is committed *before* the bus emit so a failed
    fan-out still costs the viewer their points (idempotent reorder is
    much harder than a single missed cookie animation).
    """
    assert_session_owner_or_admin(body.session_id, user)

    # Late import to dodge the api ↔ routers cycle the rest of this
    # package threads with ``importlib`` in ``include_router`` callsites.
    from autonoma.api import _sessions

    session = _sessions.get(body.session_id)
    if session is None:
        raise _err(404, "session_not_found", "no live session with that id.")

    swarm = session.swarm
    if swarm is None or session.task is None or session.task.done():
        raise _err(409, "swarm_not_running", "swarm is not running for this session.")

    agent_name = (body.agent_name or "").strip()
    if not agent_name or agent_name not in swarm.agents:
        raise _err(404, "agent_not_found", f"no agent named '{agent_name}'.")

    try:
        new_balance = await spend(user.id, COOKIE_COST)
    except InsufficientBalance as exc:
        raise _err(402, exc.code, str(exc))

    # Emit the cookie-drop bus event under the session's ContextVar so
    # the ``fortune.given`` handler routes it to viewers in the right
    # room (same pattern as the WS ``/cookie`` debug command).
    token = _current_session_id.set(body.session_id)
    try:
        cookie = swarm.fortune_jar.give_cookie(agent_name, swarm._round)
        if cookie is None:
            # Agent already has an active cookie; refund the spend so
            # the viewer isn't punished for our duplicate-suppress.
            from autonoma.points import credit

            await credit(user.id, COOKIE_COST)
            raise _err(409, "agent_busy", f"{agent_name} already has a fortune cookie.")
        await bus.emit(
            "fortune.given",
            agent=agent_name,
            fortune=cookie.fortune,
        )
    finally:
        _current_session_id.reset(token)

    return {
        "status": "ok",
        "balance": new_balance,
        "agent": agent_name,
    }
