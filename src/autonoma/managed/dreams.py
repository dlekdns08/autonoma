"""Dreams — consolidate sessions into a character's memory store."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from autonoma.config import settings
from autonoma.managed.client import DREAM_BETA

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = (
    "You are consolidating the memories of a character on an autonomous software "
    "team. From these sessions, keep what shapes the character: skills gained, "
    "teammates and how they got along, mistakes and the lessons drawn, moments "
    "worth retelling. Write in the first person, concise, grouped by theme. Drop "
    "transient tool output and file contents."
)

TERMINAL = {"completed", "failed", "canceled"}


async def run_dream(
    client: Any,
    *,
    memory_store_id: str,
    session_ids: list[str],
    model: str | None = None,
    instructions: str | None = None,
    poll_s: float = 5.0,
    timeout_s: float = 900.0,
) -> Any:
    """Create a dream that updates ``memory_store_id`` from ``session_ids``
    and wait for it to finish. Returns the final dream object."""
    ids = list(dict.fromkeys(session_ids))[:100]
    if not ids:
        raise ValueError("no sessions to dream about")
    dream = await client.beta.dreams.create(
        inputs=[
            {"type": "memory_store", "memory_store_id": memory_store_id},
            {"type": "sessions", "session_ids": ids},
        ],
        model=model or settings.managed_dream_model,
        instructions=(instructions or DEFAULT_INSTRUCTIONS)[:4096],
        output_behavior={"type": "update_existing"},
        betas=[DREAM_BETA],
    )
    deadline = asyncio.get_running_loop().time() + timeout_s
    while getattr(dream, "status", "") not in TERMINAL:
        if asyncio.get_running_loop().time() > deadline:
            logger.warning("[managed] dream %s still %s after timeout", dream.id, dream.status)
            break
        await asyncio.sleep(poll_s)
        dream = await client.beta.dreams.retrieve(dream.id, betas=[DREAM_BETA])
    return dream
