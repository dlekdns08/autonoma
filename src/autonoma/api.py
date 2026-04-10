"""WebSocket API server — bridges the event bus to the Next.js frontend.

Run with:  uv run uvicorn autonoma.api:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

# ── Connection Manager ────────────────────────────────────────────────────

class ConnectionManager:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.append(ws)
        logger.info(f"[WS] Client connected ({len(self.connections)} total)")

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.remove(ws)
        logger.info(f"[WS] Client disconnected ({len(self.connections)} total)")

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event to all connected clients."""
        message = json.dumps({"event": event_type, "data": _serialize(data)})
        disconnected: list[WebSocket] = []
        for ws in self.connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.connections.remove(ws)


manager = ConnectionManager()

# ── Serialization Helper ──────────────────────────────────────────────────

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


# ── Event Bridge: bus → WebSocket ─────────────────────────────────────────

# All events we want to forward to the frontend
FORWARDED_EVENTS = [
    "swarm.initializing",
    "swarm.ready",
    "swarm.started",
    "swarm.round",
    "swarm.finished",
    "agent.speech",
    "agent.state",
    "agent.spawned",
    "agent.spawn_failed",
    "agent.error",
    "agent.level_up",
    "agent.dream",
    "file.created",
    "task.assigned",
    "task.completed",
    "director.plan_ready",
    "project.completed",
    "world.event",
    "world.clock",
    "guild.formed",
    "campfire.complete",
    "fortune.given",
    "boss.appeared",
    "boss.defeated",
    "boss.damage",
    "boss.escaped",
    "ghost.appears",
    "quest.expired",
    "relationship.update",
    "letter.sent",
    "trade.completed",
    "quest.completed",
    "quest.assigned",
]


def _make_handler(event_type: str):
    """Create an async handler that forwards bus events to WebSocket."""
    async def handler(**kwargs: Any) -> None:
        await manager.broadcast(event_type, kwargs)
    return handler


_handlers: dict[str, Any] = {}


def _register_event_bridge() -> None:
    """Subscribe to all bus events and forward them via WebSocket."""
    for event_type in FORWARDED_EVENTS:
        handler = _make_handler(event_type)
        _handlers[event_type] = handler
        bus.on(event_type, handler)
    logger.info(f"[WS] Registered {len(FORWARDED_EVENTS)} event bridges")


def _unregister_event_bridge() -> None:
    for event_type, handler in _handlers.items():
        bus.off(event_type, handler)
    _handlers.clear()


# ── Swarm State Snapshot ──────────────────────────────────────────────────

def _get_snapshot() -> dict[str, Any]:
    """Get current swarm state for newly connected clients."""
    try:
        from autonoma.engine.runner import _current_swarm, _current_project
        if not _current_swarm or not _current_project:
            return {"status": "idle"}

        swarm = _current_swarm
        project = _current_project

        agents = []
        for name, agent in swarm.agents.items():
            agent_data: dict[str, Any] = {
                "name": name,
                "emoji": agent.persona.emoji,
                "role": agent.persona.role,
                "color": agent.persona.color,
                "position": {"x": agent.position.x, "y": agent.position.y},
                "state": agent.state.value,
                "mood": agent.mood.value if hasattr(agent, "mood") else "focused",
                "level": agent.stats.level if hasattr(agent, "stats") else 1,
                "xp": agent.stats.xp if hasattr(agent, "stats") else 0,
                "xp_to_next": agent.stats.xp_to_next_level if hasattr(agent, "stats") else 50,
            }
            if hasattr(agent, "bones") and agent.bones:
                evolved_sp, evolved_ej = agent.bones.get_evolved_form(
                    agent.stats.level if hasattr(agent, "stats") else 1
                )
                agent_data["species"] = evolved_sp
                agent_data["species_emoji"] = evolved_ej
                agent_data["rarity"] = agent.bones.rarity
                agent_data["catchphrase"] = agent.bones.catchphrase
                agent_data["traits"] = [t.value for t in agent.bones.traits]
                agent_data["stats"] = agent.bones.stats
            if hasattr(agent, "speech") and agent.speech:
                agent_data["speech"] = agent.speech.text
            agents.append(agent_data)

        tasks = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "assigned_to": t.assigned_to or "",
            }
            for t in project.tasks
        ]

        # Build relationship data from trust matrix
        relationships = []
        if hasattr(swarm, "relationships"):
            trust_matrix = swarm.relationships.trust
            for (a, b), val in trust_matrix.items():
                relationships.append({
                    "from": a,
                    "to": b,
                    "trust": val,
                })

        return {
            "status": "running",
            "project_name": project.name,
            "goal": project.goal,
            "round": swarm._round,
            "agents": agents,
            "tasks": tasks,
            "files": [f.path for f in project.files],
            "sky": swarm.world_clock.sky_line if hasattr(swarm, "world_clock") else "",
            "relationships": relationships,
        }
    except Exception:
        return {"status": "idle"}


# ── FastAPI App ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_event_bridge()
    yield
    _unregister_event_bridge()


app = FastAPI(title="Autonoma API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3478", "http://127.0.0.1:3478",
        "https://autonoma.koala.ai.kr", "http://autonoma.koala.ai.kr",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        # Send initial snapshot
        snapshot = _get_snapshot()
        await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))

        # Keep connection alive, listen for commands
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            cmd = msg.get("command")

            if cmd == "get_snapshot":
                snapshot = _get_snapshot()
                await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))

            elif cmd == "message":
                text = msg.get("text", "")
                # Handle chat commands from the frontend
                if text.startswith("/cheer"):
                    await bus.emit("world.event", title="The audience cheers wildly! Agents feel inspired!")
                elif text.startswith("/status"):
                    snapshot = _get_snapshot()
                    await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
                elif text.startswith("/snapshot"):
                    snapshot = _get_snapshot()
                    await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
                else:
                    # Echo back as a chat message event
                    await manager.broadcast("chat.message", {"text": text, "source": "user"})

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
        manager.disconnect(ws)


@app.get("/api/health")
async def health():
    return {"status": "ok", "connections": len(manager.connections)}
