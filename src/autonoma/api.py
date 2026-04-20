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
    """A single WebSocket subscriber.

    A session is a *viewer*. The actual swarm state — the agents, the
    map, the project — lives on the ``RoomState`` referenced by
    ``room_id``. ``llm_config`` is per-viewer because providers/keys are
    a viewer-level concern (each viewer may bring their own).

    The ``swarm`` / ``project`` / ``task`` attributes below are kept for
    backwards-compat with code that still references them; they're
    proxied to the room when one exists.
    """
    ws: WebSocket
    session_id: int
    llm_config: LLMConfig | None = None
    # Defaults to a "private room of one" with id == session_id; updated
    # when the session creates a real room (via ``start``) or joins one
    # via short code. Always non-None so handlers can rely on it.
    room_id: int = 0
    # Display name for chat. Defaults to a friendly anonymous handle until
    # the viewer sets one via the ``set_name`` command.
    display_name: str = ""
    # ── Compatibility shims (read-through to the room) ──
    @property
    def swarm(self) -> Any:
        room = _rooms.get(self.room_id)
        return room.swarm if room else None

    @swarm.setter
    def swarm(self, value: Any) -> None:
        room = _rooms.get(self.room_id)
        if room is not None:
            room.swarm = value

    @property
    def project(self) -> Any:
        room = _rooms.get(self.room_id)
        return room.project if room else None

    @project.setter
    def project(self, value: Any) -> None:
        room = _rooms.get(self.room_id)
        if room is not None:
            room.project = value

    @property
    def task(self) -> asyncio.Task | None:
        room = _rooms.get(self.room_id)
        return room.task if room else None

    @task.setter
    def task(self, value: asyncio.Task | None) -> None:
        room = _rooms.get(self.room_id)
        if room is not None:
            room.task = value


@dataclass
class RoomState:
    """A live swarm scene shared by one or more viewers (sessions).

    A room is created when a session calls ``start``. The creator is the
    *owner* — only they can stop / reset the run. Other viewers join via
    short code; they can chat and watch but can't drive the swarm.

    The room id mirrors the owner's session id for the room's lifetime.
    Reusing the int avoids a parallel id space and means existing
    bus-routing code continues to work unchanged.
    """
    room_id: int
    owner_session_id: int
    short_code: str
    swarm: Any = None
    project: Any = None
    task: asyncio.Task | None = None


_sessions: dict[int, SessionState] = {}
_rooms: dict[int, RoomState] = {}
# Lookup by short code (uppercase A-Z + 2-9 — no I/O/0/1 to avoid
# misreads when someone reads it aloud).
_short_codes: dict[str, int] = {}

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
        # Snapshot the connection list — a concurrent disconnect must not
        # mutate the list we're iterating over (TOCTOU).
        for ws in list(self.connections):
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
    "agent.speech_audio_start",
    "agent.speech_audio_chunk",
    "agent.speech_audio_end",
    "agent.speech_audio_dropped",
    "agent.emote",
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
    # Multi-viewer (Phase 4)
    "viewer.chat",
    "viewer.cheer",
    "room.viewers",
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
        owner = _sessions.get(sid)
        if owner is None:
            return
        # Fan out to every viewer who has joined this owner's room. The
        # owner's session id is the room id (see _create_room_for) so we
        # can route by room without an extra lookup. Sessions with a dead
        # ws are dropped lazily on send failure.
        room_id = owner.room_id
        dead: list[int] = []
        for viewer in _viewers_in_room(room_id):
            ok = await manager.send_to_ws(viewer.ws, event_type, kwargs)
            if not ok:
                dead.append(viewer.session_id)
        for vsid in dead:
            _sessions.pop(vsid, None)

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
    """Run the swarm for a single session in the background.

    Contract: the caller is responsible for setting ``_current_session_id``
    on the context that runs this coroutine (see the ``start`` command
    handler below, which uses ``contextvars.copy_context`` +
    ``ctx.run(...set)`` + ``asyncio.create_task(..., context=ctx)``).
    We intentionally do NOT call ``_current_session_id.set`` here so the
    contract is explicit and there's a single source of truth.
    """
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
    finally:
        # The TTS worker is a process-wide singleton whose internal task
        # captures a ContextVar at first enqueue. Tearing it down on session
        # end ensures the next session's worker re-captures fresh session id.
        # Phase 4 will move the worker onto RoomState and remove this.
        from autonoma.tts_worker import shutdown_default_worker
        shutdown_default_worker()


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


