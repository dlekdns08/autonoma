"""A/B preset comparison — FastAPI router (Feature #19).

Endpoints (all cookie-session, owner-scoped via ``require_active_user``)::

    POST /api/ab/compare       — body ``{"session_a": int, "session_b": int}``
                                 returns the :class:`ABReport` JSON.
    GET  /api/ab/recent-runs   — newest run_summary rows for the picker.

Read-only — both endpoints query the ``run_summary`` and
``session_anomalies`` tables but never mutate them. Heavy lifting lives
in :mod:`autonoma.ab_compare`; this module only handles HTTP plumbing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from autonoma.ab_compare import ABReport, compare_sessions, list_recent_runs
from autonoma.auth import User, require_active_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ab-compare"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _coerce_session_id(raw: Any, field_name: str) -> int:
    """Validate that the body field is an integer-shaped value.

    Strings like ``"42"`` are accepted because the cookie-auth front-end
    occasionally serialises numbers as strings; non-coercible values
    return a clean 400 with a stable error code so the UI can surface
    a useful message.
    """
    if raw is None:
        raise _err(400, "missing_session", f"{field_name} is required.")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise _err(400, "invalid_session", f"{field_name} must be an integer.")


@router.post("/api/ab/compare")
async def post_ab_compare(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Compare two completed swarm runs by ``session_id``.

    Body shape::

        {"session_a": 17, "session_b": 19}

    Returns the :class:`ABReport` as JSON. Missing rows in
    ``run_summary`` do not 404 — the report is still returned with
    ``winner="unknown"`` so the UI can show an "incomplete data"
    empty state without a separate error path.
    """
    body = dict(payload or {})
    session_a = _coerce_session_id(body.get("session_a"), "session_a")
    session_b = _coerce_session_id(body.get("session_b"), "session_b")

    report: ABReport = await compare_sessions(session_a, session_b)
    return report.to_dict()


@router.get("/api/ab/recent-runs")
async def get_recent_runs(
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Newest ``run_summary`` rows for the AB-compare picker."""
    runs = await list_recent_runs(limit=limit)
    return {"count": len(runs), "runs": runs}
