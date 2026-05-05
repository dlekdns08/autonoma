"""Persona breeding HTTP endpoint — feature #13.

POST /api/personas/breed
    body: ``{"parent_a_id": "...", "parent_b_id": "...", "name": "..."}``

Owner check: each parent must be either public, or owned by the
caller. We don't allow breeding from a stranger's private persona —
that would leak the seed_string and prompt_style through the child.

Returns 201 with ``{"persona": <bundle>}`` on success. 400 when the
two parent ids are the same or when validation fails. 404 when
either parent row is missing or not visible to the caller.

We deliberately keep this in a separate router file so the existing
``routers/personas.py`` doesn't grow another endpoint with another
import surface — breeding can ship without retesting persona
import/export/publish.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select

from autonoma.auth import User, require_active_user
from autonoma.db.engine import get_engine
from autonoma.db.schema import personas
from autonoma.personas_breed import breed_personas

router = APIRouter(tags=["personas"])


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=http_status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )


@router.post("/api/personas/breed", status_code=http_status.HTTP_201_CREATED)
async def breed(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    parent_a_id = str(payload.get("parent_a_id") or "").strip()
    parent_b_id = str(payload.get("parent_b_id") or "").strip()
    name = str(payload.get("name") or "").strip()

    if not parent_a_id or not parent_b_id:
        raise _bad_request("missing_parent", "parent_a_id, parent_b_id가 모두 필요합니다.")
    if parent_a_id == parent_b_id:
        raise _bad_request("same_parent", "동일한 페르소나끼리는 교배할 수 없습니다.")
    if not (1 <= len(name) <= 64):
        raise _bad_request("invalid_name", "이름은 1-64자여야 합니다.")

    # Owner-or-public visibility check: load both rows up front so we
    # can issue precise 404s without leaking information about which
    # parent failed the check.
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(personas).where(personas.c.id.in_([parent_a_id, parent_b_id]))
            )
        ).all()
    by_id = {r._mapping["id"]: r._mapping for r in rows}
    for pid in (parent_a_id, parent_b_id):
        row = by_id.get(pid)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "persona_not_found",
                    "message": "부모 페르소나를 찾을 수 없습니다.",
                },
            )
        is_public = bool(row["is_public"])
        is_owner = row["owner_user_id"] == user.id
        if not (is_public or is_owner):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "persona_forbidden",
                    "message": "이 페르소나로 교배할 권한이 없습니다.",
                },
            )

    try:
        bundle = await breed_personas(
            parent_a_id=parent_a_id,
            parent_b_id=parent_b_id,
            child_name=name,
            owner_user_id=user.id,
            llm_client=None,
        )
    except ValueError as exc:
        # ``breed_personas`` raises ValueError for same-parent /
        # missing-parent. We've already validated above, so anything
        # arriving here is genuinely a bad request worth surfacing.
        raise _bad_request(str(exc) or "breed_failed", "교배에 실패했습니다.")
    return {"persona": bundle}
