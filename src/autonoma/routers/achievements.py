"""Achievements / badges — FastAPI router (Feature #12).

Endpoints (all cookie-session protected via ``require_active_user``)::

    GET /api/agents/{character_uuid}/achievements
        Per-character badge list. Each entry merges the persisted row
        (id, tier, earned_at, project_uuid) with the catalog metadata
        from :data:`autonoma.world.ACHIEVEMENTS` (title, description,
        xp_reward) so the frontend can render the full card without a
        second roundtrip.

    GET /api/achievements/recent?limit=N
        Global ticker for the OBS HUD. Returns the most recent badge
        unlocks across every character, joined with character names /
        emojis so the overlay doesn't have to re-resolve.

The data path lives in :mod:`autonoma.achievements_db`; this module is
strictly the HTTP wrapper.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from autonoma.achievements_db import list_achievements, list_recent_globally
from autonoma.auth import User, require_active_user
from autonoma.world import ACHIEVEMENTS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["achievements"])


@router.get("/api/agents/{character_uuid}/achievements")
async def agent_achievements(
    character_uuid: str,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the badge list for a single character.

    Always 200; an unknown / empty character just gets ``items: []``.
    The 404 branch lives on the profile endpoint — this one is
    intentionally tolerant so the badges panel can render alongside a
    half-loaded profile without spurious errors.
    """
    rows = await list_achievements(character_uuid)
    items: list[dict[str, Any]] = []
    for row in rows:
        spec = ACHIEVEMENTS.get(row.achievement_id) or {}
        # Catalog tier may be an Enum; the wire format is the bare value.
        spec_tier = spec.get("tier", "")
        spec_tier_str = getattr(spec_tier, "value", spec_tier) or ""
        items.append(
            {
                "achievement_id": row.achievement_id,
                "title": spec.get("title", row.achievement_id),
                "description": spec.get("description", ""),
                "tier": row.tier or spec_tier_str,
                "xp_reward": int(spec.get("xp_reward", 0) or 0),
                "earned_at": str(row.earned_at),
                "project_uuid": row.project_uuid,
            }
        )
    return {
        "character_uuid": character_uuid,
        "count": len(items),
        "items": items,
    }


@router.get("/api/achievements/recent")
async def recent_achievements(
    limit: int = Query(20, ge=1, le=200),
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Recent badge unlocks across all characters; for the OBS ticker."""
    items = await list_recent_globally(limit=limit)
    return {"count": len(items), "items": items}


__all__ = ["router"]
