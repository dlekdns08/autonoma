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
from typing import Any, Literal

import io
import re
import zipfile

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response as FastAPIResponse,
    WebSocket,
    WebSocketDisconnect,
    status as http_status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from autonoma.auth import (
    SESSION_COOKIE_NAME,
    hash_password,
    issue_session_token,
    read_session_token,
    require_active_user,
    require_admin,
    verify_password,
)
from autonoma.config import settings
from autonoma.context import current_session_id as _current_session_id
from autonoma.db.users import (
    User,
    create_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user_status,
)
from autonoma.event_bus import bus
from autonoma.harness.policy import HarnessPolicyContent, default_policy_content
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
    # True when this WS authenticated via the admin password path (or
    # an admin cookie, once wired). Governs which harness policy
    # knobs the session is allowed to flip at start time — see
    # ``_resolve_start_policy``'s ``is_admin`` parameter.
    is_admin: bool = False
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
        message = _make_event_message(event_type, data)
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


def _make_event_message(event_type: str, data: dict[str, Any]) -> str:
    """Serialize an event to a JSON string ready to send over WebSocket.

    Caches the serialized payload so high-frequency events (agent.emote,
    agent.state) that fan out to N viewers don't re-serialize N times.
    The cache key is (event_type, id(data)) — since ``data`` is the raw
    **kwargs dict built inside the bus emit call it is created fresh each
    time, so id collisions across calls are not a concern.
    """
    return json.dumps({"event": event_type, "data": _serialize(data)})


# ── Event Bridge: bus → WebSocket ─────────────────────────────────────────

