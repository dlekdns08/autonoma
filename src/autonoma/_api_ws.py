"""WebSocket connection manager and event serialization helpers.

Extracted from ``autonoma.api`` to keep the main module focused on
routing / lifespan / handlers. The symbols are re-exported from
``autonoma.api`` so existing import paths continue to work.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks live WebSocket connections and provides routed delivery.

    Events are never fanned out to all connections anymore — each event
    belongs to a single session and is sent only to that session's ws.
    ``broadcast`` is kept for the rare system-wide message but should be
    used sparingly now that swarms are per-session.
    """

    def __init__(self) -> None:
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.append(ws)
        logger.info(f"[WS] Client connected ({len(self.connections)} total)")

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.connections:
            self.connections.remove(ws)
        logger.info(f"[WS] Client disconnected ({len(self.connections)} total)")

    async def send_to_ws(self, ws: WebSocket, event_type: str, data: dict[str, Any]) -> bool:
        """Send a single event to one websocket. Returns False on failure."""
        try:
            await ws.send_text(json.dumps({"event": event_type, "data": _serialize(data)}))
            return True
        except Exception:
            logger.exception("[ws] send_to_ws failed for event=%s", event_type)
            return False

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan out an event to every live connection (system-wide only)."""
        message = _make_event_message(event_type, data)
        disconnected: list[WebSocket] = []
        # Snapshot the connection list — a concurrent disconnect must not
        # mutate the list we're iterating over (TOCTOU).
        for ws in list(self.connections):
            try:
                await ws.send_text(message)
            except Exception as exc:
                logger.warning(
                    "[ws] broadcast send failed for event=%s; dropping client: %s",
                    event_type,
                    exc,
                )
                disconnected.append(ws)
        for ws in disconnected:
            if ws in self.connections:
                self.connections.remove(ws)


manager = ConnectionManager()


def _serialize(obj: Any) -> Any:
    """Make event data JSON-serializable."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if hasattr(obj, "value"):  # Enum
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):  # dataclass
        return {k: _serialize(getattr(obj, k)) for k in obj.__dataclass_fields__}
    return obj


def _make_event_message(event_type: str, data: dict[str, Any]) -> str:
    """Serialize an event to a JSON string ready to send over WebSocket."""
    return json.dumps({"event": event_type, "data": _serialize(data)})
