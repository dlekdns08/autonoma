"""Quest templates — per-user reusable quest text bank.

Endpoints
─────────
::

    GET    /api/quest-templates           — list caller's templates
    POST   /api/quest-templates           — save text as new template
    DELETE /api/quest-templates/{id}      — owner-only delete

The text cap mirrors ``autonoma.quests.MAX_TEXT_LEN`` (256) so any
saved template can be proposed as-is via ``POST /api/quests/propose``.

Scoping is strictly per-user: there's no admin "see everyone's
templates" path because templates are a personal scratchpad — sharing
intent lives in the persona marketplace, not here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, insert, select

from autonoma.auth import User, require_active_user
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import quest_templates
from autonoma.quests import MAX_TEXT_LEN

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quest-templates"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ── Request models ────────────────────────────────────────────────────


class CreateTemplateBody(BaseModel):
    text: str = Field(..., description="quest template body, <=256 chars")


# ── Helpers ───────────────────────────────────────────────────────────


def _row_to_dict(m: Any) -> dict[str, Any]:
    return {
        "id": int(m["id"]),
        "user_id": str(m["user_id"]),
        "text": str(m["text"]),
        "created_at": (m["created_at"].isoformat() if m["created_at"] is not None else None),
    }


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/api/quest-templates")
async def list_my_templates(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return every template saved by the caller, newest first."""
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(quest_templates)
                .where(quest_templates.c.user_id == user.id)
                .order_by(desc(quest_templates.c.created_at), desc(quest_templates.c.id))
            )
        ).all()
    return {
        "count": len(rows),
        "templates": [_row_to_dict(r._mapping) for r in rows],
    }


@router.post("/api/quest-templates", status_code=http_status.HTTP_201_CREATED)
async def create_template(
    body: CreateTemplateBody,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Insert a new template for the caller.

    Same validation as ``propose_quest``: trim, reject empty, cap at
    ``MAX_TEXT_LEN``. We don't dedup — the user is free to keep two
    templates with identical text if that's intentional.
    """
    cleaned = (body.text or "").strip()
    if not cleaned:
        raise _err(400, "template_text_empty", "template text must not be empty.")
    if len(cleaned) > MAX_TEXT_LEN:
        raise _err(
            400,
            "template_text_too_long",
            f"template text exceeds {MAX_TEXT_LEN} characters (got {len(cleaned)}).",
        )

    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            insert(quest_templates).values(
                user_id=user.id,
                text=cleaned,
            )
        )
        template_id = int(result.inserted_primary_key[0])
        row = (
            await conn.execute(select(quest_templates).where(quest_templates.c.id == template_id))
        ).first()

    if row is None:
        # Vanishingly unlikely (we just inserted in the same txn) but
        # keep the contract honest.
        raise _err(500, "template_insert_failed", "could not load saved template.")

    return {
        "status": "ok",
        "template": _row_to_dict(row._mapping),
    }


@router.delete("/api/quest-templates/{template_id}")
async def delete_template(
    template_id: int,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Delete a template the caller owns.

    Returns 404 for both "doesn't exist" and "exists but belongs to
    someone else" so we don't leak the existence of other users' rows.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(quest_templates)
            .where(quest_templates.c.id == int(template_id))
            .where(quest_templates.c.user_id == user.id)
        )
        if result.rowcount == 0:
            raise _err(404, "template_not_found", "no template with that id.")

    return {
        "status": "ok",
        "template_id": int(template_id),
    }
