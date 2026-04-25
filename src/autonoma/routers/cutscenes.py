"""Cutscene composer API — Phase 3-#3.

Endpoints (all cookie-session, owner-scoped)::

    GET    /api/cutscenes               — list mine
    POST   /api/cutscenes               — create
    GET    /api/cutscenes/{id}          — fetch one
    PUT    /api/cutscenes/{id}          — replace
    DELETE /api/cutscenes/{id}          — drop
    POST   /api/cutscenes/{id}/play     — fan out cutscene.step events

Trigger evaluation (project_complete / achievement / boss_defeated) is
bus-driven and lives in the swarm runtime — we wire it as a one-time
bus subscription at module import time so cutscenes "just work" even
without the host pressing Play.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma.auth import User, require_active_user
from autonoma.cutscenes import (
    Cutscene,
    CutsceneNotFound,
    CutsceneStepKind,
    cutscene_store,
)
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cutscenes"])


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}
    )


@router.get("/api/cutscenes")
async def list_my_cutscenes(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    items = cutscene_store.list_for_owner(user.id)
    return {"cutscenes": [c.model_dump(mode="json") for c in items]}


@router.post("/api/cutscenes", status_code=http_status.HTTP_201_CREATED)
async def create_cutscene(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    payload = dict(payload or {})
    payload["owner_user_id"] = user.id  # never trust the client
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    try:
        cutscene = Cutscene.model_validate(payload)
    except Exception as exc:
        raise _err(400, "invalid_cutscene", str(exc)) from exc
    saved = cutscene_store.save(cutscene)
    return {"cutscene": saved.model_dump(mode="json")}


@router.get("/api/cutscenes/{cutscene_id}")
async def get_cutscene(
    cutscene_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        cutscene = cutscene_store.get(user.id, cutscene_id)
    except CutsceneNotFound:
        raise _err(404, "cutscene_not_found", "컷씬을 찾을 수 없습니다.")
    except ValueError as exc:
        raise _err(422, "cutscene_corrupt", str(exc)) from exc
    return {"cutscene": cutscene.model_dump(mode="json")}


@router.put("/api/cutscenes/{cutscene_id}")
async def update_cutscene(
    cutscene_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        existing = cutscene_store.get(user.id, cutscene_id)
    except CutsceneNotFound:
        raise _err(404, "cutscene_not_found", "컷씬을 찾을 수 없습니다.")
    payload = dict(payload or {})
    # Preserve identity / ownership: clients can't change either via
    # this endpoint.
    payload["id"] = existing.id
    payload["owner_user_id"] = user.id
    payload["created_at"] = existing.created_at
    payload.pop("updated_at", None)
    try:
        updated = Cutscene.model_validate(payload)
    except Exception as exc:
        raise _err(400, "invalid_cutscene", str(exc)) from exc
    saved = cutscene_store.save(updated)
    return {"cutscene": saved.model_dump(mode="json")}


@router.delete(
    "/api/cutscenes/{cutscene_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_cutscene(
    cutscene_id: str,
    user: User = Depends(require_active_user),
) -> None:
    if not cutscene_store.delete(user.id, cutscene_id):
        raise _err(404, "cutscene_not_found", "컷씬을 찾을 수 없습니다.")


@router.post("/api/cutscenes/{cutscene_id}/play")
async def play_cutscene(
    cutscene_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Fan the cutscene's steps onto the bus as ``cutscene.step`` events.

    Each step fires after its ``at_ms`` offset has elapsed since the
    play call. We don't wait — the endpoint returns immediately so the
    HTTP request doesn't pin a worker for the full cutscene duration.
    The frontend hook is also responsible for client-side scheduling so
    audio/clip alignment stays jitter-free; this fan-out exists mainly
    so REMOTE viewers (mobile /watch, OBS) see the same cutscene as the
    host.
    """
    try:
        cutscene = cutscene_store.get(user.id, cutscene_id)
    except CutsceneNotFound:
        raise _err(404, "cutscene_not_found", "컷씬을 찾을 수 없습니다.")

    await bus.emit(
        "cutscene.started",
        cutscene_id=cutscene.id,
        name=cutscene.name,
        owner=user.id,
        total_ms=cutscene.total_duration_ms(),
    )

    async def _runner() -> None:
        for step in cutscene.steps:
            # Sleep relative to play start. We re-anchor to "now" each
            # iteration so a slow step doesn't drift the schedule —
            # asyncio.sleep semantics already handle that. Using
            # ``at_ms`` as an absolute offset means the schedule
            # remains stable even if a previous emit took a few ms.
            await asyncio.sleep(0)  # cooperative yield
            try:
                await bus.emit(
                    "cutscene.step",
                    cutscene_id=cutscene.id,
                    at_ms=step.at_ms,
                    kind=step.kind.value
                    if isinstance(step.kind, CutsceneStepKind)
                    else str(step.kind),
                    label=step.label,
                    payload=step.payload,
                )
            except Exception as exc:
                logger.warning(f"[cutscenes] step emit failed: {exc}")
        await bus.emit("cutscene.finished", cutscene_id=cutscene.id)

    asyncio.create_task(_runner())
    return {"status": "started", "cutscene_id": cutscene.id}


# ── Trigger wiring ────────────────────────────────────────────────────


def _matches_trigger(cutscene: Cutscene, event: str, data: dict[str, Any]) -> bool:
    trig = cutscene.trigger
    if trig.kind == "manual":
        return False
    if trig.kind == "project_complete" and event == "project.completed":
        return True
    if trig.kind == "achievement" and event == "achievement.earned":
        return not trig.value or trig.value == data.get("achievement_id")
    if trig.kind == "boss_defeated" and event == "boss.defeated":
        return True
    return False


async def _on_bus_event(event_name: str, data: dict[str, Any]) -> None:
    """Tap handler — fires matching cutscenes when relevant events emit."""
    if event_name not in (
        "project.completed",
        "achievement.earned",
        "boss.defeated",
    ):
        return
    for cutscene in cutscene_store.iter_all():
        if not _matches_trigger(cutscene, event_name, data):
            continue
        # Re-use the same fan-out as the manual play endpoint.
        async def _runner(cs: Cutscene) -> None:
            await bus.emit(
                "cutscene.started",
                cutscene_id=cs.id,
                name=cs.name,
                owner=cs.owner_user_id,
                total_ms=cs.total_duration_ms(),
                triggered_by=event_name,
            )
            for step in cs.steps:
                await asyncio.sleep(0)
                await bus.emit(
                    "cutscene.step",
                    cutscene_id=cs.id,
                    at_ms=step.at_ms,
                    kind=step.kind.value
                    if isinstance(step.kind, CutsceneStepKind)
                    else str(step.kind),
                    label=step.label,
                    payload=step.payload,
                )
            await bus.emit("cutscene.finished", cutscene_id=cs.id)

        asyncio.create_task(_runner(cutscene))


# Subscribe once on module import — the tap is global so we don't
# attach per-swarm. Re-importing the module won't double-subscribe in
# normal Python because ``import`` is cached.
_TRIGGER_TAP_INSTALLED = False


def install_trigger_tap() -> None:
    global _TRIGGER_TAP_INSTALLED
    if _TRIGGER_TAP_INSTALLED:
        return
    bus.tap(_on_bus_event)
    _TRIGGER_TAP_INSTALLED = True


install_trigger_tap()
