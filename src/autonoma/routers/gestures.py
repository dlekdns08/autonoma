"""Self-authored gesture API.

    GET  /api/gestures?agent=NAME         — active generated gestures for an agent
    GET  /api/gestures/{id}               — one gesture incl. its score
    GET  /api/gestures/{id}/clip          — playable payload (mocap clip shape)
    POST /api/gestures/generate           — compose one for {agent, kind, value}
    POST /api/gestures/{id}/vote          — {"up": true|false}; retires on net -3

Clip payloads are served in exactly the ``/api/mocap-clips/{id}`` shape so
the stage loader only needs the ``gen:`` id prefix.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field

from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.gestures import service
from autonoma.gestures.choreographer import CharacterContext
from autonoma.mocap.triggers import validate_trigger

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gestures"])

# One vote per (user, gesture) for the process lifetime — same posture as
# quest voting (``routers/quests.py``): simple, not persisted.
_voted: set[tuple[str, str]] = set()


class GenerateRequest(BaseModel):
    agent: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=16)
    value: str = Field(min_length=1, max_length=64)
    force: bool = False
    role: str = ""
    species: str = ""
    traits: list[str] = Field(default_factory=list)
    catchphrase: str = ""
    mood: str = ""


class VoteRequest(BaseModel):
    up: bool


@router.get("/api/gestures")
async def list_gestures(
    agent: str = Query(min_length=1, max_length=64),
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    rows = await service.list_for_agent(agent)
    return {"agent": agent, "gestures": [g.to_dict() for g in rows]}


@router.get("/api/gestures/{gesture_id}")
async def get_gesture(
    gesture_id: str,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    g = await service.get(gesture_id, with_score=True)
    if g is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="gesture_not_found")
    return {"gesture": g.to_dict(include_score=True)}


@router.get("/api/gestures/{gesture_id}/clip")
async def get_gesture_clip(
    gesture_id: str,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    result = await service.get_payload(gesture_id)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="gesture_not_found")
    g, payload_b64 = result
    return {
        "clip": {
            "id": g.clip_ref,
            "name": g.name,
            "source_vrm": "generated.vrm",
            "duration_s": g.duration_s,
            "fps": g.fps,
            "frame_count": g.frame_count,
        },
        "payload_gz_b64": payload_b64,
    }


@router.post("/api/gestures/generate", status_code=http_status.HTTP_201_CREATED)
async def generate_gesture(
    req: GenerateRequest,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    err = validate_trigger(req.kind, req.value)
    if err:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=err)

    # Compose on the server-side LLM config when one exists; otherwise
    # the procedural fallback still yields a valid gesture.
    client = None
    model = ""
    try:
        from autonoma.api import _build_admin_llm_config
        from autonoma.llm import create_llm_client

        cfg = _build_admin_llm_config()
        if cfg is not None:
            client = create_llm_client(cfg)
            model = settings.gesture_gen_model or cfg.resolved_worker_model()
    except Exception:  # pragma: no cover — defensive; fallback path below
        logger.exception("[gestures] could not build LLM client; using fallback")

    ctx = CharacterContext(
        name=req.agent,
        role=req.role,
        species=req.species,
        traits=req.traits,
        catchphrase=req.catchphrase,
        mood=req.mood,
    )
    g = await service.ensure_gesture(
        agent_name=req.agent,
        kind=req.kind,
        value=req.value,
        ctx=ctx,
        client=client,
        model=model,
        force=req.force,
    )
    return {"gesture": g.to_dict()}


@router.post("/api/gestures/{gesture_id}/vote")
async def vote_gesture(
    gesture_id: str,
    req: VoteRequest,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    key = (user.id, gesture_id)
    if key in _voted:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="already_voted")
    g = await service.vote(gesture_id, up=req.up)
    if g is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="gesture_not_found")
    _voted.add(key)
    return {"gesture": g.to_dict()}
