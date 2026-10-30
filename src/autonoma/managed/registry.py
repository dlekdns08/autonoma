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


_LEAD_DELEGATION = (
    " When the task splits into independent parts (for example a module and its "
    "docs), hand each self-contained part to a copy of yourself, one task per copy. "
    "Hand running and fixing up the test suite to Test runner with the exact command. "
    "Keep integration, verification and the final summary for yourself."
)


async def _ensure_test_runner(client: Any, data: dict[str, Any]) -> str:
    if data.get("test_runner_id"):
        return data["test_runner_id"]
    runner = await client.beta.agents.create(
        name="Test runner",
        description=(
            "Low-cost agent that runs a project's tests or program and reports failures "
            "with output and file:line. Give it the command and what should pass."
        ),
        model=settings.managed_worker_model,
        system=(
            "Run exactly the command you are given in the shared workspace, read the "
            "failures, and report pass/fail with the relevant output and file:line for "
            "each failure. Fix only test files; report problems in source code instead."
        ),
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
                "configs": [
                    {"name": "web_fetch", "enabled": False},
                    {"name": "web_search", "enabled": False},
                ],
            }
        ],
    )
    data["test_runner_id"] = runner.id
    return runner.id


async def ensure_agent(client: Any) -> tuple[str, Any]:
    """Return ``(agent_id, version)`` for the worker agent, creating it once.

    With ``managed_multiagent`` the worker is a lead with a roster
    (copies of itself + a cheaper Test runner); it is stored under its own
    key so toggling the flag never reuses the wrong agent.
    """
    multi = bool(settings.managed_multiagent)
    id_key, ver_key = (
        ("lead_agent_id", "lead_agent_version") if multi else ("agent_id", "agent_version")
    )
    async with _lock:
        data = load()
        if data.get(id_key):
            return data[id_key], data.get(ver_key)
        model = settings.deep_work_model or settings.model
        extra: dict[str, Any] = {}
        system = _worker_system_prompt()
        if multi:
            runner_id = await _ensure_test_runner(client, data)
            system += _LEAD_DELEGATION
            extra["multiagent"] = {
                "type": "multiagent_20261001",
                "subagents": {
                    "type": "enabled",
                    "predefined_agents": [runner_id, {"type": "self"}],
                    "inline_agents": {"type": "disabled"},
                },
                "workflows": (
                    {
                        "type": "enabled",
                        "predefined_agents": [runner_id],
                        "inline_agents": {"type": "disabled"},
                    }
                    if settings.managed_workflows
                    else {"type": "disabled"}
                ),
            }
        agent = await client.beta.agents.create(
            name=settings.managed_agent_name + (" (lead)" if multi else ""),
            model={"id": model, "effort": settings.deep_work_effort or "medium"},
            system=system,
            **extra,
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
        data[id_key] = agent.id
        data[ver_key] = getattr(agent, "version", None)
        save(data)
        logger.info("[managed] created %s agent %s", "lead" if multi else "worker", agent.id)
        return agent.id, data[ver_key]


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
