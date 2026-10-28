"""Persisted ids for the Managed Agents resources Autonoma creates.

Agents and environments are create-once / run-many objects, so their
ids live in ``data_dir/managed_agents.json`` rather than being
re-created in the request path. Memory stores are keyed by character
uuid so a character keeps its memories across runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from autonoma.config import settings

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()


def _path() -> Path:
    return Path(settings.data_dir) / "managed_agents.json"


def load() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _worker_system_prompt() -> str:
    return (
        "You are a worker on Autonoma, an autonomous software team whose work is "
        "shown on a live stage. You build exactly what the task asks for inside the "
        "session workspace, verify it (run tests or the program), and write every "
        "deliverable file under /mnt/session/outputs/ keeping the project-relative "
        "path (e.g. /mnt/session/outputs/src/app.py). Finish with a 3-6 line summary."
    )


async def ensure_environment(client: Any) -> str:
    async with _lock:
        data = load()
        if data.get("environment_id"):
            return data["environment_id"]
        env = await client.beta.environments.create(
            name=settings.managed_environment_name,
            config={
                "type": "cloud",
                "networking": {
                    "type": settings.managed_networking,
                    "allow_package_managers": True,
                    "allow_mcp_servers": False,
                },
            },
        )
        data["environment_id"] = env.id
        save(data)
        logger.info("[managed] created environment %s", env.id)
        return env.id


async def ensure_agent(client: Any) -> tuple[str, Any]:
    """Return ``(agent_id, version)`` for the worker agent, creating it once."""
    async with _lock:
        data = load()
        if data.get("agent_id"):
            return data["agent_id"], data.get("agent_version")
        model = settings.deep_work_model or settings.model
        agent = await client.beta.agents.create(
            name=settings.managed_agent_name,
            model={"id": model, "effort": settings.deep_work_effort or "medium"},
            system=_worker_system_prompt(),
            tools=[
                {
                    "type": "agent_toolset_20260401",
                    # The workspace is the sandbox; unattended runs must
                    # never pause on a permission prompt.
                    "default_config": {
                        "enabled": True,
                        "permission_policy": {"type": "always_allow"},
                    },
                    "configs": [
                        {"name": "web_fetch", "enabled": False},
                        {"name": "web_search", "enabled": False},
                    ],
                }
            ],
        )
        data["agent_id"] = agent.id
        data["agent_version"] = getattr(agent, "version", None)
        save(data)
        logger.info("[managed] created agent %s", agent.id)
        return agent.id, data["agent_version"]


async def ensure_memory_store(client: Any, character_uuid: str, name: str) -> str:
    async with _lock:
        data = load()
        stores: dict[str, str] = data.setdefault("memory_stores", {})
        if character_uuid in stores:
            return stores[character_uuid]
        store = await client.beta.memory_stores.create(
            name=f"Autonoma character: {name}"[:128],
            description=(
                f"Long-term memories of {name}, an Autonoma character: what they built, "
                "who they worked with, lessons learned, and their evolving personality."
            ),
        )
        stores[character_uuid] = store.id
        save(data)
        logger.info("[managed] created memory store %s for %s", store.id, name)
        return store.id
