"""Event bus with wildcard support for agent coordination and TUI."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

Handler = Callable[..., Coroutine[Any, Any, None]]


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
            await asyncio.gather(
                *(h(**data) for h in handlers), return_exceptions=True
            )


bus = EventBus()
