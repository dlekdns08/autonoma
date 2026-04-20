"""Event bus with wildcard support for agent coordination and TUI."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

Handler = Callable[..., Coroutine[Any, Any, None]]

logger = logging.getLogger(__name__)


def _handler_name(handler: Handler) -> str:
    """Best-effort human-readable name for logging."""
    return (
        getattr(handler, "__qualname__", None)
        or getattr(handler, "__name__", None)
        or repr(handler)
    )


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Handler) -> None:
        self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    async def emit(self, event: str, **data: Any) -> None:
        handlers: list[Handler] = []
        handlers.extend(self._handlers.get(event, []))
        handlers.extend(self._handlers.get("*", []))
        for pattern, hs in self._handlers.items():
            if pattern.endswith(".*") and event.startswith(pattern[:-2]):
                handlers.extend(hs)
        if handlers:
            results = await asyncio.gather(
                *(h(**data) for h in handlers), return_exceptions=True
            )
            # Surface any handler errors instead of silently swallowing them.
            # We keep return_exceptions=True so one bad handler doesn't kill
            # the whole batch — but at least we log what went wrong.
            for handler, result in zip(handlers, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Event handler failed: event=%s handler=%s error=%r",
                        event,
                        _handler_name(handler),
                        result,
                    )


bus = EventBus()
