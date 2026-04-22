"""Swarm vs swarm — feature #7 (scaffold).

Full matchmaking needs a coordinator service (rendezvous, matchmaking,
score adjudication) that doesn't exist in-repo yet. This module ships
the *local* half of the contract so deploying a coordinator in front
of multiple Autonoma instances is possible.

Three endpoints:

  * ``POST /api/battle/invite`` — the current session owner publishes
    a challenge. Returns an invite token that can be shared.
  * ``POST /api/battle/accept``  — another instance redeems the token,
    indicating it will run the same harness task. Returns the task
    descriptor.
  * ``GET  /api/battle/{invite_id}/score`` — post-run, both sides
    compare outcome metadata (rounds used, final_answer, XP earned).

No WebSocket syncing yet — that's the matchmaker's job. What's here
lets two cooperating admins point their instances at each other and
agree on the same problem.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma.auth import User, require_active_user

router = APIRouter(tags=["battle"])


@dataclass
class _Invite:
    id: str
    owner_user_id: str
    task_goal: str
    max_rounds: int
    harness_preset_id: str | None
    created_at: float
    accepted_by: str | None = None
    owner_score: dict[str, Any] | None = None
    challenger_score: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# In-memory only. A coordinator service would persist these; for the
# PoC we treat invites as ephemeral — re-invite on restart.
_invites: dict[str, _Invite] = {}


@router.post("/api/battle/invite", status_code=http_status.HTTP_201_CREATED)
async def create_invite(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    goal = str(payload.get("task_goal") or "").strip()
    if not (1 <= len(goal) <= 500):
        raise HTTPException(400, detail={"code": "invalid_goal", "message": "task_goal 1-500자"})
    inv = _Invite(
        id=secrets.token_urlsafe(12),
        owner_user_id=user.id,
        task_goal=goal,
        max_rounds=int(payload.get("max_rounds") or 20),
        harness_preset_id=payload.get("harness_preset_id"),
        created_at=time.time(),
    )
    _invites[inv.id] = inv
    return {"invite": inv.to_dict()}


@router.post("/api/battle/accept")
async def accept_invite(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    invite_id = str(payload.get("invite_id") or "")
    inv = _invites.get(invite_id)
    if inv is None:
        raise HTTPException(404, detail={"code": "invite_not_found", "message": "해당 초대장을 찾을 수 없습니다."})
    if inv.accepted_by:
        raise HTTPException(409, detail={"code": "already_accepted", "message": "이미 수락된 초대장입니다."})
    if inv.owner_user_id == user.id:
        raise HTTPException(400, detail={"code": "cannot_self_accept", "message": "본인의 초대장은 수락할 수 없습니다."})
    inv.accepted_by = user.id
    return {
        "invite_id": inv.id,
        "task_goal": inv.task_goal,
        "max_rounds": inv.max_rounds,
        "harness_preset_id": inv.harness_preset_id,
    }


@router.post("/api/battle/{invite_id}/score")
async def submit_score(
    invite_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Each side POSTs its final metrics. Winner decided by (a)
    ``task_completed``, (b) fewer rounds, (c) higher team XP."""
    inv = _invites.get(invite_id)
    if inv is None:
        raise HTTPException(404, detail={"code": "invite_not_found"})
    score = {
        "task_completed": bool(payload.get("task_completed")),
        "rounds_used": int(payload.get("rounds_used") or 0),
        "team_xp": int(payload.get("team_xp") or 0),
        "final_answer": str(payload.get("final_answer") or "")[:2000],
    }
    if user.id == inv.owner_user_id:
        inv.owner_score = score
    elif user.id == inv.accepted_by:
        inv.challenger_score = score
    else:
        raise HTTPException(403, detail={"code": "not_a_participant"})

    resolved = _resolve_if_ready(inv)
    return {"invite_id": invite_id, "resolved": resolved}


@router.get("/api/battle/{invite_id}")
async def get_invite(
    invite_id: str,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    inv = _invites.get(invite_id)
    if inv is None:
        raise HTTPException(404, detail={"code": "invite_not_found"})
    return inv.to_dict()


def _resolve_if_ready(inv: _Invite) -> dict[str, Any] | None:
    if inv.owner_score is None or inv.challenger_score is None:
        return None
    a, b = inv.owner_score, inv.challenger_score
    # Tie-break: completed wins; then fewer rounds; then higher XP.
    def _key(s: dict[str, Any]) -> tuple[int, int, int]:
        return (
            1 if s["task_completed"] else 0,
            -int(s["rounds_used"]),
            int(s["team_xp"]),
        )
    if _key(a) > _key(b):
        winner = "owner"
    elif _key(b) > _key(a):
        winner = "challenger"
    else:
        winner = "draw"
    return {"winner": winner, "owner": a, "challenger": b}
