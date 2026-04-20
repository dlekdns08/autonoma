"""WebSocket API server — bridges the event bus to the Next.js frontend.

Run with:  uv run uvicorn autonoma.api:app --reload --port 8000

Authentication flow
───────────────────
Every new WebSocket connection starts unauthenticated.  Before a swarm can
be started the client must send one of:

  {"command": "authenticate", "type": "admin", "password": "<admin_password>"}
      → grants access to the server-configured API key.

  {"command": "authenticate", "type": "user",
   "provider": "anthropic"|"openai"|"vllm",
   "api_key": "...",
   "model":   "...",
   "base_url": "..."   ← required only for vllm}
      → uses the client's own API key.

On success the server replies with {"event": "auth.success", ...}.
On failure  the server replies with {"event": "auth.failed",  ...}.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import io
import zipfile

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.llm import LLMConfig

logger = logging.getLogger(__name__)

# ── Per-session state ────────────────────────────────────────────────────
# Each WebSocket connection owns its own swarm/project/task so that
# concurrent users don't share state or see each other's events.
# The session id we hand out to clients is the integer id of the
# WebSocket object; it is unique for the lifetime of the connection.


@dataclass
class SessionState:
    ws: WebSocket
    session_id: int
    llm_config: LLMConfig | None = None
    swarm: Any = None          # AgentSwarm instance
    project: Any = None        # ProjectState instance
    task: asyncio.Task | None = None  # background swarm runner


_sessions: dict[int, SessionState] = {}

# Carries the current session id down through every awaitable that runs
# inside a swarm task, so that bus handlers can send events only to the
# originating client rather than broadcasting to every connection.
_current_session_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "autonoma_current_session_id", default=None
)


def _build_admin_llm_config() -> LLMConfig | None:
    """Construct the server-side LLMConfig from settings (admin use only)."""
    provider = settings.provider
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            return None
        return LLMConfig(
            provider="anthropic",
            api_key=settings.anthropic_api_key,
            model=settings.model,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            return None
        return LLMConfig(
            provider="openai",
            api_key=settings.openai_api_key,
            model=settings.model,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    if provider == "vllm":
        if not settings.vllm_base_url:
            return None
        return LLMConfig(
            provider="vllm",
            api_key=settings.vllm_api_key,
            model=settings.model,
            base_url=settings.vllm_base_url,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    return None


# ── Connection Manager ────────────────────────────────────────────────────

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

    async def send_to_ws(
        self, ws: WebSocket, event_type: str, data: dict[str, Any]
    ) -> bool:
        """Send a single event to one websocket. Returns False on failure."""
        try:
            await ws.send_text(
                json.dumps({"event": event_type, "data": _serialize(data)})
            )
            return True
        except Exception:
            return False

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan out an event to every live connection (system-wide only)."""
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
    "fortune.fulfilled",
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
    "swarm.diagnostic",
]


def _make_handler(event_type: str):
    """Build a bus handler that routes events to the session that emitted them.

    The session id is carried via ``_current_session_id`` (a ContextVar set
    at the top of ``_run_swarm``). Because asyncio propagates context into
    awaitables and tasks it creates, every ``bus.emit`` that originates
    inside a swarm task inherits the right session id automatically.
    """

    async def handler(**kwargs: Any) -> None:
        sid = _current_session_id.get()
        if sid is None:
            # Emitted outside a swarm task (CLI/TUI/tests). Preserve the old
            # broadcast semantics for backward compatibility.
            await manager.broadcast(event_type, kwargs)
            return
        session = _sessions.get(sid)
        if session is None:
            return
        ok = await manager.send_to_ws(session.ws, event_type, kwargs)
        if not ok:
            # ws is gone — drop this session so we stop trying to send.
            _sessions.pop(sid, None)

    return handler


_handlers: dict[str, Any] = {}


def _register_event_bridge() -> None:
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

async def _run_swarm(
    session_id: int,
    goal: str,
    max_rounds: int = 30,
    llm_config: LLMConfig | None = None,
) -> None:
    """Run the swarm for a single session in the background."""
    # Tag every awaitable spawned from this task with our session id so bus
    # events get delivered only to this session's WebSocket.
    _current_session_id.set(session_id)

    from autonoma.agents.swarm import AgentSwarm
    from autonoma.models import ProjectState
    from autonoma.workspace import WorkspaceManager

    session = _sessions.get(session_id)
    if session is None:
        logger.warning(
            f"[Swarm] session {session_id} vanished before run could start"
        )
        return

    name = goal.lower().replace(" ", "-")[:40]
    project = ProjectState(name=name, description=goal)
    swarm = AgentSwarm(llm_config=llm_config)

    session.swarm = swarm
    session.project = project

    try:
        await swarm.initialize(project)

        for agent_name, agent in swarm.agents.items():
            if not any(a.name == agent_name for a in project.agents):
                project.agents.append(agent.persona)

        await swarm.run(project, max_rounds=max_rounds)

        if project.files:
            workspace = WorkspaceManager()
            out = settings.output_dir / name
            await workspace.write_all(project)
            logger.info(f"[Swarm:{session_id}] Files written to {out}")

    except asyncio.CancelledError:
        swarm.stop()
        logger.info(f"[Swarm:{session_id}] Cancelled")
        raise
    except Exception as e:
        logger.exception(f"[Swarm:{session_id}] Error: {e}")
        # Send the failure only to the owner — never to other sessions.
        sess = _sessions.get(session_id)
        if sess is not None:
            await manager.send_to_ws(sess.ws, "swarm.error", {"error": str(e)})


