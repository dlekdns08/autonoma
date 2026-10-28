"""Managed Agents endpoints (admin) + the Anthropic webhook receiver.

GET  /api/managed/status                 — configured? persisted ids
POST /api/managed/deployments            — {name, goal, cron, timezone, budget_usd}
POST /api/managed/dreams                 — {character_uuid, session_ids, instructions}
POST /api/managed/memory/sync            — {character_uuid, name, memoir}
POST /api/managed/webhook                — Anthropic → Autonoma (HMAC-verified)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import status as http_status
from pydantic import BaseModel, Field

from autonoma.auth import User, require_admin
from autonoma.event_bus import bus
from autonoma.managed import client as managed_client
from autonoma.managed import deployments, dreams, memory, registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["managed"])

# Webhook deliveries can repeat; ids are per event, so dedupe on them.
_seen_webhook_ids: set[str] = set()


def _require_configured() -> None:
    if not managed_client.is_configured():
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="managed_agents_not_configured",
        )


class DeploymentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=4000)
    cron: str = Field(min_length=9, max_length=64)
    timezone: str = "UTC"
    budget_usd: float | None = None


class DreamRequest(BaseModel):
    character_uuid: str = Field(min_length=1, max_length=64)
    name: str = ""
    session_ids: list[str] = Field(min_length=1, max_length=100)
    instructions: str | None = None


class MemorySyncRequest(BaseModel):
    character_uuid: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    memoir: str = Field(min_length=1, max_length=100_000)


@router.get("/api/managed/status")
async def managed_status(_user: User = Depends(require_admin)) -> dict[str, Any]:
    data = registry.load()
    return {
        "configured": managed_client.is_configured(),
        "environment_id": data.get("environment_id"),
        "agent_id": data.get("agent_id"),
        "memory_stores": data.get("memory_stores", {}),
        "deployments": data.get("deployments", []),
    }


@router.post("/api/managed/deployments", status_code=http_status.HTTP_201_CREATED)
async def create_deployment(
    req: DeploymentRequest, _user: User = Depends(require_admin)
) -> dict[str, Any]:
    _require_configured()
    dep = await deployments.create_scheduled_run(
        managed_client.get_client(),
        name=req.name,
        goal=req.goal,
        cron=req.cron,
        timezone=req.timezone,
        budget_usd=req.budget_usd,
    )
    schedule = getattr(dep, "schedule", None)
    return {
        "id": dep.id,
        "status": getattr(dep, "status", ""),
        "upcoming_runs_at": list(getattr(schedule, "upcoming_runs_at", []) or []),
    }


@router.post("/api/managed/dreams", status_code=http_status.HTTP_202_ACCEPTED)
async def start_dream(req: DreamRequest, _user: User = Depends(require_admin)) -> dict[str, Any]:
    _require_configured()
    client = managed_client.get_client()
    store_id = await registry.ensure_memory_store(
        client, req.character_uuid, req.name or req.character_uuid
    )
    dream = await dreams.run_dream(
        client,
        memory_store_id=store_id,
        session_ids=req.session_ids,
        instructions=req.instructions,
        timeout_s=0,  # return immediately; the caller polls
    )
    return {"id": dream.id, "status": getattr(dream, "status", ""), "memory_store_id": store_id}


@router.post("/api/managed/memory/sync")
async def sync_memory(
    req: MemorySyncRequest, _user: User = Depends(require_admin)
) -> dict[str, Any]:
    _require_configured()
    client = managed_client.get_client()
    store_id = await registry.ensure_memory_store(client, req.character_uuid, req.name)
    memory_id = await memory.sync_memoir(client, store_id, req.memoir)
    return {"memory_store_id": store_id, "memory_id": memory_id}


@router.post("/api/managed/webhook")
async def anthropic_webhook(request: Request) -> Response:
    """Anthropic → Autonoma. Verified with the SDK's ``webhooks.unwrap`` (reads
    ``ANTHROPIC_WEBHOOK_SIGNING_KEY``); thin payloads are re-emitted on the
    bus as ``managed.<data.type>`` for the stage/log."""
    raw = (await request.body()).decode("utf-8")
    try:
        event = managed_client.get_client().beta.webhooks.unwrap(raw, headers=dict(request.headers))
    except Exception:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid_signature"
        )
    event_id = str(getattr(event, "id", ""))
    if event_id in _seen_webhook_ids:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    if event_id:
        _seen_webhook_ids.add(event_id)
        if len(_seen_webhook_ids) > 5000:
            _seen_webhook_ids.clear()
    data = getattr(event, "data", None)
    await bus.emit(
        "managed.webhook",
        kind=str(getattr(data, "type", "")),
        resource_id=str(getattr(data, "id", "")),
        event_id=event_id,
    )
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
