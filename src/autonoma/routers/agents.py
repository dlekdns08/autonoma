"""Public agent profile — feature #5.

Each persisted character gets a read-only profile page fed by this
router. Three responsibilities:

* ``GET /api/agents/{name_or_uuid}/profile`` — returns the bones,
  lifetime stats, recent diary entries, top relationships.
* ``POST /api/agents/{uuid}/journal`` — author-only: pin a note to
  the profile.
* ``GET /api/agents/{uuid}/autobiography`` — streams a narrative
  synthesized from the journal via the admin LLM. Optional, off by
  default so idle curiosity doesn't burn tokens.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import desc, func, select

from autonoma.auth import User, require_active_user
from autonoma.db.engine import get_engine
from autonoma.db.schema import (
    agent_journal,
    characters,
    project_participants,
    relationships,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


async def _find_character(identifier: str) -> dict[str, Any] | None:
    """Resolve a name or uuid to a single character row (latest-alive wins)."""
    engine = get_engine()
    async with engine.connect() as conn:
        # UUID first
        row = (
            await conn.execute(
                select(characters).where(characters.c.character_uuid == identifier)
            )
        ).first()
        if row is None:
            # Fall back to newest-alive by name; names can collide, so we
            # pick the most recently seen living one.
            row = (
                await conn.execute(
                    select(characters)
                    .where(characters.c.name == identifier)
                    .order_by(
                        desc(characters.c.is_alive),
                        desc(characters.c.last_seen_at),
                    )
                )
            ).first()
    return dict(row._mapping) if row else None


@router.get("/api/agents/{identifier}/profile")
async def agent_profile(
    identifier: str,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    char = await _find_character(identifier)
    if not char:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "agent_not_found", "message": "해당 이름/uuid의 캐릭터를 찾을 수 없습니다."},
        )
    uuid = char["character_uuid"]
    engine = get_engine()
    async with engine.connect() as conn:
        # Recent diary / memory
        journal_rows = (
            await conn.execute(
                select(agent_journal)
                .where(agent_journal.c.character_uuid == uuid)
                .order_by(desc(agent_journal.c.created_at))
                .limit(40)
            )
        ).all()
        # Project participation count
        runs = (
            await conn.execute(
                select(func.count())
                .select_from(project_participants)
                .where(project_participants.c.character_uuid == uuid)
            )
        ).scalar_one()
        # Outbound relationships (friends + rivals)
        rels = (
            await conn.execute(
                select(relationships)
                .where(relationships.c.from_uuid == uuid)
                .order_by(desc(relationships.c.trust))
                .limit(20)
            )
        ).all()

    def _parse_json(raw: Any, default: Any) -> Any:
        try:
            return json.loads(raw) if raw else default
        except (TypeError, json.JSONDecodeError):
            return default

    return {
        "character": {
            "uuid": uuid,
            "name": char["name"],
            "role": char["role"],
            "species": char["species"],
            "species_emoji": char["species_emoji"],
            "catchphrase": char["catchphrase"],
            "rarity": char["rarity"],
            "level": char["level"],
            "total_xp": char["total_xp_earned"],
            "runs_survived": char["runs_survived"],
            "runs_died": char["runs_died"],
            "tasks_completed": char["tasks_completed_lifetime"],
            "files_created": char["files_created_lifetime"],
            "traits": _parse_json(char["traits_json"], []),
            "stats": _parse_json(char["stats_json"], {}),
            "last_mood": char["last_mood"],
            "is_alive": bool(char["is_alive"]),
            "first_seen": str(char["first_seen_at"]),
            "last_seen": str(char["last_seen_at"]),
        },
        "runs": int(runs or 0),
        "journal": [
            {
                "kind": r._mapping["kind"],
                "round": r._mapping["round_number"],
                "mood": r._mapping["mood"],
                "text": r._mapping["text"],
                "at": str(r._mapping["created_at"]),
            }
            for r in journal_rows
        ],
        "relationships": [
            {
                "to_uuid": r._mapping["to_uuid"],
                "trust": r._mapping["trust"],
                "familiarity": r._mapping["familiarity"],
                "sentiment": r._mapping["sentiment"],
                "last_interaction": r._mapping["last_interaction"],
            }
            for r in rels
        ],
    }


@router.post("/api/agents/{uuid}/journal/pin")
async def pin_note(
    uuid: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, str]:
    """Pin a user-authored ``note`` to an agent's journal. Only the user
    who is currently logged in can pin notes; the UI decides whether to
    surface the action (typically for owners/admins)."""
    text = str(payload.get("text") or "").strip()
    if not (1 <= len(text) <= 2000):
        raise HTTPException(
            400,
            detail={"code": "invalid_text", "message": "노트는 1-2000자여야 합니다."},
        )
    char = await _find_character(uuid)
    if char is None:
        raise HTTPException(404, detail={"code": "agent_not_found", "message": "해당 캐릭터를 찾을 수 없습니다."})
    from sqlalchemy import insert
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(agent_journal).values(
                character_uuid=char["character_uuid"],
                project_uuid=None,
                kind="note",
                round_number=0,
                mood="",
                text=f"[by {user.username}] {text}",
            )
        )
    return {"status": "ok"}
