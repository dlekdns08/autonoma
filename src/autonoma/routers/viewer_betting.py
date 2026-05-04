"""Viewer betting — FastAPI router (Feature #4).

Endpoints (all under prefix ``/api/betting``)
─────────────────────────────────────────────
::

    POST /api/betting/markets                            — admin
    GET  /api/betting/markets?session_id=                — cookie session
    POST /api/betting/markets/{market_id}/bet?session_id — cookie session
    POST /api/betting/markets/{market_id}/lock?session_id — admin
    POST /api/betting/markets/{market_id}/resolve?session_id — admin
    GET  /api/betting/leaderboard?session_id=&limit=     — cookie session
    GET  /api/betting/balance?session_id=                — cookie session

Every endpoint short-circuits with ``HTTP 503 {"code":"betting_disabled"}``
when ``settings.viewer_betting_enabled`` is False so a misconfigured
deployment can never serve real bets.

The router is intentionally thin: validation and bus emits live in
:mod:`autonoma.viewer_betting`. Errors raised by that module are mapped
to HTTP status codes here:

  * ``ValueError("invalid_stake")``       → 422
  * ``ValueError("market_not_open")``     → 422
  * ``ValueError("market_already_exists")`` → 409
  * ``ValueError("already_bet")``         → 409
  * any other ``ValueError``              → 422 (with the raw code as
    the error ``code`` so the widget can branch on novel failures
    without a router change)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field

from autonoma._session_owner import assert_session_owner_or_admin
from autonoma.auth import User, require_active_user, require_admin
from autonoma.config import settings
from autonoma.viewer_betting import (
    balance,
    leaderboard,
    list_open_markets,
    lock_market,
    open_market,
    place_bet,
    resolve_market,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/betting", tags=["betting"])


# ── Helpers ───────────────────────────────────────────────────────────


def _err(status: int, code: str, message: str) -> HTTPException:
    """Standard structured-error helper, matches the rest of the routers."""
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}
    )


def _check_enabled() -> None:
    """Raise 503 when the feature flag is off.

    Called at the top of every endpoint so a stale frontend hitting a
    disabled deploy gets a clean structured error instead of a 404 or a
    surprise database write.
    """
    if not settings.viewer_betting_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "betting_disabled",
                "message": "Viewer betting is disabled by server config",
            },
        )


def _map_value_error(exc: ValueError) -> HTTPException:
    """Map a domain ``ValueError`` from the DB module to an HTTP error.

    The contract with ``viewer_betting.py`` is that the exception
    message is a *stable code* (``"already_bet"``, ``"invalid_stake"``
    etc.), not a free-form sentence. We pass it through as-is so the
    widget can show the right toast.
    """
    code = str(exc) or "betting_error"
    if code == "already_bet":
        return _err(409, code, "you already placed a bet on this market.")
    if code == "market_already_exists":
        return _err(409, code, "a market with that id already exists.")
    return _err(422, code, code.replace("_", " "))


# ── Request models ────────────────────────────────────────────────────


class OpenMarketBody(BaseModel):
    session_id: int = Field(..., description="live session id")
    market_id: str = Field(..., description="caller-supplied stable handle")
    question: str = Field(..., description="market question shown to viewers")
    closes_at_round: int = Field(
        0, description="round number at which betting auto-locks"
    )


class PlaceBetBody(BaseModel):
    option: str = Field(..., description="which side the viewer picked")
    stake: int = Field(..., description="play-money stake; must be 10|50|100")


class ResolveBody(BaseModel):
    winning_option: str = Field(..., description="the option that won")


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post(
    "/markets",
    status_code=http_status.HTTP_201_CREATED,
)
async def open_market_endpoint(
    body: OpenMarketBody,
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Open a new market. Admin/host only.

    Returns the freshly inserted row in the same shape that
    ``GET /api/betting/markets`` exposes, so the design board UI can
    render the new card without a follow-up GET.
    """
    _check_enabled()
    try:
        market = await open_market(
            session_id=body.session_id,
            market_id=body.market_id,
            question=body.question,
            closes_at_round=body.closes_at_round,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return {"status": "ok", "market": market.to_dict()}


@router.get("/markets")
async def list_markets_endpoint(
    session_id: int = Query(..., description="live session id"),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return every *open* market in the session, newest first.

    Locked and resolved markets are filtered out at the DB layer; the
    widget never displays them as bettable.
    """
    _check_enabled()
    # I2 fix: only the session's owner (or admin) may list markets.
    assert_session_owner_or_admin(session_id, user)
    markets = await list_open_markets(session_id)
    return {
        "session_id": session_id,
        "count": len(markets),
        "markets": [m.to_dict() for m in markets],
    }


@router.post("/markets/{market_id}/bet")
async def place_bet_endpoint(
    market_id: str,
    body: PlaceBetBody,
    session_id: int = Query(..., description="live session id"),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Place a single viewer bet on ``market_id``.

    The viewer identity is taken from the authenticated cookie
    session — the caller never gets to forge a ``viewer_id``. Display
    name is the user's username so the leaderboard renders something
    human-readable without an extra join.
    """
    _check_enabled()
    # I2 fix: bettors must belong to the session whose market they're
    # touching — stops cross-session bet manipulation.
    assert_session_owner_or_admin(session_id, user)
    try:
        entry = await place_bet(
            session_id=session_id,
            market_id=market_id,
            viewer_id=user.id,
            display_name=user.username,
            option=body.option,
            stake=body.stake,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return {"status": "ok", "entry": entry.to_dict()}


@router.post("/markets/{market_id}/lock")
async def lock_market_endpoint(
    market_id: str,
    session_id: int = Query(..., description="live session id"),
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Flip the market to ``locked`` so no further bets land.

    Idempotent at the HTTP layer: a re-lock returns
    ``{"status": "noop"}`` rather than raising, so the host's "lock"
    button can be tapped twice without an error toast.
    """
    _check_enabled()
    locked = await lock_market(session_id, market_id)
    if not locked:
        return {
            "status": "noop",
            "market_id": market_id,
            "session_id": session_id,
        }
    return {
        "status": "ok",
        "market_id": market_id,
        "session_id": session_id,
    }


@router.post("/markets/{market_id}/resolve")
async def resolve_market_endpoint(
    market_id: str,
    body: ResolveBody,
    session_id: int = Query(..., description="live session id"),
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Settle the market on ``winning_option`` and return the summary.

    The summary dict comes straight from
    :func:`autonoma.viewer_betting.resolve_market` so the wire format
    matches the bus event payload — observers and HTTP callers see the
    same numbers.
    """
    _check_enabled()
    summary = await resolve_market(
        session_id=session_id,
        market_id=market_id,
        winning_option=body.winning_option,
    )
    return {"status": "ok", "summary": summary}


@router.get("/leaderboard")
async def leaderboard_endpoint(
    session_id: int = Query(..., description="live session id"),
    limit: int = Query(20, description="max rows to return; capped at 200"),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Top bettors by net play-money winnings in the session."""
    _check_enabled()
    # I2 fix: only the session's owner (or admin) may view its leaderboard.
    assert_session_owner_or_admin(session_id, user)
    rows = await leaderboard(session_id=session_id, limit=limit)
    return {
        "session_id": session_id,
        "count": len(rows),
        "leaderboard": rows,
    }


@router.get("/balance")
async def balance_endpoint(
    session_id: int = Query(..., description="live session id"),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the calling viewer's current play-money balance.

    The viewer is always the cookie-session user; we don't accept a
    ``viewer_id`` query param so a malicious frontend can't snoop on
    someone else's balance.
    """
    _check_enabled()
    bal = await balance(session_id=session_id, viewer_id=user.id)
    return {"viewer_id": user.id, "balance": bal}
