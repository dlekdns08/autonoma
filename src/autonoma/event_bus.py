"""Event bus with wildcard support for agent coordination and TUI."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

Handler = Callable[..., Coroutine[Any, Any, None]]
# A "tap" sees every event with its name + data payload. Used by the
# tracing recorder to persist the full event stream without forcing each
# subscriber to know about wildcard semantics. Tap handlers receive
# ``(event_name, data_dict)`` and must not mutate ``data_dict``.
TapHandler = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]

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
        self._taps: list[TapHandler] = []

    def on(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Handler) -> None:
        self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def tap(self, handler: TapHandler) -> None:
        """Subscribe ``handler`` to every emitted event.

        Tap handlers see ``(event_name, data_dict)`` and run after the
        normal subscribers. Used by the tracing recorder so observers can
        persist the full event stream without each emitter knowing about
        it. Errors are logged but never propagated.
        """
        self._taps.append(handler)

    def untap(self, handler: TapHandler) -> None:
        self._taps = [h for h in self._taps if h is not handler]

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
            # the whole batch — but log at ERROR with the full traceback so
            # operators can see *why* a spawn / cleanup / relationship handler
            # died. A silent failure here used to mean a crashed
            # ``_on_spawn_request`` left the requester waiting forever with
            # no ``agent.spawn_failed`` signal.
            for handler, result in zip(handlers, results):
                if isinstance(result, Exception):
                    logger.error(
                        "Event handler failed: event=%s handler=%s",
                        event,
                        _handler_name(handler),
                        exc_info=result,
                    )
        if self._taps:
            tap_results = await asyncio.gather(
                *(t(event, data) for t in self._taps), return_exceptions=True
            )
            for tap, result in zip(self._taps, tap_results):
                if isinstance(result, Exception):
                    logger.error(
                        "Event tap failed: event=%s tap=%s",
                        event,
                        _handler_name(tap),
                        exc_info=result,
                    )


bus = EventBus()