FORWARDED_EVENTS = [
    "swarm.initializing",
    "swarm.ready",
    "swarm.started",
    "swarm.round",
    "swarm.finished",
    "session.metadata",
    "session.checkpoint",
    "agent.speech",
    "agent.speech_audio_start",
    "agent.speech_audio_chunk",
    "agent.speech_audio_end",
    "agent.speech_audio_dropped",
    "agent.emote",
    "agent.state",
    "agent.mood",
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
    "fortune.pickup",
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
        # Serialize ONCE before the fan-out loop so N viewers don't each
        # pay the recursive _serialize + json.dumps cost independently.
        message = _make_event_message(event_type, kwargs)
        room_id = owner.room_id
        dead: list[int] = []
        for viewer in _viewers_in_room(room_id):
            try:
                await viewer.ws.send_text(message)
            except Exception:
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


async def _resolve_start_policy(
    *,
    user_id: str | None,
    preset_id: Any,
    overrides: Any,
    is_admin: bool = False,
) -> tuple[HarnessPolicyContent | None, str | None]:
    """Build the final HarnessPolicyContent for a ``start`` command.

    Returns ``(content, None)`` on success, or ``(None, error_message)``
    when the preset is not accessible or the merged content fails
    validation. ``user_id`` is optional so callers that haven't yet
    wired per-session auth (the WS path) can skip ownership checks —
    when supplied, a non-default preset owned by another user yields
    ``"preset not accessible"``.

    ``is_admin`` governs admin-only rules: non-admins cannot set
    ``safety.enforcement_level=off`` etc. Dangerous cross-section combos
    are rejected for everyone regardless of role.
    """
    from pydantic import ValidationError

    from autonoma.db.harness_policies import get_policy_by_id
    from autonoma.harness.validation import check_content

    if preset_id is None:
        base = default_policy_content().model_dump(mode="json")
    else:
        if not isinstance(preset_id, str) or not preset_id:
            return None, "preset not accessible"
        preset = await get_policy_by_id(preset_id)
        if preset is None:
            return None, "preset not accessible"
        if (
            not preset.is_default
            and user_id is not None
            and preset.owner_user_id != user_id
        ):
            return None, "preset not accessible"
        base = preset.content.model_dump(mode="json")

    if overrides is not None:
        if not isinstance(overrides, dict):
            return None, "invalid overrides"
        # Per-section REPLACE: each top-level key swaps the whole
        # sub-policy object. This matches the documented merge semantics
        # and avoids a deep-merge subtlety where partial fields would
        # conflict with pydantic's extra="forbid".
        for section, value in overrides.items():
            base[section] = value

    try:
        content = HarnessPolicyContent(**base)
    except ValidationError:
        return None, "invalid policy content"

    issues = check_content(content, is_admin=is_admin)
    if issues:
        # Surface the first issue's message — WS callers stream one
        # error at a time. HTTP callers use the richer validation path
        # below (see create/update preset endpoints) to get the full
        # list.
        return None, issues[0].message

    return content, None


async def _run_swarm(
    session_id: int,
    goal: str,
    max_rounds: int = 30,
    llm_config: LLMConfig | None = None,
    policy: HarnessPolicyContent | None = None,
    preset_id: str | None = None,
    overrides: dict[str, Any] | None = None,
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
    swarm = AgentSwarm(policy=policy, llm_config=llm_config)

    session.swarm = swarm
    session.project = project

    # Seed per-run observability. ``policy`` is the effective content
    # after preset + overrides + validation, so recording it here gives
    # an accurate picture even when the user didn't supply either.
    if policy is not None:
        from autonoma.harness.observability import record_run_start

        record_run_start(
            session_id=session_id,
            preset_id=preset_id,
            overrides=overrides,
            content=policy,
        )

    checkpoint_task = _start_checkpoint_task(session_id, policy)
    _apply_policy_side_effects(session_id, policy)

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
        if checkpoint_task is not None:
            checkpoint_task.cancel()

        # Seal per-run observability before taking the room down.
        from autonoma.harness.observability import (
            get_session_metadata,
            metadata_to_dict,
            record_run_end,
        )

        record_run_end(session_id)
        meta = get_session_metadata(session_id)
        if meta is not None:
            # Fan out the final metadata so the frontend + any external
            # listeners can persist it. Piggy-backs on the existing
            # event bus → FORWARDED_EVENTS path.
            await bus.emit("session.metadata", **metadata_to_dict(meta))

        # Shut down this room's TTS worker (per-room since Phase 4).
        # Passing session_id explicitly avoids reading a ContextVar that
        # may already be cleared by the time the finally-block runs.
        from autonoma.tts_worker import shutdown_worker
        shutdown_worker(session_id)


def _apply_policy_side_effects(
    session_id: int, policy: HarnessPolicyContent | None
) -> None:
    """Log the new Group-C policy choices at run start.

    The fields themselves already feed observability counters via
    ``record_run_start`` — this exists so an operator reading raw logs
    can confirm which variants actually kicked in without parsing JSON.
    """
    if policy is None:
        return
    logger.info(
        "[swarm:%s] policy system=%s cache=%s budget=%s/%d checkpoint=%s/%ds",
        session_id,
        policy.system.prompt_variant,
        policy.cache.provider_cache,
        policy.budget.enforcement,
        policy.budget.tokens_per_run,
        policy.checkpoint.include_full_state,
        policy.checkpoint.interval_seconds,
    )


def _start_checkpoint_task(
    session_id: int, policy: HarnessPolicyContent | None
) -> asyncio.Task[None] | None:
    """Spin up the periodic ``session.checkpoint`` emitter.

    Returns ``None`` (no task) when the policy disables checkpoints
    (``interval_seconds=0``) or no policy was resolved. The task is
    cancelled from ``_run_swarm``'s ``finally`` block, which already
    handles room teardown — no separate cleanup path needed.
    """
    if policy is None or policy.checkpoint.interval_seconds <= 0:
        return None

    interval = policy.checkpoint.interval_seconds
    shape_variant = policy.checkpoint.include_full_state
    from autonoma.harness.strategies import lookup

    shape_fn = lookup("checkpoint.include_full_state", shape_variant)

    async def _emit_loop() -> None:
        round_counter = 0
        try:
            while True:
                await asyncio.sleep(interval)
                round_counter += 1
                sess = _sessions.get(session_id)
                if sess is None or sess.swarm is None:
                    continue
                agents = (
                    [a.name for a in sess.swarm.agents.values()]
                    if shape_variant == "on"
                    else []
                )
                payload = shape_fn(
                    {
                        "session_id": session_id,
                        "round": round_counter,
                        "tokens_used": 0,  # placeholder until token tracking lands
                        "agents": agents,
                        "recent_messages": [],
                    }
                )
                await bus.emit("session.checkpoint", **payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — log & carry on
            logger.warning("[swarm:%s] checkpoint loop: %s", session_id, exc)

    return asyncio.create_task(_emit_loop())


# ── Swarm State Snapshot ──────────────────────────────────────────────────

# Per-session snapshot coalescing: tracks in-flight snapshot tasks so
# rapid get_snapshot requests don't pile up. Only one snapshot computes
# at a time per session; subsequent requests reuse the same future.
_snapshot_tasks: dict[int, asyncio.Task[dict[str, Any]]] = {}


async def _get_snapshot_coalesced(session_id: int) -> dict[str, Any]:
    """Return snapshot, coalescing concurrent requests into one computation."""
    existing = _snapshot_tasks.get(session_id)
    if existing is not None and not existing.done():
        return await existing

    async def _compute() -> dict[str, Any]:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, _get_snapshot, session_id
            )
        finally:
            _snapshot_tasks.pop(session_id, None)

    task = asyncio.create_task(_compute())
    _snapshot_tasks[session_id] = task
    return await task


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

USERNAME_PATTERN = re.compile(r"^[a-z0-9_-]{3,32}$")


async def _bootstrap_admin_user() -> None:
    """Create the default admin user on startup if conditions are met.

    Fires iff ``settings.admin_password`` is set AND there is no user
    named ``admin`` in the DB. We intentionally gate on the username
    (not on "any admin-role user") so an operator who has already
    created named admin accounts doesn't get a stray ``admin`` row.
    """
    from autonoma.db.engine import init_db as _init_db

    if not settings.admin_password:
        return
    # Make sure the users table exists before we query.
    await _init_db()
    existing = await get_user_by_username("admin")
    if existing is not None:
        return
    try:
        await create_user(
            username="admin",
            password_hash=hash_password(settings.admin_password),
            role="admin",
            status="active",
        )
        logger.info("[auth] bootstrap admin user created from AUTONOMA_ADMIN_PASSWORD")
    except Exception as exc:
        # Don't take the whole app down if somebody raced us.
        logger.warning("[auth] bootstrap admin user create failed: %s", exc)


async def _bootstrap_default_harness_policy() -> None:
    """Seed the system-default harness preset on startup.

    Idempotent — ``ensure_default_policy`` checks for an existing default
    before inserting, so this is safe on every boot.
    """
    from autonoma.db.harness_policies import ensure_default_policy

    try:
        await ensure_default_policy()
        logger.info("[harness] default policy ensured")
    except Exception as exc:
        # Non-fatal: a missing default preset is recoverable at first use.
        logger.warning("[harness] default policy bootstrap failed: %s", exc)


def _log_startup_summary() -> None:
    """One-line bootstrap summary. Makes "why is auth misbehaving" take
    ten seconds to diagnose instead of ten minutes.
    """
    logger.info(
        "[startup] admin_bootstrap=%s session_secret=%s cookie_samesite=%s provider=%s",
        "configured" if settings.admin_password else "skipped",
        "configured" if settings.session_secret else "ephemeral",
        _cookie_samesite(),
        settings.provider,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_event_bridge()
    await _bootstrap_admin_user()
    await _bootstrap_default_harness_policy()
    _log_startup_summary()
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


def _cookie_is_secure() -> bool:
    """Secure cookies in production, lax in development.

    We can't know whether we're behind HTTPS from Python alone, so treat
    the presence of ``session_secret`` as the signal: if an operator has
    set a durable secret they've configured this for real deployment.
    Tests and local dev that don't set one get non-Secure cookies so
    they work over plain http://localhost.
    """
    return bool(settings.session_secret)


def _cookie_samesite() -> Literal["lax", "strict"]:
    """Lax in development, strict once a session_secret is configured.

    Same signal as ``_cookie_is_secure`` — an operator who has set a durable
    secret has signed up for real deployment semantics. Lax is required for
    typical dev setups where the Next.js dev server (port 3000) and the
    FastAPI process (3479) are different origins but same site.
    """
    return "strict" if settings.session_secret else "lax"


def _set_session_cookie(response: FastAPIResponse, user_id: str) -> None:
    token = issue_session_token(user_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_is_secure(),
        samesite=_cookie_samesite(),
        path="/",
    )


def _clear_session_cookie(response: FastAPIResponse) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_is_secure(),
        samesite=_cookie_samesite(),
    )


# ── /api/auth/* ───────────────────────────────────────────────────────


@app.post("/api/auth/signup", status_code=http_status.HTTP_201_CREATED)
async def auth_signup(payload: dict[str, Any]) -> dict[str, Any]:
    """Public signup. New users start as ``pending`` until an admin
    approves them. Returns 201 on success, 409 if taken, 400 if the
    input doesn't match the username/password rules."""
    username = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not USERNAME_PATTERN.match(username):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid_username",
        )
    if len(password) < 6:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid_password",
        )
    if await get_user_by_username(username) is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="username_taken",
        )
    try:
        password_hash = hash_password(password)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid_password",
        )
    await create_user(
        username=username,
        password_hash=password_hash,
        role="user",
        status="pending",
    )
    return {"status": "pending"}