# ── Swarm State Snapshot ──────────────────────────────────────────────────

def _get_snapshot(session_id: int | None) -> dict[str, Any]:
    """Build the current swarm snapshot for a single session."""
    try:
        session = _sessions.get(session_id) if session_id is not None else None
        if session is None or session.swarm is None or session.project is None:
            return {"status": "idle"}

        swarm = session.swarm
        project = session.project

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

        boss_data: dict[str, Any] | None = None
        if hasattr(swarm, "boss_arena") and swarm.boss_arena.current_boss:
            b = swarm.boss_arena.current_boss
            if b.phase.value in ("appearing", "fighting"):
                boss_data = {
                    "name": b.name,
                    "species": b.species,
                    "level": b.level,
                    "hp": b.hp,
                    "max_hp": b.max_hp,
                    "x": 52.0,
                    "y": 54.0,
                }

        cookies: list[dict[str, Any]] = []
        if hasattr(swarm, "fortune_jar"):
            for name, cookie in swarm.fortune_jar.active_fortunes.items():
                cookies.append({
                    "recipient": name,
                    "fortune": cookie.fortune,
                    "bonus_xp": cookie.bonus_xp,
                })

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
            "boss": boss_data,
            "cookies": cookies,
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
    global _swarm_task, _swarm, _project
    ws_id = id(ws)

    await manager.connect(ws)
    try:
        # ── Announce auth requirements on connect ──
        has_admin = bool(settings.admin_password)
        await ws.send_text(json.dumps({
            "event": "auth.status",
            "data": {
                "requires_auth": True,
                "has_admin": has_admin,
                # Tell the UI which provider the server uses so it can show
                # the correct model name when admin logs in.
                "server_provider": settings.provider if has_admin else None,
                "server_model": settings.model if has_admin else None,
            },
        }))

        # ── Initial state snapshot ──
        snapshot = _get_snapshot()
        await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))

        # ── Command loop ──
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            cmd = msg.get("command")

            # ── authenticate ──────────────────────────────────────────
            if cmd == "authenticate":
                auth_type = msg.get("type")

                if auth_type == "admin":
                    if not settings.admin_password:
                        await ws.send_text(json.dumps({
                            "event": "auth.failed",
                            "data": {"message": "관리자 계정이 서버에 설정되어 있지 않습니다."},
                        }))
                    elif msg.get("password") != settings.admin_password:
                        await ws.send_text(json.dumps({
                            "event": "auth.failed",
                            "data": {"message": "관리자 비밀번호가 올바르지 않습니다."},
                        }))
                    else:
                        llm_cfg = _build_admin_llm_config()
                        if llm_cfg is None:
                            await ws.send_text(json.dumps({
                                "event": "auth.failed",
                                "data": {"message": "서버에 API 키가 설정되어 있지 않습니다. 관리자에게 문의하세요."},
                            }))
                        else:
                            _auth_store[ws_id] = llm_cfg
                            logger.info(f"[WS:{ws_id}] Admin authenticated (provider={llm_cfg.provider})")
                            await ws.send_text(json.dumps({
                                "event": "auth.success",
                                "data": {
                                    "is_admin": True,
                                    "provider": llm_cfg.provider,
                                    "model": llm_cfg.model,
                                },
                            }))

                elif auth_type == "user":
                    provider = msg.get("provider", "anthropic")
                    api_key = (msg.get("api_key") or "").strip()
                    model = (msg.get("model") or "").strip()
                    base_url = (msg.get("base_url") or "").strip()

                    error: str | None = None
                    if provider not in ("anthropic", "openai", "vllm"):
                        error = f"지원하지 않는 프로바이더입니다: {provider}"
                    elif not model:
                        error = "모델명을 입력해주세요."
                    elif provider != "vllm" and not api_key:
                        error = "API 키를 입력해주세요."
                    elif provider == "vllm" and not base_url:
                        error = "vLLM 서버 URL을 입력해주세요."

                    if error:
                        await ws.send_text(json.dumps({
                            "event": "auth.failed",
                            "data": {"message": error},
                        }))
                    else:
                        llm_cfg = LLMConfig(
                            provider=provider,  # type: ignore[arg-type]
                            api_key=api_key,
                            model=model,
                            base_url=base_url,
                            max_tokens=settings.max_tokens,
                            temperature=settings.temperature,
                        )
                        _auth_store[ws_id] = llm_cfg
                        logger.info(f"[WS:{ws_id}] User authenticated (provider={provider}, model={model})")
                        await ws.send_text(json.dumps({
                            "event": "auth.success",
                            "data": {
                                "is_admin": False,
                                "provider": provider,
                                "model": model,
                            },
                        }))
                else:
                    await ws.send_text(json.dumps({
                        "event": "auth.failed",
                        "data": {"message": "인증 방식이 올바르지 않습니다 (admin 또는 user)."},
                    }))

            # ── get_snapshot ──────────────────────────────────────────
            elif cmd == "get_snapshot":
                snapshot = _get_snapshot()
                await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))

            # ── start ─────────────────────────────────────────────────
            elif cmd == "start":
                llm_cfg = _auth_store.get(ws_id)
                if llm_cfg is None:
                    await ws.send_text(json.dumps({
                        "event": "auth.required",
                        "data": {"message": "스웜을 시작하려면 먼저 로그인해주세요."},
                    }))
                    continue

                goal = msg.get("goal", "").strip()
                if not goal:
                    await ws.send_text(json.dumps({
                        "event": "error",
                        "data": {"message": "Goal is required"},
                    }))
                elif _swarm_task and not _swarm_task.done():
                    await ws.send_text(json.dumps({
                        "event": "error",
                        "data": {"message": "Swarm is already running"},
                    }))
                else:
                    max_rounds = msg.get("max_rounds", 30)
                    _swarm_task = asyncio.create_task(
                        _run_swarm(goal, max_rounds, llm_config=llm_cfg)
                    )
                    await ws.send_text(json.dumps({
                        "event": "swarm.starting",
                        "data": {"goal": goal},
                    }))

            # ── stop ──────────────────────────────────────────────────
            elif cmd == "stop":
                if _swarm_task and not _swarm_task.done():
                    _swarm_task.cancel()
                    await ws.send_text(json.dumps({"event": "swarm.stopped", "data": {}}))

            # ── reset ─────────────────────────────────────────────────
            # Bring the server back to the idle state so the user can
            # start a brand-new project from the start screen.
            elif cmd == "reset":
                if _swarm_task and not _swarm_task.done():
                    _swarm_task.cancel()
                    try:
                        await _swarm_task
                    except (asyncio.CancelledError, Exception):
                        pass
                _swarm = None
                _project = None
                _swarm_task = None
                logger.info(f"[WS:{ws_id}] Project reset — returning to idle")
                await manager.broadcast("swarm.reset", {})
                await manager.broadcast("snapshot", _get_snapshot())

            # ── message ───────────────────────────────────────────────
            elif cmd == "message":
                text = msg.get("text", "")
                target = msg.get("target")
                if text.startswith("/cheer"):
                    await bus.emit("world.event", title="The audience cheers wildly! Agents feel inspired!")
                elif text.startswith("/status") or text.startswith("/snapshot"):
                    snapshot = _get_snapshot()
                    await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
                elif text.startswith("/stop"):
                    if _swarm_task and not _swarm_task.done():
                        _swarm_task.cancel()
                else:
                    await manager.broadcast(
                        "chat.message",
                        {"text": text, "source": "user", "target": target or ""},
                    )
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
    if _project is None:
        raise HTTPException(status_code=404, detail="No active project")
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
    if _project is None or not _project.files:
        raise HTTPException(status_code=404, detail="No files to download")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for artifact in _project.files:
            safe_path = "/".join(
                seg for seg in artifact.path.split("/") if seg and seg != ".."
            )
            if not safe_path:
                continue
            zf.writestr(safe_path, artifact.content)
    project_name = _project.name or "autonoma-project"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_name}.zip"'},
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "connections": len(manager.connections),
        "swarm_running": _swarm_task is not None and not _swarm_task.done(),
    }


