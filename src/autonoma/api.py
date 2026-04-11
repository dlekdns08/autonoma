"""WebSocket API server — bridges the event bus to the Next.js frontend.

Run with:  uv run uvicorn autonoma.api:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import io
import zipfile

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

# ── Swarm State (managed by the API) ────────────────────────────────────

_swarm: Any = None       # AgentSwarm instance
_project: Any = None     # ProjectState instance
_swarm_task: asyncio.Task | None = None  # Background task running the swarm


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
        if ws in self.connections:
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
            if ws in self.connections:
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
    "human.feedback",
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


# ── Swarm Runner ─────────────────────────────────────────────────────────

async def _run_swarm(goal: str, max_rounds: int = 30) -> None:
    """Run the swarm in the background."""
    global _swarm, _project

    from autonoma.agents.swarm import AgentSwarm
    from autonoma.config import settings
    from autonoma.models import ProjectState
    from autonoma.workspace import WorkspaceManager

    # Create project name from goal
    name = goal.lower().replace(" ", "-")[:40]
    project = ProjectState(name=name, description=goal)
    swarm = AgentSwarm()

    _swarm = swarm
    _project = project

    try:
        await swarm.initialize(project)

        for agent_name, agent in swarm.agents.items():
            if not any(a.name == agent_name for a in project.agents):
                project.agents.append(agent.persona)

        await swarm.run(project, max_rounds=max_rounds)

        # Write files
        if project.files:
            workspace = WorkspaceManager()
            out = settings.output_dir / name
            await workspace.write_all(project)
            logger.info(f"[Swarm] Files written to {out}")

    except asyncio.CancelledError:
        swarm.stop()
        logger.info("[Swarm] Cancelled")
        _swarm = None
        _project = None
    except Exception as e:
        logger.error(f"[Swarm] Error: {e}")
        await manager.broadcast("swarm.error", {"error": str(e)})
        _swarm = None
        _project = None
    # On normal completion we intentionally keep _swarm and _project alive so
    # the frontend can still download files and view the final answer. They
    # will be replaced when the user starts a new run.


# ── Swarm State Snapshot ──────────────────────────────────────────────────

def _get_snapshot() -> dict[str, Any]:
    """Get current swarm state for newly connected clients."""
    try:
        if not _swarm or not _project:
            return {"status": "idle"}

        swarm = _swarm
        project = _project

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

        # Build relationship data from graph
        relationships = []
        if hasattr(swarm, "relationships"):
            for (a, b), rel in swarm.relationships._graph.items():
                if rel.familiarity > 0:
                    relationships.append({
                        "from": a,
                        "to": b,
                        "trust": rel.trust,
                    })

        status = "finished" if (not swarm._running and project.final_answer) else "running"

        return {
            "status": status,
            "project_name": project.name,
            "goal": project.description,
            "round": swarm._round,
            "agents": agents,
            "tasks": tasks,
            "files": [
                {
                    "path": f.path,
                    "size": len(f.content),
                    "description": f.description,
                    "created_by": f.created_by,
                }
                for f in project.files
            ],
            "sky": swarm.world_clock.sky_line if hasattr(swarm, "world_clock") else "",
            "relationships": relationships,
            "final_answer": project.final_answer,
        }
    except Exception as e:
        logger.warning(f"[WS] snapshot failed: {e}")
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
    global _swarm_task

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

            elif cmd == "start":
                goal = msg.get("goal", "").strip()
                if not goal:
                    await ws.send_text(json.dumps({"event": "error", "data": {"message": "Goal is required"}}))
                elif _swarm_task and not _swarm_task.done():
                    await ws.send_text(json.dumps({"event": "error", "data": {"message": "Swarm is already running"}}))
                else:
                    max_rounds = msg.get("max_rounds", 30)
                    _swarm_task = asyncio.create_task(_run_swarm(goal, max_rounds))
                    await ws.send_text(json.dumps({
                        "event": "swarm.starting",
                        "data": {"goal": goal},
                    }))

            elif cmd == "stop":
                if _swarm_task and not _swarm_task.done():
                    _swarm_task.cancel()
                    await ws.send_text(json.dumps({"event": "swarm.stopped", "data": {}}))

            elif cmd == "message":
                text = msg.get("text", "")
                target = msg.get("target")  # optional agent name for direct instruction
                # Handle chat commands from the frontend
                if text.startswith("/cheer"):
                    await bus.emit("world.event", title="The audience cheers wildly! Agents feel inspired!")
                elif text.startswith("/status"):
                    snapshot = _get_snapshot()
                    await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
                elif text.startswith("/snapshot"):
                    snapshot = _get_snapshot()
                    await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
                elif text.startswith("/stop"):
                    if _swarm_task and not _swarm_task.done():
                        _swarm_task.cancel()
                else:
                    # Always echo for the UI log (with target if any)
                    await manager.broadcast(
                        "chat.message",
                        {"text": text, "source": "user", "target": target or ""},
                    )
                    # If a swarm is running, inject the message into the target agent's inbox
                    if (
                        text
                        and _swarm is not None
                        and _swarm_task is not None
                        and not _swarm_task.done()
                    ):
                        try:
                            await _swarm.inject_human_message(text, target=target)
                        except Exception as e:
                            logger.error(f"[WS] Failed to inject human message: {e}")

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
        manager.disconnect(ws)


@app.get("/api/files")
async def list_files() -> dict[str, Any]:
    """Return a listing of files produced by the current/last swarm run."""
    if _project is None:
        return {"project": None, "files": []}
    return {
        "project": _project.name,
        "files": [
            {
                "path": f.path,
                "size": len(f.content),
                "description": f.description,
                "created_by": f.created_by,
            }
            for f in _project.files
        ],
    }


@app.get("/api/files/download")
async def download_file(path: str) -> Response:
    """Download a single generated file by its project-relative path."""
    if _project is None:
        raise HTTPException(status_code=404, detail="No active project")

    # Path traversal defense — match the artifact by exact recorded path.
    artifact = next((f for f in _project.files if f.path == path), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    filename = path.rsplit("/", 1)[-1] or "file"
    return Response(
        content=artifact.content.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/files/zip")
async def download_zip() -> Response:
    """Download all generated files as a single zip archive."""
    if _project is None or not _project.files:
        raise HTTPException(status_code=404, detail="No files to download")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for artifact in _project.files:
            # Defense-in-depth: strip leading slashes and any ".." segments
            safe_path = "/".join(
                seg for seg in artifact.path.split("/")
                if seg and seg != ".."
            )
            if not safe_path:
                continue
            zf.writestr(safe_path, artifact.content)

    project_name = _project.name or "autonoma-project"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{project_name}.zip"'
        },
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "connections": len(manager.connections),
        "swarm_running": _swarm_task is not None and not _swarm_task.done(),
    }