def _cleanup_session(session_id: int) -> None:
    """Remove a session entry. Safe to call multiple times."""
    _sessions.pop(session_id, None)


async def _cancel_session_task(session: SessionState) -> None:
    """Cancel the session's running swarm task (if any) and await its exit."""
    if session.task is not None and not session.task.done():
        session.task.cancel()
        try:
            await session.task
        except (asyncio.CancelledError, Exception):
            pass
    session.task = None


def _session_project(session_id: int | None) -> Any | None:
    if session_id is None:
        return None
    session = _sessions.get(session_id)
    return session.project if session else None


def _generate_short_code() -> str:
    """Generate a 6-char alphanumeric short code unique among live rooms.

    Avoids 0/1/I/O so a viewer reading the code aloud doesn't get
    misheard. Falls through with secrets.choice (cryptographic RNG) so
    the codes can't be guessed from a sequence.
    """
    import secrets
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if code not in _short_codes:
            return code
    # Vanishingly unlikely under normal load — but if it ever fires we
    # want to know rather than spin forever.
    raise RuntimeError("could not allocate a unique short code")


def _create_room_for(session: SessionState) -> RoomState:
    """Create a new room owned by ``session`` and reroute the session
    into it. Returns the room (also stored in ``_rooms``)."""
    code = _generate_short_code()
    room = RoomState(
        room_id=session.session_id,
        owner_session_id=session.session_id,
        short_code=code,
    )
    _rooms[room.room_id] = room
    _short_codes[code] = room.room_id
    session.room_id = room.room_id
    return room


def _viewers_in_room(room_id: int) -> list[SessionState]:
    return [s for s in _sessions.values() if s.room_id == room_id]