# ── Model discovery ───────────────────────────────────────────────────────


@app.get("/api/models/{provider}")
async def list_models_server(provider: str):
    """Return models available via the server-configured admin key.

    Used by the Admin tab to show what the server-side key can actually reach.
    Unauthenticated from the network's perspective, but only exposes the
    server's own key indirectly (we only return model IDs, never the key).
    """
    from autonoma.model_catalog import list_models

    if provider not in ("anthropic", "openai", "vllm"):
        raise HTTPException(status_code=400, detail="unknown provider")

    api_key = ""
    base_url = ""
    if provider == "anthropic":
        api_key = settings.anthropic_api_key
    elif provider == "openai":
        api_key = settings.openai_api_key
    elif provider == "vllm":
        api_key = settings.vllm_api_key
        base_url = settings.vllm_base_url

    models, is_live = await asyncio.to_thread(list_models, provider, api_key, base_url)
    return {"provider": provider, "is_live": is_live, "models": models}


@app.post("/api/models")
async def list_models_with_key(payload: dict[str, Any]):
    """Return models for a user-supplied key (used by the 'User API key' tab).

    Request body:
        {"provider": "anthropic"|"openai"|"vllm", "api_key": "...", "base_url": "..."}
    """
    from autonoma.model_catalog import list_models

    provider = (payload.get("provider") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    base_url = (payload.get("base_url") or "").strip()

    if provider not in ("anthropic", "openai", "vllm"):
        raise HTTPException(status_code=400, detail="unknown provider")

    models, is_live = await asyncio.to_thread(list_models, provider, api_key, base_url)
    return {"provider": provider, "is_live": is_live, "models": models}
