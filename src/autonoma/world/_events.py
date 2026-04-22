"""Shared event-bus helper used by every world submodule.

Kept in its own file so submodules can import it without pulling in the
rest of ``autonoma.world`` (which would be a circular import). The bus
itself is imported lazily for the same reason.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _fire_event(event_name: str, **kwargs: Any) -> None:
    """Fire-and-forget bus.emit from sync code.

    Uses ``asyncio.get_event_loop().create_task()`` when a running loop
    is available. Silently skips emission when there is no loop (tests,
    CLI without an event loop). Import is deferred to avoid a circular
    import between world code and ``event_bus`` at module load time.
    """
    try:
        from autonoma.event_bus import bus  # noqa: PLC0415
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(bus.emit(event_name, **kwargs))
    except Exception as _exc:
        logger.debug(f"[world] Could not emit '{event_name}': {_exc}")