@app.post("/api/auth/login")
async def auth_login(payload: dict[str, Any], response: FastAPIResponse) -> dict[str, Any]:
    """Username/password login. 200 + cookie on success."""
    username = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not username or not password:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="bad_credentials",
        )
    user = await get_user_by_username(username)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="bad_credentials",
        )
    if user.status != "active":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="not_active",
        )
    _set_session_cookie(response, user.id)
    return {"user": user.public_dict()}


@app.post("/api/auth/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def auth_logout(response: FastAPIResponse) -> FastAPIResponse:
    """Unconditionally clear the session cookie. 204 even if no cookie
    was present (idempotent logout)."""
    _clear_session_cookie(response)
    response.status_code = http_status.HTTP_204_NO_CONTENT
    return response


@app.get("/api/auth/me")
async def auth_me(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the current active user or 401/403."""
    return {"user": user.public_dict()}


@app.get("/api/debug/auth")
async def debug_auth(request: Request) -> dict[str, Any]:
    """Dev-only sanity check for auth wiring.

    Returns 404 when ``session_secret`` is set — the presence of a durable
    secret is our "this is a real deployment" signal, and a probe endpoint
    that enumerates bootstrap state has no business on a prod server.
    """
    if settings.session_secret:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND)
    admin_row = await get_user_by_username("admin")
    return {
        "admin_password_configured": bool(settings.admin_password),
        "session_secret_configured": False,
        "admin_user_exists": admin_row is not None,
        "admin_user_active": (admin_row is not None and admin_row.status == "active"),
        "cookie_received": SESSION_COOKIE_NAME in request.cookies,
        "cookie_samesite": _cookie_samesite(),
        "cookie_secure": _cookie_is_secure(),
    }


# ── /api/admin/users/* ────────────────────────────────────────────────


@app.get("/api/admin/users")
async def admin_list_users(
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    users = await list_users()
    return {"users": [u.public_dict() for u in users]}


async def _transition_user(
    user_id: str,
    *,
    required_status: set[str] | None,
    new_status: str,
) -> None:
    """Apply a status transition, 404 if missing, 409 if current status
    isn't in ``required_status`` (when specified)."""
    user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )
    if required_status is not None and user.status not in required_status:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="invalid_state_transition",
        )
    await update_user_status(user_id, new_status)  # type: ignore[arg-type]


