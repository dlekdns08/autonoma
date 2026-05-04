"""Coordinator REST API — feature #1 (2026-05).

A central matchmaker that two or more Autonoma instances dial into so
they can be paired against each other on a shared goal. Endpoints::

    POST /api/coordinator/register          — register an instance
    POST /api/coordinator/match/queue       — enqueue an invite
    POST /api/coordinator/match/{id}/score  — submit KPI dict
    GET  /api/coordinator/match/{id}        — fetch result if both submitted
    GET  /api/coordinator/leaderboard       — top-N by ELO

Authentication: every POST endpoint requires the ``X-Autonoma-Coord-Token``
header to match ``settings.coordinator_token`` (constant-time compare).
GETs are unauthenticated so a public scoreboard page can fetch them.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi import status as http_status

from autonoma.config import settings
from autonoma.coordinator.model import MatchInvite
from autonoma.coordinator.store import coordinator_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/coordinator", tags=["coordinator"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}
    )


def _require_token(x_autonoma_coord_token: Optional[str]) -> None:
    """Reject the request unless the shared secret matches.

    Uses ``hmac.compare_digest`` for constant-time comparison so a
    timing oracle can't be used to recover the token byte-by-byte.
    Refuses unconditionally when the server hasn't configured a token —
    the alternative ("anything matches an empty string") is the worst
    possible failure mode for a coordinator with write access.
    """
    expected = settings.coordinator_token or ""
    if not expected:
        raise _err(
            503,
            "coordinator_disabled",
            "coordinator_token is not configured on this server",
        )
    if not x_autonoma_coord_token or not hmac.compare_digest(
        x_autonoma_coord_token, expected
    ):
        raise _err(401, "invalid_token", "X-Autonoma-Coord-Token mismatch")


# ── routes ─────────────────────────────────────────────────────────


@router.post("/register", status_code=http_status.HTTP_201_CREATED)
async def register_instance(
    payload: dict[str, Any],
    x_autonoma_coord_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_token(x_autonoma_coord_token)
    payload = dict(payload or {})
    instance_id = str(payload.get("instance_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    endpoint = str(payload.get("endpoint") or "").strip()
    if not instance_id or not name or not endpoint:
        raise _err(
            400,
            "invalid_registration",
            "instance_id, name, endpoint are required",
        )
    await coordinator_store.register_instance(instance_id, name, endpoint)
    return {"registered": True, "instance_id": instance_id}


@router.post("/match/queue", status_code=http_status.HTTP_201_CREATED)
async def enqueue_match(
    payload: dict[str, Any],
    x_autonoma_coord_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_token(x_autonoma_coord_token)
    payload = dict(payload or {})
    # Don't let clients pre-fill paired fields.
    for clobber in ("match_id", "opponent_invite_id", "opponent_instance_id"):
        payload.pop(clobber, None)
    try:
        invite = MatchInvite.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise _err(400, "invalid_invite", str(exc)) from exc
    saved = await coordinator_store.enqueue_match(invite)
    # Try to pair right away so callers can see if their invite found
    # an opponent on this same call.
    pairs = await coordinator_store.pair_pending_matches()
    paired = next(
        ((a, b) for a, b in pairs if saved.id in (a.id, b.id)), None
    )
    response: dict[str, Any] = {"invite": saved.model_dump(mode="json")}
    if paired is not None:
        a, b = paired
        response["paired"] = True
        response["match_id"] = a.match_id
        response["opponents"] = {
            a.instance_id: a.model_dump(mode="json"),
            b.instance_id: b.model_dump(mode="json"),
        }
    else:
        response["paired"] = False
    return response


@router.post("/match/{match_id}/score")
async def submit_score(
    match_id: str,
    payload: dict[str, Any],
    x_autonoma_coord_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_token(x_autonoma_coord_token)
    payload = dict(payload or {})
    instance_id = str(payload.get("instance_id") or "").strip()
    kpi = payload.get("kpi") or {}
    if not instance_id:
        raise _err(400, "invalid_score", "instance_id is required")
    if not isinstance(kpi, dict):
        raise _err(400, "invalid_score", "kpi must be a JSON object")
    result = await coordinator_store.submit_score(match_id, instance_id, kpi)
    if result is None:
        return {"match_id": match_id, "resolved": False}
    return {
        "match_id": match_id,
        "resolved": True,
        "result": result.model_dump(mode="json"),
    }


@router.get("/match/{match_id}")
async def get_match(match_id: str) -> dict[str, Any]:
    """Fetch the resolved result; 202 if still waiting on a submission."""
    result = await coordinator_store.get_result(match_id)
    if result is None:
        # Use 202 Accepted to mean "we know about it, just not done yet".
        raise HTTPException(
            status_code=http_status.HTTP_202_ACCEPTED,
            detail={"code": "match_pending", "match_id": match_id},
        )
    return {"result": result.model_dump(mode="json")}


@router.get("/leaderboard")
async def get_leaderboard(limit: int = 50) -> dict[str, Any]:
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500
    entries = await coordinator_store.get_leaderboard(limit=limit)
    return {"entries": [e.model_dump(mode="json") for e in entries]}
