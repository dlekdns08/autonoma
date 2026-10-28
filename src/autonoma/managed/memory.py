"""Character memoir ↔ Managed Agents memory store."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MEMOIR_PATH = "/memoir.md"


async def sync_memoir(client: Any, store_id: str, memoir_text: str) -> str:
    """Write the memoir to ``/memoir.md`` in the store (create or update)."""
    try:
        memory = await client.beta.memory_stores.memories.create(
            store_id, path=MEMOIR_PATH, content=memoir_text
        )
        return memory.id
    except Exception as exc:  # 409 memory_path_conflict → update in place
        if "409" not in str(exc) and "conflict" not in str(exc).lower():
            raise
    page = await client.beta.memory_stores.memories.list(store_id)
    for m in getattr(page, "data", []) or []:
        if getattr(m, "path", "") == MEMOIR_PATH:
            await client.beta.memory_stores.memories.update(
                m.id, memory_store_id=store_id, content=memoir_text
            )
            return m.id
    raise RuntimeError("memoir memory not found after conflict")


async def read_memories(client: Any, store_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    page = await client.beta.memory_stores.memories.list(store_id)
    out = []
    for m in (getattr(page, "data", []) or [])[:limit]:
        out.append(
            {"id": m.id, "path": getattr(m, "path", ""), "content": getattr(m, "content", "")}
        )
    return out
