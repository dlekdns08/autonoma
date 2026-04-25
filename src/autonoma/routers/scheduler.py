"""Schedule REST API — Phase 4-A.

Endpoints (cookie-session, owner-scoped)::

    GET    /api/schedules               — list mine
    POST   /api/schedules               — create
    GET    /api/schedules/{id}          — fetch one
    PUT    /api/schedules/{id}          — replace (settings + enabled)
    DELETE /api/schedules/{id}          — drop
    POST   /api/schedules/{id}/fire     — manually trigger
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma.auth import User, require_active_user
from autonoma.scheduler import (
    Schedule,
    ScheduleNotFound,
    schedule_store,
    scheduler_runner,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["scheduler"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}
    )


@router.get("/api/schedules")
async def list_my_schedules(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    items = schedule_store.list_for_owner(user.id)
    return {
        "schedules": [s.model_dump(mode="json") for s in items],
        "runner_active": scheduler_runner.running,
    }


@router.post("/api/schedules", status_code=http_status.HTTP_201_CREATED)
async def create_schedule(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    payload = dict(payload or {})
    payload["owner_user_id"] = user.id
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    payload.pop("last_fired_at", None)
    try:
        schedule = Schedule.model_validate(payload)
    except Exception as exc:
        raise _err(400, "invalid_schedule", str(exc)) from exc
    saved = schedule_store.save(schedule)
    return {"schedule": saved.model_dump(mode="json")}


@router.get("/api/schedules/{sched_id}")
async def get_schedule(
    sched_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        schedule = schedule_store.get(user.id, sched_id)
    except ScheduleNotFound:
        raise _err(404, "schedule_not_found", "스케줄을 찾을 수 없습니다.")
    return {"schedule": schedule.model_dump(mode="json")}


@router.put("/api/schedules/{sched_id}")
async def update_schedule(
    sched_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        existing = schedule_store.get(user.id, sched_id)
    except ScheduleNotFound:
        raise _err(404, "schedule_not_found", "스케줄을 찾을 수 없습니다.")
    payload = dict(payload or {})
    payload["id"] = existing.id
    payload["owner_user_id"] = user.id
    payload["created_at"] = existing.created_at
    # Preserve last_fired_at unless caller explicitly resets it.
    payload.setdefault("last_fired_at", existing.last_fired_at)
    payload.pop("updated_at", None)
    try:
        updated = Schedule.model_validate(payload)
    except Exception as exc:
        raise _err(400, "invalid_schedule", str(exc)) from exc
    saved = schedule_store.save(updated)
    return {"schedule": saved.model_dump(mode="json")}


@router.delete(
    "/api/schedules/{sched_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_schedule(
    sched_id: str,
    user: User = Depends(require_active_user),
) -> None:
    if not schedule_store.delete(user.id, sched_id):
        raise _err(404, "schedule_not_found", "스케줄을 찾을 수 없습니다.")


@router.post("/api/schedules/{sched_id}/fire")
async def fire_schedule(
    sched_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    fired = await scheduler_runner.fire_now(user.id, sched_id)
    if not fired:
        raise _err(
            404,
            "schedule_not_found_or_disabled",
            "스케줄이 없거나 비활성화 상태입니다.",
        )
    return {"fired": True, "schedule_id": sched_id}