@app.post(
    "/api/admin/users/{user_id}/approve",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_approve_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(
        user_id, required_status={"pending"}, new_status="active"
    )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/admin/users/{user_id}/deny",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_deny_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(
        user_id, required_status={"pending"}, new_status="disabled"
    )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/admin/users/{user_id}/disable",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_disable_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(
        user_id, required_status={"active"}, new_status="disabled"
    )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/admin/users/{user_id}/reactivate",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_reactivate_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(
        user_id, required_status={"disabled"}, new_status="active"
    )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


# ── /api/harness/presets/* ────────────────────────────────────────────

_HARNESS_NAME_MIN = 1
_HARNESS_NAME_MAX = 64


def _pydantic_errors_to_fastapi(exc: Any) -> list[dict[str, Any]]:
    """Convert a pydantic ValidationError to FastAPI's native error shape.

    FastAPI renders ``RequestValidationError`` as
    ``{"detail": [{"loc": [...], "msg": "...", "type": "..."}, ...]}`` —
    we mirror that shape when rejecting ``HarnessPolicyContent(**body)``
    so the frontend can reuse the same rendering path for both.
    """
    items: list[dict[str, Any]] = []
    for err in exc.errors():
        items.append(
            {
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return items


def _reject_invalid_harness_content(
    content: HarnessPolicyContent, *, is_admin: bool
) -> None:
    """Raise HTTPException when semantic validation rejects ``content``.

    Admin-only violations surface as 403 (the caller lacks the role),
    dangerous-combo violations as 422 (the content is illegal for
    everyone). Field-level ``path`` ("safety.code_execution") is exposed
    via ``loc`` so the frontend can highlight the offending control.
    """
    from autonoma.harness.validation import check_content

    issues = check_content(content, is_admin=is_admin)
    if not issues:
        return
    admin_only = [i for i in issues if i.admin_only]
    if admin_only:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=[
                {
                    "loc": ["body", "content", *i.path.split(".")],
                    "msg": i.message,
                    "type": "admin_only",
                }
                for i in admin_only
            ],
        )
    raise HTTPException(
        status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=[
            {
                "loc": ["body", "content"],
                "msg": i.message,
                "type": "dangerous_combo",
            }
            for i in issues
        ],
    )


def _harness_policy_to_dict(policy: Any) -> dict[str, Any]:
    return policy.model_dump(mode="json")


@app.get("/api/harness/pipeline")
async def get_harness_pipeline(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Pipeline-view layout: 16 nodes grouped into A/B/C plus intra-group edges.

    Read-only; the actual editing flow still goes through the existing
    preset / override endpoints. This endpoint exists so the UI can lay
    the harness out as a visual flow without embedding the grouping
    decisions into frontend code.
    """
    from autonoma.harness.pipeline import pipeline_payload

    return pipeline_payload()


@app.get("/api/harness/schema")
async def get_harness_schema(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Field-by-field schema so the frontend can build a form without
    mirroring the Pydantic model. Returns a flattened shape:

    ``{"sections": {"<section>": {"<field>": {type, default, options?,
    min?, max?}}}}``

    The frontend builds a drop-down for ``type == "enum"`` and a number
    input with ``min``/``max`` for ``type == "int"`` / ``"float"``.
    Stays in lockstep with ``HarnessPolicyContent`` automatically — when
    a Literal gains a value or a Field changes bounds, this endpoint
    reflects it on the next request without a code change here.
    """
    from typing import Literal, get_args, get_origin

    content_cls = HarnessPolicyContent
    sections: dict[str, dict[str, Any]] = {}
    default_content = default_policy_content().model_dump(mode="json")

    for section_name, section_field in content_cls.model_fields.items():
        sub_model = section_field.annotation
        fields: dict[str, Any] = {}
        for field_name, field_info in sub_model.model_fields.items():
            annot = field_info.annotation
            default = default_content[section_name].get(field_name)
            spec: dict[str, Any] = {"default": default}
            if get_origin(annot) is Literal:
                spec["type"] = "enum"
                spec["options"] = list(get_args(annot))
            elif annot is bool:
                spec["type"] = "bool"
            elif annot is int:
                spec["type"] = "int"
            elif annot is float:
                spec["type"] = "float"
            else:
                spec["type"] = "unknown"
            # Pydantic stores ge/le on constraint metadata.
            for meta in field_info.metadata:
                if hasattr(meta, "ge"):
                    spec["min"] = meta.ge
                if hasattr(meta, "le"):
                    spec["max"] = meta.le
            fields[field_name] = spec
        sections[section_name] = fields
    return {"sections": sections}


@app.get("/api/harness/presets")
async def list_harness_presets(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """List the caller's presets plus the system default preset."""
    from autonoma.db.harness_policies import (
        get_default_policy,
        list_policies_for_user,
    )

    owned = await list_policies_for_user(user.id)
    seen_ids = {p.id for p in owned}
    # list_policies_for_user already includes the default when it exists,
    # but we re-fetch and merge defensively in case the default was
    # created after the user's rows (ordering quirks).
    default = await get_default_policy()
    presets = list(owned)
    if default is not None and default.id not in seen_ids:
        presets.append(default)
    return {"presets": [_harness_policy_to_dict(p) for p in presets]}


@app.get("/api/harness/presets/{preset_id}")
async def get_harness_preset(
    preset_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    from autonoma.db.harness_policies import get_policy_by_id

    preset = await get_policy_by_id(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    if not preset.is_default and preset.owner_user_id != user.id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    return _harness_policy_to_dict(preset)


@app.post(
    "/api/harness/presets",
    status_code=http_status.HTTP_201_CREATED,
)
async def create_harness_preset(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    from pydantic import ValidationError

    from autonoma.db.harness_policies import create_policy

    name = str(payload.get("name") or "").strip()
    if not (_HARNESS_NAME_MIN <= len(name) <= _HARNESS_NAME_MAX):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", "name"],
                    "msg": (
                        f"name must be {_HARNESS_NAME_MIN}..{_HARNESS_NAME_MAX} chars"
                    ),
                    "type": "value_error",
                }
            ],
        )
    content_raw = payload.get("content")
    if not isinstance(content_raw, dict):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", "content"],
                    "msg": "content must be an object",
                    "type": "type_error",
                }
            ],
        )
    try:
        content = HarnessPolicyContent(**content_raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_pydantic_errors_to_fastapi(exc),
        )
    _reject_invalid_harness_content(content, is_admin=(user.role == "admin"))
    created = await create_policy(
        owner_user_id=user.id,
        name=name,
        content=content,
    )
    return _harness_policy_to_dict(created)


