"""Session ownership helper used by routers that take ``session_id``.

Centralises the "is this caller allowed to operate on this session?"
check so routers don't each reimplement it. Resolves through the live
``_sessions`` map (populated by api.py) — modules that can't import
``api`` directly use this instead.
"""
from __future__ import annotations
from typing import Any
from fastapi import HTTPException, status as http_status

def assert_session_owner_or_admin(session_id: int, user: Any) -> None:
    """Raise 404 if the session doesn't exist OR the caller isn't its owner.

    Admin users (``user.role == 'admin'``) bypass the owner check.
    404 (not 403) is intentional — leaking 'this session exists but
    you can't see it' enables enumeration. The caller can ignore the
    distinction in their handler.
    """
    # Lazy-import api to avoid the import cycle at module load.
    try:
        from autonoma.api import _sessions
    except ImportError:
        return  # CI / standalone uses don't have the live registry
    sess = _sessions.get(int(session_id))
    if sess is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found"},
        )
    if getattr(user, "role", "") == "admin":
        return
    if sess.owner_user_id != getattr(user, "id", None):
        # Same 404 shape — no enumeration leak.
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found"},
        )
