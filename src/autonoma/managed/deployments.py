"""Scheduled deployments — autonomous runs on a cron schedule."""

from __future__ import annotations

import logging
from typing import Any

from autonoma.config import settings
from autonoma.managed import registry

logger = logging.getLogger(__name__)


async def create_scheduled_run(
    client: Any,
    *,
    name: str,
    goal: str,
    cron: str,
    timezone: str = "UTC",
    budget_usd: float | None = None,
) -> Any:
    """Create a deployment that builds ``goal`` on ``cron`` using the worker
    agent. Each firing is a fresh session; outputs land in the session's
    ``/mnt/session/outputs`` (fetch them with ``files.list(scope_id=...)``)."""
    env_id = await registry.ensure_environment(client)
    agent_id, _ = await registry.ensure_agent(client)
    kwargs: dict[str, Any] = dict(
        name=name[:128],
        agent=agent_id,
        environment_id=env_id,
        initial_events=[
            {"type": "user.message", "content": [{"type": "text", "text": goal}]},
        ],
        schedule={"type": "cron", "expression": cron, "timezone": timezone},
    )
    budget = settings.deep_work_max_budget_usd if budget_usd is None else budget_usd
    if budget:
        kwargs["budget"] = {
            "type": "limit",
            "max_list_cost": {"amount": str(max(1, int(round(budget * 100)))), "currency": "USD"},
        }
    deployment = await client.beta.deployments.create(**kwargs)
    data = registry.load()
    data.setdefault("deployments", []).append(
        {"id": deployment.id, "name": name, "cron": cron, "timezone": timezone}
    )
    registry.save(data)
    logger.info("[managed] created deployment %s (%s)", deployment.id, cron)
    return deployment
