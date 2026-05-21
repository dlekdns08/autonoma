"""Custom Achievement DSL — admin-only FastAPI router.

Endpoints (all gated by :func:`autonoma.auth.require_admin`)::

    GET    /api/custom-achievements           — list all definitions
    POST   /api/custom-achievements           — create + validate a new def
    PATCH  /api/custom-achievements/{id}      — toggle enabled
    DELETE /api/custom-achievements/{id}      — remove def + counters

Validation + persistence lives in
:mod:`autonoma.custom_achievements`; this module is strictly the HTTP
wrapper.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from autonoma.auth import User, require_admin
from autonoma.custom_achievements import (
    DSLValidationError,
    create_definition,
    delete_definition,
    list_definitions,
    refresh_cache,
    set_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["custom-achievements"])


class PatchBody(BaseModel):
    enabled: bool


@router.get("/api/custom-achievements")
async def list_custom_achievements(
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """List every custom achievement definition (enabled + disabled)."""
    items = await list_definitions()
    return {"count": len(items), "items": items}


@router.post("/api/custom-achievements")
async def create_custom_achievement(
    payload: dict[str, Any] = Body(...),
    user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Create a new definition. 400 on DSL validation failure."""
    try:
        defn = await create_definition(payload, created_by=str(user.id))
    except DSLValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_dsl", "message": str(exc)},
        ) from exc
    return {"status": "ok", "definition": defn.to_wire()}


@router.patch("/api/custom-achievements/{achievement_id}")
async def patch_custom_achievement(
    achievement_id: str,
    body: PatchBody,
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Toggle the ``enabled`` flag on a definition."""
    ok = await set_enabled(achievement_id, body.enabled)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "no such custom achievement"},
        )
    return {"status": "ok", "id": achievement_id, "enabled": body.enabled}


@router.delete("/api/custom-achievements/{achievement_id}")
async def delete_custom_achievement(
    achievement_id: str,
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Remove a definition and its progress counters.

    Already-earned ``earned_achievements`` rows are left alone — once a
    character has the badge, deleting the definition should NOT retract
    history.
    """
    ok = await delete_definition(achievement_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "no such custom achievement"},
        )
    return {"status": "ok", "id": achievement_id, "deleted": True}


@router.post("/api/custom-achievements/_refresh-cache")
async def refresh_custom_cache(
    _user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Force a reload of the in-memory active-def cache. For debugging."""
    await refresh_cache()
    return {"status": "ok"}


__all__ = ["router"]