@app.put("/api/harness/presets/{preset_id}")
async def update_harness_preset(
    preset_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    from pydantic import ValidationError

    from autonoma.db.harness_policies import (
        get_policy_by_id,
        update_policy,
    )

    existing = await get_policy_by_id(preset_id)
    if existing is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    # Default preset is read-only; surface as 403 rather than letting the
    # helper's ValueError escape as 500.
    if existing.is_default:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    if existing.owner_user_id != user.id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )

    new_name: str | None = None
    if "name" in payload and payload["name"] is not None:
        new_name = str(payload["name"]).strip()
        if not (_HARNESS_NAME_MIN <= len(new_name) <= _HARNESS_NAME_MAX):
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "name"],
                        "msg": (
                            f"name must be {_HARNESS_NAME_MIN}..{_HARNESS_NAME_MAX} chars"
                        ),
                        "type": "value_error",
                    }
                ],
            )

    new_content: HarnessPolicyContent | None = None
    if "content" in payload and payload["content"] is not None:
        content_raw = payload["content"]
        if not isinstance(content_raw, dict):
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "content"],
                        "msg": "content must be an object",
                        "type": "type_error",
                    }
                ],
            )
        try:
            new_content = HarnessPolicyContent(**content_raw)
        except ValidationError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_pydantic_errors_to_fastapi(exc),
            )
        _reject_invalid_harness_content(
            new_content, is_admin=(user.role == "admin")
        )

    try:
        updated = await update_policy(
            preset_id, name=new_name, content=new_content
        )
    except ValueError:
        # Defense in depth: the explicit is_default check above should
        # have caught this, but keep the translation in case the helper
        # adds other ValueError paths.
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    if updated is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    return _harness_policy_to_dict(updated)


