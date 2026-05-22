"""Viewer Fantasy Draft — FastAPI router (MVP).

Endpoints
─────────
::

    GET  /api/sessions/{session_id}/draft/agents
    POST /api/sessions/{session_id}/draft
    GET  /api/sessions/{session_id}/draft/scoreboard

All three require an active cookie session (guests count) — viewers on
``/watch/<code>`` typically authenticate as guests, so we deliberately
do *not* apply ``assert_session_owner_or_admin`` here: that helper is
for routes that mutate session-private state, and the whole point of
this feature is to let *spectators* play along.

We still validate that the session id maps to a live session (404
otherwise) so the modal can render a sensible "session ended" state
without dumping a stack trace.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from autonoma.auth import User, require_active_user
from autonoma.draft import (
    PICK_COUNT,
    DraftError,
    InvalidPicks,
    get_draft,
    list_session_agents,
    normalize_picks,
    scoreboard,
    upsert_draft,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["draft"])


def _err(status: int, code: str, message: str) -> HTTPException:
    """Standard structured-error helper, matches the rest of the routers."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _assert_session_exists(session_id: int) -> None:
    """Surface 404 when the session id isn't currently live.

    Mirrors ``_session_owner.assert_session_owner_or_admin`` but
    without the ownership check — viewers are not session owners.
    Falls back to a no-op when the api module isn't importable
    (CI / direct-handler unit tests).
    """
    try:
        from autonoma.api import _sessions
    except ImportError:
        return
    if not _sessions:
        # No live registry — typical of router-coroutine unit tests
        # that invoke handlers directly without the full app
        # lifecycle. Skip the existence check; the swarm-bridge
        # helpers degrade gracefully in that case anyway.
        return
    try:
        sid_int = int(session_id)
    except (TypeError, ValueError):
        raise _err(404, "session_not_found", "no session with that id.")
    if sid_int not in _sessions:
        raise _err(404, "session_not_found", "no session with that id.")


# ── Request models ────────────────────────────────────────────────────


class DraftBody(BaseModel):
    """Three-agent roster pick.

    Pydantic enforces the list-of-strings shape; the count + distinctness
    rules are checked by :func:`autonoma.draft.normalize_picks` so the
    same validation runs on every entry point (router, CLI, tests).
    """

    picks: list[str] = Field(
        ...,
        description=f"exactly {PICK_COUNT} distinct agent names",
        min_length=PICK_COUNT,
        max_length=PICK_COUNT,
    )


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/draft/agents")
async def list_agents_for_draft(
    session_id: int,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the agents currently live in the session.

    Shape: ``{"agents": [{name, emoji, role, mood}, …]}``. The list is
    a snapshot — agents may spawn/despawn between calls, which is fine
    because the modal re-polls when it re-opens and the score function
    treats unknown picks as 0 rather than erroring.
    """
    _assert_session_exists(session_id)
    agents = list_session_agents(session_id)
    return {"session_id": int(session_id), "agents": agents}


@router.post("/api/sessions/{session_id}/draft")
async def submit_draft(
    session_id: int,
    body: DraftBody,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Upsert the caller's 3-agent roster for this session.

    Idempotent at the (viewer_id, session_id) level — resubmitting
    overwrites the previous picks in place. We resolve a friendly
    display label from ``user.username`` so the scoreboard doesn't have
    to do a join on every render.

    Errors:
      * ``400 invalid_picks``  — count/distinctness/empties (raised
        from :func:`autonoma.draft.normalize_picks`).
      * ``404 session_not_found`` — session id isn't currently live.
    """
    _assert_session_exists(session_id)
    try:
        picks = normalize_picks(list(body.picks))
    except InvalidPicks as exc:
        raise _err(400, exc.code, str(exc))
    except DraftError as exc:  # defensive — any new subclass falls here
        raise _err(400, exc.code, str(exc))

    # ``username`` may be empty for guest users; the scoreboard falls
    # back to a viewer_id prefix when this is blank, so we don't try to
    # synthesise something here.
    display = getattr(user, "username", "") or ""
    record = await upsert_draft(
        viewer_id=user.id,
        session_id=int(session_id),
        picks=picks,
        viewer_name=display,
    )
    return {
        "status": "ok",
        "draft": record.to_dict(),
    }


@router.get("/api/sessions/{session_id}/draft/scoreboard")
async def get_scoreboard(
    session_id: int,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Live scoreboard for ``session_id``.

    Shape: ``{"rows": [{viewer_name, picks, score}, …], "my_rank":
    int | None}``. Rows are ordered by score descending; ``my_rank``
    is the caller's 1-indexed position if they've submitted a draft,
    else ``None``.

    Score is computed *live* from the swarm's in-memory agent stats
    (see :func:`autonoma.draft.scoreboard`). The MVP polls this
    endpoint at ~5s from the watch page — fast enough to feel live
    without putting load on the server.
    """
    _assert_session_exists(session_id)
    payload = await scoreboard(session_id, my_viewer_id=user.id)
    # Also surface the caller's own current draft so the modal can
    # pre-check their existing picks on re-open without a second
    # request. None if they haven't drafted yet.
    own = await get_draft(user.id, session_id)
    return {
        "session_id": int(session_id),
        "rows": payload["rows"],
        "my_rank": payload["my_rank"],
        "my_picks": own.picks if own is not None else None,
    }
