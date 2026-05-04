"""Session anomaly listing — Feature #18.

Endpoint::

    GET /api/sessions/{session_id}/anomalies

Cookie-session required (``require_active_user``). Returns every
anomaly the in-process detector emitted for the given session, oldest
first, so the AB-compare overlay can plot them on a timeline.

Mounting note: this router exposes a ``router`` attribute and follows
the same convention as the rest of ``autonoma.routers.*``. Wire it
into ``api.py`` next to the other 2026 feature routers via
``app.include_router(_anomalies_router.router)``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from autonoma.anomaly import list_anomalies
from autonoma.auth import User, require_active_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["anomalies"])


@router.get("/api/sessions/{session_id}/anomalies")
async def get_session_anomalies(
    session_id: int,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return every persisted anomaly for ``session_id``.

    The response shape mirrors the other read-only feature routers:
    a small envelope with ``session_id`` + ``count`` + ``anomalies``.
    Each anomaly is the dict produced by :meth:`Anomaly.to_dict` so the
    frontend can render the rule's ``details`` payload directly.
    """
    anomalies = await list_anomalies(session_id)
    return {
        "session_id": int(session_id),
        "count": len(anomalies),
        "anomalies": [a.to_dict() for a in anomalies],
    }