@app.delete(
    "/api/harness/presets/{preset_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_harness_preset(
    preset_id: str,
    user: User = Depends(require_active_user),
) -> FastAPIResponse:
    from autonoma.db.harness_policies import delete_policy, get_policy_by_id

    existing = await get_policy_by_id(preset_id)
    if existing is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    if existing.is_default or existing.owner_user_id != user.id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    try:
        await delete_policy(preset_id)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@app.get("/api/harness/metrics")
async def get_harness_metrics(
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Global strategy-pick counters across every run on this process.

    Keys are ``"section.field=value"`` (e.g. ``"routing.strategy=priority"``);
    values are pick counts since server start. Admin-only since the
    signal is effectively a usage heat-map — not sensitive per se, but
    not something we want public either.
    """
    from autonoma.harness.observability import get_global_counters

    return {"counters": get_global_counters()}


@app.get("/api/session/{session_id}/metadata")
async def get_session_harness_metadata(
    session_id: int,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Per-run metadata: preset id, overridden sections, effective
    policy, timestamps, and per-run picks. Callers that own a live
    session can fetch mid-run; the ``ended_at`` field flips from null to
    a timestamp when the run finishes.

    Scoped to the caller's own sessions — admins can fetch any session
    since the signal is useful for post-mortem debugging."""
    from autonoma.harness.observability import (
        get_session_metadata,
        metadata_to_dict,
    )

    meta = get_session_metadata(session_id)
    if meta is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="session_not_found",
        )
    # Non-admins can only see metadata for sessions they opened on this
    # connection. ``_sessions`` is keyed by session_id and currently has
    # no user_id link, so fall back to a "any active user sees their
    # own session" check via the live SessionState (ws owner).
    if user.role != "admin":
        sess = _sessions.get(session_id)
        if sess is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="session_not_found",
            )
    return metadata_to_dict(meta)


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

        # ── Cookie-based auto-auth ───────────────────────────────────
        # If the WS handshake carried a valid HTTP session cookie, promote
        # the session automatically instead of forcing the viewer back
        # through the legacy `authenticate` command. Without this the
        # AuthModal reopens right after HTTP login and only the "Legacy
        # admin" tab can satisfy the WS, which is what users were seeing.
        cookie_token = ws.cookies.get(SESSION_COOKIE_NAME)
        cookie_user_id = read_session_token(cookie_token or "")
        cookie_user = await get_user_by_id(cookie_user_id) if cookie_user_id else None
        if cookie_user is not None and cookie_user.status == "active":
            llm_cfg = _build_admin_llm_config()
            if llm_cfg is not None:
                session.llm_config = llm_cfg
                session.is_admin = cookie_user.role == "admin"
                logger.info(
                    f"[WS:{session.session_id}] Cookie auth ok "
                    f"(user={cookie_user.username}, role={cookie_user.role}, "
                    f"provider={llm_cfg.provider})"
                )
                await manager.send_to_ws(ws, "auth.success", {
                    "is_admin": session.is_admin,
                    "provider": llm_cfg.provider,
                    "model": llm_cfg.model,
                })

        # ── Initial state snapshot (always idle for a fresh connection) ──
        snapshot = await _get_snapshot_coalesced(session.session_id)
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
                            session.is_admin = True
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
                snap = await _get_snapshot_coalesced(session.session_id)
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

                    # Resolve the harness policy for this run. Users may
                    # pick a preset via ``preset_id`` and layer per-section
                    # overrides on top (overrides REPLACE a whole section,
                    # they don't merge per-field).
                    policy_content, policy_err = await _resolve_start_policy(
                        user_id=None,
                        preset_id=msg.get("preset_id"),
                        overrides=msg.get("overrides"),
                        is_admin=session.is_admin,
                    )
                    if policy_err is not None:
                        await manager.send_to_ws(ws, "error", {
                            "message": policy_err,
                        })
                        continue

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
                            policy=policy_content,
                            preset_id=msg.get("preset_id"),
                            overrides=msg.get("overrides")
                            if isinstance(msg.get("overrides"), dict)
                            else None,
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
                        snap = await _get_snapshot_coalesced(target_room_id)
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
                    ws, "snapshot", await _get_snapshot_coalesced(session.session_id)
                )

            # ── pickup_cookie ─────────────────────────────────────────
            elif cmd == "pickup_cookie":
                recipient = (msg.get("recipient") or "").strip()
                if (
                    recipient
                    and session.swarm is not None
                    and session.task is not None
                    and not session.task.done()
                ):
                    # The ws loop runs outside the swarm task's ContextVar
                    # context, so set the session id token here before awaiting
                    # so bus events emitted during pickup reach this room.
                    token = _current_session_id.set(session.session_id)
                    try:
                        await session.swarm.pickup_fortune_cookie(recipient)
                    except (AttributeError, RuntimeError) as e:
                        logger.warning(
                            f"[WS:{session.session_id}] "
                            f"pickup_fortune_cookie race (swarm stale): {e!r}"
                        )
                    except Exception as e:
                        logger.error(
                            f"[WS:{session.session_id}] "
                            f"Failed to pickup cookie: {e!r}"
                        )
                    finally:
                        _current_session_id.reset(token)

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
                        ws, "snapshot", await _get_snapshot_coalesced(session.session_id)
                    )
                elif text.startswith("/stop"):
                    if session.task is not None and not session.task.done():
                        session.task.cancel()
                elif text.startswith("/cookie"):
                    # Dev helper: drop a fortune cookie on the map right now
                    # so the pickup loop can be verified without waiting for
                    # dawn. Usage: "/cookie <AgentName>" — if the name is
                    # omitted, picks a random non-Director agent.
                    parts = text.split(maxsplit=1)
                    target_name = parts[1].strip() if len(parts) > 1 else ""
                    if session.swarm is None or session.task is None or session.task.done():
                        await manager.send_to_ws(ws, "chat.message", {
                            "text": "/cookie: swarm not running",
                            "source": "system", "target": "",
                        })
                    else:
                        swarm = session.swarm
                        if not target_name:
                            candidates = [
                                n for n in swarm.agents.keys() if n != "Director"
                            ]
                            target_name = candidates[0] if candidates else ""
                        if target_name not in swarm.agents:
                            await manager.send_to_ws(ws, "chat.message", {
                                "text": f"/cookie: unknown agent '{target_name}'",
                                "source": "system", "target": "",
                            })
                        else:
                            token = _current_session_id.set(session.session_id)
                            try:
                                cookie = swarm.fortune_jar.give_cookie(
                                    target_name, swarm._round
                                )
                                if cookie is None:
                                    await manager.send_to_ws(ws, "chat.message", {
                                        "text": f"/cookie: {target_name} already has one",
                                        "source": "system", "target": "",
                                    })
                                else:
                                    await bus.emit(
                                        "fortune.given",
                                        agent=target_name,
                                        fortune=cookie.fortune,
                                    )
                            finally:
                                _current_session_id.reset(token)
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
