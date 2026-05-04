"""Auto highlight reel detection — FastAPI router (Feature #5).

Endpoints (all cookie-session, owner-scoped via ``require_active_user``)::

    GET  /api/highlights/{session_id}        — ranked candidate list
    POST /api/highlights/{session_id}/clip   — request a clip for one moment

The ``GET`` endpoint reads from the in-memory ``HighlightRecorder``
singleton (see ``autonoma.highlights``); the ``POST`` endpoint emits a
``highlights.clip_ready`` bus event so the OBS overlay / MediaRecorder
buffer on the frontend can slice the actual MP4. Server never holds
video bytes — we only schedule the cut.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma._session_owner import assert_session_owner_or_admin
from autonoma.auth import User, require_active_user
from autonoma.event_bus import bus
from autonoma.highlights import get_recorder

logger = logging.getLogger(__name__)

router = APIRouter(tags=["highlights"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}
    )


@router.get("/api/highlights/{session_id}")
async def list_highlights(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the top-N ranked highlight candidates for ``session_id``.

    Cap (``settings.highlights_max_clips``) is applied inside
    ``HighlightRecorder.snapshot``. The list is sorted by score
    descending, then timestamp descending so the freshest peak rises
    to the top on ties.
    """
    if not session_id:
        raise _err(400, "missing_session", "session_id is required.")
    # IDOR fix (C5): refuse to surface highlight candidates for a
    # session the caller doesn't own. 404 (not 403) so a hostile
    # client can't enumerate live session ids.
    assert_session_owner_or_admin(session_id, user)
    recorder = get_recorder()
    candidates = recorder.snapshot(session_id)
    return {
        "session_id": session_id,
        "count": len(candidates),
        "candidates": [c.to_dict() for c in candidates],
    }


@router.post("/api/highlights/{session_id}/clip")
async def request_clip(
    session_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Emit ``highlights.clip_ready`` so the OBS overlay can slice a clip.

    Body shape::

        {"kind": "boss.defeated", "title": "Dragon down", "round_number": 12}

    All fields are optional from the server's perspective — but the
    frontend uses ``kind`` + ``title`` for the on-screen toast and
    ``round_number`` to align the clip cursor in the rolling buffer.
    """
    if not session_id:
        raise _err(400, "missing_session", "session_id is required.")
    # Same IDOR check (C5) as the GET handler — only the session
    # owner (or an admin) can request a clip.
    assert_session_owner_or_admin(session_id, user)

    body = dict(payload or {})
    kind = str(body.get("kind") or "manual")
    title = str(body.get("title") or "Highlight")
    raw_round = body.get("round_number") or body.get("round") or 0
    try:
        round_number = int(raw_round)
    except (TypeError, ValueError):
        round_number = 0

    await bus.emit(
        "highlights.clip_ready",
        session_id=session_id,
        kind=kind,
        title=title,
        round_number=round_number,
        requested_by=user.id,
    )

    return {
        "status": "ok",
        "session_id": session_id,
        "kind": kind,
        "title": title,
        "round_number": round_number,
    }
