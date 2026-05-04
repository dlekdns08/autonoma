"""Live quest designer — FastAPI router (feature #14).

Endpoints
─────────
::

    POST /api/quests/propose           — any cookie session
    POST /api/quests/{id}/vote         — any cookie session, deduped per user
    GET  /api/quests                   — any cookie session
    POST /api/quests/{id}/activate     — admin/host only
    POST /api/quests/{id}/complete     — admin/host only

The vote dedup set lives in this module's process memory (see
:data:`_voted_pairs`). It is intentionally not persisted: a viewer who
reloads the page should still be one-vote-per-quest for the lifetime
of the proposal, but once the operator activates a winner we expect
the next round's proposals to be a fresh slate. Activation therefore
purges every dedup entry whose ``quest_id`` matches the activated
quest, freeing voters to spend their next vote on a different card
without any client-side bookkeeping.

Routing-only logic lives here; persistence and bus emits go through
``autonoma.quests``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field

from autonoma.auth import User, require_active_user, require_admin
from autonoma.quests import (
    QuestTextEmpty,
    QuestTextTooLong,
    activate_top_quest,
    complete_quest,
    get_quest,
    list_quests,
    propose_quest,
    vote_quest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quests"])


# ── Vote dedup (per-process, in-memory) ───────────────────────────────
# Each entry is a (user_id, quest_id) tuple. Cleared per quest on
# activation so the next proposal pool starts fresh — see
# ``_clear_votes_for``. We keep the structure private to this module
# because it's an HTTP-layer concern; the underlying
# ``autonoma.quests.vote_quest`` is allowed to be called by anyone.

_voted_pairs: set[tuple[str, int]] = set()


def _clear_votes_for(quest_id: int) -> None:
    """Drop every dedup entry tied to ``quest_id``.

    Called after a successful activation so voters who supported the
    winning card aren't penalized when they want to vote on a brand
    new proposal in the next pool. The other proposals in the same
    pool remain "spent" — that's deliberate: a viewer who already
    voted for "build a healer" shouldn't get a free re-vote on
    "summon a dragon" just because a third quest activated.

    Implementation detail: rebuilding the set is O(n) where n is the
    total number of votes cast in this process's lifetime. For our
    expected scale (live stream with a few dozen viewers and a handful
    of quests per session) that's negligible; if it ever becomes hot,
    swap to a ``dict[int, set[str]]`` index by quest_id.
    """
    global _voted_pairs
    _voted_pairs = {(uid, qid) for (uid, qid) in _voted_pairs if qid != quest_id}


def _err(status: int, code: str, message: str) -> HTTPException:
    """Standard structured-error helper, matches the rest of the routers."""
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}
    )


# ── Request models ────────────────────────────────────────────────────


class ProposeBody(BaseModel):
    session_id: int = Field(..., description="live session id")
    text: str = Field(..., description="quest card body, <=256 chars")


class CompleteBody(BaseModel):
    """``round_number`` is optional from the wire; the operator can
    pass the current round explicitly or let the server default to 0
    (useful when completing leftover cards at session teardown)."""
    round_number: int = 0


class ActivateBody(BaseModel):
    """``round_number`` defaults to 0 if the host doesn't pass one —
    that mirrors the completion endpoint's permissive default."""
    round_number: int = 0


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/api/quests/propose", status_code=http_status.HTTP_201_CREATED)
async def propose(
    body: ProposeBody,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Submit a new quest card.

    Any active cookie session — including guests — can propose. We
    reject empty bodies and overlong text up front; the underlying
    insert is forgiving about everything else (timestamps and the
    ``proposed`` status come from server defaults).
    """
    try:
        quest_id = await propose_quest(body.session_id, body.text)
    except QuestTextEmpty as exc:
        raise _err(400, exc.code, str(exc))
    except QuestTextTooLong as exc:
        raise _err(400, exc.code, str(exc))
    return {
        "status": "ok",
        "quest_id": quest_id,
        "session_id": body.session_id,
        "proposed_by": user.id,
    }


@router.post("/api/quests/{quest_id}/vote")
async def vote(
    quest_id: int,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Cast a single vote on ``quest_id``.

    The (user_id, quest_id) pair is added to the per-process dedup
    set; a second vote from the same cookie session returns 409 with
    ``code=already_voted``. The atomic counter increment lives in
    ``autonoma.quests.vote_quest`` so racing votes from different
    viewers can't collide.
    """
    record = await get_quest(quest_id)
    if record is None:
        raise _err(404, "quest_not_found", "no quest with that id.")

    key = (user.id, int(quest_id))
    if key in _voted_pairs:
        raise _err(409, "already_voted", "you already voted on this quest.")

    new_total = await vote_quest(quest_id)
    if new_total == 0:
        # Race: row vanished between fetch and increment. Treat as 404
        # rather than silently advancing the dedup set.
        raise _err(404, "quest_not_found", "no quest with that id.")

    _voted_pairs.add(key)
    return {
        "status": "ok",
        "quest_id": int(quest_id),
        "votes": new_total,
        "voter": user.id,
    }


@router.get("/api/quests")
async def list_for_session(
    session_id: int = Query(..., description="live session id"),
    status: str | None = Query(
        default=None,
        description="filter by lifecycle: proposed|active|completed|skipped",
    ),
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return every quest for ``session_id``, sorted by votes desc.

    The optional ``status`` filter is a straight string match against
    the column; we don't enumerate it server-side so callers can pass
    custom states a future migration may add without needing a router
    change.
    """
    quests = await list_quests(session_id, status=status)
    return {
        "session_id": session_id,
        "status": status,
        "count": len(quests),
        "quests": [q.to_dict() for q in quests],
    }


@router.post("/api/quests/{quest_id}/activate")
async def activate(
    quest_id: int,
    body: ActivateBody | None = None,
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Promote the highest-voted *proposed* quest in this quest's session.

    The path parameter identifies which session pool to draw from — we
    look up ``quest_id``, take its ``session_id``, and ask
    ``activate_top_quest`` to pick the winner. That's intentionally
    looser than "activate exactly this quest": the host clicks a
    proposal in the design board UI and the swarm gets the top-voted
    card, which is almost always the one they clicked but not
    necessarily (if a late vote leapfrogged it).

    Admin/host only — gated by ``require_admin``.
    """
    record = await get_quest(quest_id)
    if record is None:
        raise _err(404, "quest_not_found", "no quest with that id.")

    round_number = int(body.round_number) if body is not None else 0
    activated = await activate_top_quest(record.session_id, round_number)
    if activated is None:
        raise _err(409, "no_proposed_quest", "no proposed quest available to activate.")

    # Free voters of the activated card to spend their next vote on a
    # fresh proposal in the next pool. See ``_clear_votes_for``.
    _clear_votes_for(activated.id)

    return {
        "status": "ok",
        "quest": activated.to_dict(),
        "round_number": round_number,
    }


@router.post("/api/quests/{quest_id}/complete")
async def complete(
    quest_id: int,
    body: CompleteBody | None = None,
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Mark ``quest_id`` completed at ``round_number``.

    Admin/host only. Idempotent: re-completing already-completed
    quests still returns 200 + emits the bus event so multi-listener
    overlays all see the closing tick. Missing rows return 404.
    """
    round_number = int(body.round_number) if body is not None else 0
    ok = await complete_quest(quest_id, round_number)
    if not ok:
        raise _err(404, "quest_not_found", "no quest with that id.")
    return {
        "status": "ok",
        "quest_id": int(quest_id),
        "round_number": round_number,
    }
