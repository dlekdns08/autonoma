"""Persona marketplace — feature #6.

A persona is an exportable bundle:

  * ``seed_string`` — drives the deterministic ``AgentBones.from_role``
  * ``name`` / ``role``
  * optional ``voice_profile_id`` + ``vrm_file``
  * free-form ``prompt_style`` (tone/voice notes the agent inherits)
  * ``tags`` for discovery

Operators can mark a persona public; public personas show up in
``GET /api/personas/public``. Private personas are visible only to
the owner. No in-app payment loop — sharing is intended to be
gift-economy for the PoC.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import desc, insert, select, update

from autonoma.auth import User, require_active_user
from autonoma.db.engine import get_engine
from autonoma.db.schema import personas

router = APIRouter(tags=["personas"])


PERSONA_BUNDLE_VERSION = "1"


def _row_to_bundle(m: Any) -> dict[str, Any]:
    """Render a row as a portable JSON bundle."""
    try:
        tags = json.loads(m["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags = []
    return {
        "bundle_version": PERSONA_BUNDLE_VERSION,
        "id": m["id"],
        "name": m["name"],
        "role": m["role"],
        "seed_string": m["seed_string"],
        "voice_profile_id": m["voice_profile_id"],
        "vrm_file": m["vrm_file"],
        "prompt_style": m["prompt_style"],
        "tags": tags,
        "is_public": bool(m["is_public"]),
        "download_count": int(m["download_count"] or 0),
        "created_at": str(m["created_at"]),
        "updated_at": str(m["updated_at"]),
    }


@router.get("/api/personas")
async def list_my_personas(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(personas)
                .where(personas.c.owner_user_id == user.id)
                .order_by(desc(personas.c.updated_at))
            )
        ).all()
    return {"personas": [_row_to_bundle(r._mapping) for r in rows]}


@router.get("/api/personas/public")
async def list_public_personas(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(personas)
                .where(personas.c.is_public == 1)
                .order_by(desc(personas.c.download_count))
                .limit(100)
            )
        ).all()
    return {"personas": [_row_to_bundle(r._mapping) for r in rows]}


@router.post("/api/personas", status_code=http_status.HTTP_201_CREATED)
async def create_persona(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    seed_string = str(payload.get("seed_string") or "").strip()
    if not (1 <= len(name) <= 64):
        raise HTTPException(400, detail={"code": "invalid_name", "message": "이름은 1-64자여야 합니다."})
    if not (1 <= len(seed_string) <= 255):
        raise HTTPException(400, detail={"code": "invalid_seed", "message": "seed_string은 1-255자여야 합니다."})
    pid = str(uuid.uuid4())
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        raise HTTPException(400, detail={"code": "invalid_tags", "message": "tags는 리스트여야 합니다."})
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(personas).values(
                id=pid,
                owner_user_id=user.id,
                name=name,
                role=str(payload.get("role") or "coder"),
                seed_string=seed_string,
                voice_profile_id=payload.get("voice_profile_id"),
                vrm_file=str(payload.get("vrm_file") or ""),
                prompt_style=str(payload.get("prompt_style") or ""),
                tags_json=json.dumps(tags),
                is_public=1 if payload.get("is_public") else 0,
            )
        )
        row = (await conn.execute(select(personas).where(personas.c.id == pid))).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "persona_not_found", "message": "방금 생성된 페르소나를 다시 읽지 못했습니다."},
        )
    return {"persona": _row_to_bundle(row._mapping)}


@router.post("/api/personas/import", status_code=http_status.HTTP_201_CREATED)
async def import_persona(
    bundle: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Import a persona JSON bundle. Always creates a NEW row under the
    caller's ownership — we never overwrite another user's persona.

    The downloaded copy drops the original's ``download_count`` but
    increments the *source* persona's counter when the source id is
    present in the bundle, so shared personas accumulate a popularity
    signal organically.
    """
    if str(bundle.get("bundle_version") or "") != PERSONA_BUNDLE_VERSION:
        raise HTTPException(400, detail={"code": "bad_bundle_version", "message": "지원하지 않는 번들 버전입니다."})
    payload = {
        "name": bundle.get("name"),
        "seed_string": bundle.get("seed_string"),
        "role": bundle.get("role"),
        "voice_profile_id": None,  # voice profile ids don't cross users
        "vrm_file": bundle.get("vrm_file"),
        "prompt_style": bundle.get("prompt_style"),
        "tags": bundle.get("tags") or [],
        "is_public": False,  # imports start private
    }
    # Bump source popularity counter if present + public
    source_id = bundle.get("id")
    engine = get_engine()
    async with engine.begin() as conn:
        if source_id:
            await conn.execute(
                update(personas)
                .where(personas.c.id == str(source_id))
                .where(personas.c.is_public == 1)
                .values(download_count=personas.c.download_count + 1)
            )
    return await create_persona(payload, user)


@router.post("/api/personas/{persona_id}/publish")
async def toggle_publish(
    persona_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, str]:
    make_public = bool(payload.get("is_public"))
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            update(personas)
            .where(personas.c.id == persona_id)
            .where(personas.c.owner_user_id == user.id)
            .values(is_public=1 if make_public else 0)
        )
    if result.rowcount == 0:
        raise HTTPException(404, detail={"code": "persona_not_found", "message": "해당 페르소나를 찾을 수 없거나 권한이 없습니다."})
    return {"status": "ok", "is_public": str(make_public).lower()}