async def _notify_room_membership(room_id: int) -> None:
    """Broadcast the current viewer count + names to everyone in the room.

    Cheap: rooms are small (single-digit viewers in normal use) and this
    only fires on join / leave, not per-event.
    """
    viewers = _viewers_in_room(room_id)
    payload = {
        "room_id": room_id,
        "viewer_count": len(viewers),
        "viewers": [v.display_name or f"anon-{v.session_id % 1000}" for v in viewers],
    }
    for v in viewers:
        await manager.send_to_ws(v.ws, "room.viewers", payload)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    session = SessionState(ws=ws, session_id=id(ws))
    # A fresh connection is its own private room until it either starts
    # a swarm (creating a real room) or joins one via short code. The
    # default room id == session id keeps the bus-routing invariant
    # (always send to a non-zero room id).
    session.room_id = session.session_id
    _sessions[session.session_id] = session

    await manager.connect(ws)
    try:
        # ── Announce auth + session id on connect ──
        has_admin = bool(settings.admin_password)
        await ws.send_text(json.dumps({
            "event": "auth.status",
            "data": {
                "requires_auth": True,
                "has_admin": has_admin,
                "server_provider": settings.provider if has_admin else None,
                "server_model": settings.model if has_admin else None,
                # Every connection gets its own session id; the client must
                # use it for downloads so file routes are isolated too.
                "session_id": session.session_id,
            },
        }))

        # ── Initial state snapshot (always idle for a fresh connection) ──
        snapshot = _get_snapshot(session.session_id)
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
                        await manager.send_to_ws(ws, "auth.failed", {
                            "message": "관리자 계정이 서버에 설정되어 있지 않습니다.",
                        })
                    elif msg.get("password") != settings.admin_password:
                        await manager.send_to_ws(ws, "auth.failed", {
                            "message": "관리자 비밀번호가 올바르지 않습니다.",
                        })
                    else:
                        llm_cfg = _build_admin_llm_config()
                        if llm_cfg is None:
                            await manager.send_to_ws(ws, "auth.failed", {
                                "message": "서버에 API 키가 설정되어 있지 않습니다. 관리자에게 문의하세요.",
                            })
                        else:
                            session.llm_config = llm_cfg
                            logger.info(
                                f"[WS:{session.session_id}] Admin authenticated "
                                f"(provider={llm_cfg.provider})"
                            )
                            await manager.send_to_ws(ws, "auth.success", {
                                "is_admin": True,
                                "provider": llm_cfg.provider,
                                "model": llm_cfg.model,
                            })

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
                        logger.info(
                            f"[WS:{session.session_id}] User auth rejected: {error}"
                        )
                        await manager.send_to_ws(ws, "auth.failed", {"message": error})
                    else:
                        # Only carry base_url when the provider actually uses it.
                        # Passing a stale base_url to anthropic/openai can make
                        # the SDK talk to the wrong endpoint and throw errors
                        # that look like "invalid API key".
                        effective_base_url = base_url if provider == "vllm" else ""
                        llm_cfg = LLMConfig(
                            provider=provider,  # type: ignore[arg-type]
                            api_key=api_key,
                            model=model,
                            base_url=effective_base_url,
                            max_tokens=settings.max_tokens,
                            temperature=settings.temperature,
                        )
                        session.llm_config = llm_cfg
                        logger.info(
                            f"[WS:{session.session_id}] User authenticated "
                            f"(provider={provider}, model={model}, "
                            f"key_len={len(api_key)}, base_url="
                            f"{effective_base_url or '(n/a)'})"
                        )
                        await manager.send_to_ws(ws, "auth.success", {
                            "is_admin": False,
                            "provider": provider,
                            "model": model,
                        })
                else:
                    await manager.send_to_ws(ws, "auth.failed", {
                        "message": "인증 방식이 올바르지 않습니다 (admin 또는 user).",
                    })

            # ── get_snapshot ──────────────────────────────────────────
            elif cmd == "get_snapshot":
                snap = _get_snapshot(session.session_id)
                await manager.send_to_ws(ws, "snapshot", snap)

            # ── start ─────────────────────────────────────────────────
            elif cmd == "start":
                if session.llm_config is None:
                    await manager.send_to_ws(ws, "auth.required", {
                        "message": "스웜을 시작하려면 먼저 로그인해주세요.",
                    })
                    continue

                goal = msg.get("goal", "").strip()
                if not goal:
                    await manager.send_to_ws(ws, "error", {
                        "message": "Goal is required",
                    })
                elif session.task is not None and not session.task.done():
                    await manager.send_to_ws(ws, "error", {
                        "message": "Swarm is already running in this session",
                    })
                else:
                    max_rounds = msg.get("max_rounds", 30)
                    # Materialize a real room so other viewers can join via
                    # short code. ``room_id == session.session_id`` is
                    # preserved so the existing ContextVar routing keeps
                    # working without a parallel id space.
                    room = _rooms.get(session.session_id)
                    if room is None:
                        room = _create_room_for(session)
                    # Capture the current context so _run_swarm starts with
                    # our session id set on the ContextVar.
                    ctx = contextvars.copy_context()
                    ctx.run(_current_session_id.set, session.session_id)
                    session.task = asyncio.create_task(
                        _run_swarm(
                            session.session_id,
                            goal,
                            max_rounds,
                            llm_config=session.llm_config,
                        ),
                        context=ctx,
                    )
                    await manager.send_to_ws(ws, "swarm.starting", {
                        "goal": goal,
                        "room_code": room.short_code,
                    })

            # ── join_room ─────────────────────────────────────────────
            # Other viewers attach to an already-running swarm by short
            # code. The host's `start` reply contains the code; the
            # frontend either renders it as a sharable link
            # (?room=ABCXYZ) or shows it for read-aloud copy.
            elif cmd == "join_room":
                code = (msg.get("code") or "").strip().upper()
                if not code:
                    await manager.send_to_ws(ws, "room.join_failed", {
                        "message": "Room code required.",
                    })
                else:
                    target_room_id = _short_codes.get(code)
                    target_room = (
                        _rooms.get(target_room_id) if target_room_id else None
                    )
                    if target_room is None:
                        await manager.send_to_ws(ws, "room.join_failed", {
                            "message": "Room not found or already ended.",
                        })
                    elif target_room_id == session.session_id:
                        await manager.send_to_ws(ws, "room.join_failed", {
                            "message": "You're already the host of this room.",
                        })
                    else:
                        # Leave any prior room (and notify it) before
                        # joining the new one. The "private" default room
                        # has no other viewers so this is a no-op there.
                        prev_room_id = session.room_id
                        session.room_id = target_room_id
                        await manager.send_to_ws(ws, "room.joined", {
                            "code": code,
                            "is_owner": False,
                        })
                        # Snapshot the live scene so the new viewer gets
                        # the current state immediately, not on the next
                        # frame.
                        snap = _get_snapshot(target_room_id)
                        await manager.send_to_ws(ws, "snapshot", snap)
                        await _notify_room_membership(target_room_id)
                        if prev_room_id != target_room_id:
                            await _notify_room_membership(prev_room_id)

            # ── set_name ──────────────────────────────────────────────
            elif cmd == "set_name":
                # Bound the length so a malicious viewer can't spam a
                # huge name through the chat list.
                name = (msg.get("name") or "").strip()[:24]
                session.display_name = name
                await _notify_room_membership(session.room_id)

            # ── chat ──────────────────────────────────────────────────
            # Spectator chat. Any viewer can send; the message fans out
            # to every viewer in the room (and is also surfaced to the
            # swarm as a `viewer.chat` event so agents can react to it).
            elif cmd == "chat":
                text = (msg.get("text") or "").strip()[:280]
                if not text:
                    pass
                else:
                    name = session.display_name or f"anon-{session.session_id % 1000}"
                    payload = {
                        "from": name,
                        "text": text,
                        "is_owner": session.session_id == session.room_id,
                    }
                    for v in _viewers_in_room(session.room_id):
                        await manager.send_to_ws(v.ws, "viewer.chat", payload)

            # ── stop ──────────────────────────────────────────────────
            elif cmd == "stop":
                if session.task is not None and not session.task.done():
                    session.task.cancel()
                    await manager.send_to_ws(ws, "swarm.stopped", {})

            # ── reset ─────────────────────────────────────────────────
            elif cmd == "reset":
                await _cancel_session_task(session)
                session.swarm = None
                session.project = None
                logger.info(
                    f"[WS:{session.session_id}] Session reset — returning to idle"
                )
                await manager.send_to_ws(ws, "swarm.reset", {})
                await manager.send_to_ws(
                    ws, "snapshot", _get_snapshot(session.session_id)
                )

            # ── message ───────────────────────────────────────────────
            elif cmd == "message":
                text = msg.get("text", "")
                target = msg.get("target")
                if text.startswith("/cheer"):
                    # The ws command loop runs OUTSIDE the per-task ContextVar
                    # context, so emitting through the bus would leak this
                    # event to every connected user via the broadcast
                    # fallback in ``_make_handler``. Send it straight to the
                    # originating session's ws instead.
                    await manager.send_to_ws(
                        session.ws,
                        "world.event",
                        {
                            "title": (
                                "The audience cheers wildly! "
                                "Agents feel inspired!"
                            ),
                        },
                    )
                elif text.startswith("/status") or text.startswith("/snapshot"):
                    await manager.send_to_ws(
                        ws, "snapshot", _get_snapshot(session.session_id)
                    )
                elif text.startswith("/stop"):
                    if session.task is not None and not session.task.done():
                        session.task.cancel()
                else:
                    await manager.send_to_ws(ws, "chat.message", {
                        "text": text, "source": "user", "target": target or "",
                    })
                    if (
                        text
                        and session.swarm is not None
                        and session.task is not None
                        and not session.task.done()
                    ):
                        # The guard above can pass and then the swarm may
                        # become stale before the await resolves (the task
                        # can finish or be torn down mid-flight). Catch the
                        # attribute/runtime errors that result from touching
                        # a half-cleaned-up swarm so the ws loop keeps
                        # running for this client.
                        try:
                            await session.swarm.inject_human_message(
                                text, target=target
                            )
                        except (AttributeError, RuntimeError) as e:
                            logger.warning(
                                f"[WS:{session.session_id}] "
                                f"inject_human_message race "
                                f"(swarm stale): {e!r}"
                            )
                        except Exception as e:
                            logger.error(
                                f"[WS:{session.session_id}] "
                                f"Failed to inject human message: {e!r}"
                            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[WS:{session.session_id}] Error: {e}")
    finally:
        # Tear down anything this session owned so no orphaned task keeps
        # burning tokens after the user disconnects.
        room_id = session.room_id
        # Only cancel the swarm task when the *owner* leaves. Spectators
        # disconnecting must not stop the show.
        owned_room = _rooms.get(session.session_id)
        if owned_room is not None:
            await _cancel_session_task(session)
            # Bid the room goodbye — drop the short code and the room
            # registry entry. Any remaining viewers will see future
            # events stop arriving and can choose to leave.
            _short_codes.pop(owned_room.short_code, None)
            _rooms.pop(session.session_id, None)
        manager.disconnect(ws)
        _cleanup_session(session.session_id)
        # If this session was a *viewer* (not the owner), notify the
        # remaining viewers so the audience count updates.
        if owned_room is None and room_id != session.session_id:
            await _notify_room_membership(room_id)


def _require_session(session_id: int | None) -> SessionState:
    if session_id is None:
        raise HTTPException(
            status_code=400, detail="session query parameter is required"
        )
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/api/files")
async def list_files(session: int | None = Query(default=None)) -> dict[str, Any]:
    project = _session_project(session)
    if project is None:
        return {"project": None, "files": []}
    return {
        "project": project.name,
        "files": [
            {
                "path": f.path,
                "size": len(f.content),
                "description": f.description,
                "created_by": f.created_by,
            }
            for f in project.files
        ],
    }


@app.get("/api/files/download")
async def download_file(
    path: str, session: int | None = Query(default=None)
) -> Response:
    project = _session_project(session)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project for session")
    artifact = next((f for f in project.files if f.path == path), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    filename = path.rsplit("/", 1)[-1] or "file"
    return Response(
        content=artifact.content.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/files/zip")
async def download_zip(session: int | None = Query(default=None)) -> Response:
    project = _session_project(session)
    if project is None or not project.files:
        raise HTTPException(status_code=404, detail="No files to download")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for artifact in project.files:
            safe_path = "/".join(
                seg for seg in artifact.path.split("/") if seg and seg != ".."
            )
            if not safe_path:
                continue
            zf.writestr(safe_path, artifact.content)
    project_name = project.name or "autonoma-project"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_name}.zip"'},
    )


@app.get("/api/health")
async def health():
    active_swarms = sum(
        1 for s in _sessions.values()
        if s.task is not None and not s.task.done()
    )
    return {
        "status": "ok",
        "connections": len(manager.connections),
        "sessions": len(_sessions),
        "active_swarms": active_swarms,
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
