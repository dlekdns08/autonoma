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
import hmac
import itertools
import json
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware

from autonoma._api_persist import (  # noqa: F401 — re-exported
    _insert_file_history,
    _record_run_summary,
    _upsert_checkpoint,
)
from autonoma.auth import (
    SESSION_COOKIE_NAME,
    hash_password,
    is_guest_user_id,
    read_session_token,
)
from autonoma.config import settings
from autonoma.context import current_session_id as _current_session_id
from autonoma.db.users import (
    create_user,
    get_user_by_id,
    get_user_by_username,
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

    The ``swarm`` / ``project`` / ``task`` attributes are read-through
    properties that proxy to the session's room when one exists.
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
    # Set when this session authenticates via HTTP cookie. File/artifact
    # endpoints check this to reject cross-user access. None means either
    # a legacy admin-password session or a user-provided-API-key session
    # (both only expose the owner's own session_id to their browser).
    owner_user_id: str | None = None
    # Per-connection brute-force guard for the legacy admin-password
    # auth path. Locks the connection out after too many failures within
    # the window so a hostile client can't enumerate passwords on a
    # single WS.
    failed_auth_attempts: int = 0
    last_failed_auth_at: float = 0.0
    # Same shape but for ``join_room`` short-code probes — without this
    # an attacker can enumerate live rooms by spamming codes on a
    # single WS. Same per-connection scope as the admin guard above.
    failed_join_attempts: int = 0
    last_failed_join_at: float = 0.0
    # Last-fired-at timestamp for the viewer chat-command bridge
    # (``!cheer`` / ``!cookie`` / ``!boo``). Tracked here so the throttle
    # is per-WS — viewers can't dodge it by reconnecting unless they
    # actually drop their socket, which is the right trade-off for an
    # in-memory MVP guard. Reset to 0.0 implicitly on connect.
    last_viewer_command_at: float = 0.0
    # Last-fired-at timestamp for the director-hint command. Separate
    # window from chat commands because hints reach an LLM prompt and
    # warrant a slower cadence (see ``_DIRECTOR_HINT_THROTTLE_SECONDS``).
    last_director_hint_at: float = 0.0

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
    # ── live-share fields (2026-05) ────────────────────────────────────
    # Off by default. Hosts opt-in via POST /api/live-share/visibility,
    # which sets ``is_public=True`` and announces the room on the
    # ``/live`` directory page. Title and description are operator-set
    # so the directory card can advertise more than the goal text.
    is_public: bool = False
    public_title: str = ""
    public_description: str = ""
    started_at: float = 0.0  # time.time() when the room went live


_sessions: dict[int, SessionState] = {}


# Expose ``session_id → owner_user_id`` to modules that can't import
# ``api`` directly (cutscenes, scheduler). Registered once at module
# load; the resolver closes over the live ``_sessions`` dict so it
# always sees current state.
def _resolve_session_owner(session_id: int) -> str | None:
    sess = _sessions.get(session_id)
    return sess.owner_user_id if sess is not None else None


from autonoma.context import set_session_owner_resolver as _install_owner_resolver  # noqa: E402

_install_owner_resolver(_resolve_session_owner)


# ── Headless WebSocket stub ──────────────────────────────────────────
#
# Cron-triggered runs and other backend-only swarm spawns don't have a
# browser on the other end. ``_run_swarm`` is heavily coupled to the
# WebSocket-backed ``SessionState`` though, so the cheapest way to
# reuse the existing run loop (with checkpoints, run-summary writes,
# observability bookkeeping intact) is to feed it a stub that
# satisfies the WebSocket protocol but drops every outbound message.
#
# IMPORTANT: this is a *write-only* sink. Any inbound-receive code that
# tries to await ``receive_text`` on it will hang forever — but the
# headless launcher never goes near the inbound loop, so that path is
# never exercised.

# Headless runner symbols (``_HeadlessWebSocket``, ``_next_headless_session_id``,
# ``_run_swarm_headless``, ``_on_schedule_fire_requested``) live in
# ``autonoma._api_headless`` — re-exported below near where they're used.
_rooms: dict[int, RoomState] = {}
# Lookup by short code (uppercase A-Z + 2-9 — no I/O/0/1 to avoid
# misreads when someone reads it aloud).
_short_codes: dict[str, int] = {}

# Guards mutations to ``_rooms`` and ``_short_codes``. Room
# creation/destruction interleaves with HTTP handlers that read these
# maps, and the disconnect-cleanup path touches both: without the lock a
# concurrent ``_create_room`` and a viewer disconnect can both update
# ``_short_codes`` and leave a stale code pointing at a torn-down room
# id (observed once in dev as a 404 on a freshly issued code).
_rooms_lock: asyncio.Lock | None = None


def _get_rooms_lock() -> asyncio.Lock:
    """Lazy singleton — see ``_TASKS_LOCK_SINGLETON`` in agents.base for
    the same pattern. Keeps the module importable before the event loop
    exists (tests, CLI commands)."""
    global _rooms_lock
    if _rooms_lock is None:
        _rooms_lock = asyncio.Lock()
    return _rooms_lock


# Monotonic session-id source. ``id(ws)`` was the previous source and
# could alias across connections once the WebSocket object was GC'd and
# its memory slot reused, letting a fresh connection inherit a stale
# session's room/file state. ``itertools.count`` is atomic in CPython and
# the counter survives for the lifetime of the process.
_next_session_id: itertools.count[int] = itertools.count(1)

# Legacy admin-password brute-force guardrails. Per-connection (not per
# IP) because that's the scope we can enforce from inside the WS loop;
# hostile clients opening many WS connections are a separate concern
# that belongs on the reverse proxy.
_ADMIN_AUTH_MAX_ATTEMPTS = 5
_ADMIN_AUTH_WINDOW_SECONDS = 60.0

# Room-code probe guardrails. 6-character short codes are 34^6 ≈ 1.5 B
# combinations so brute-force is infeasible, but a determined probe
# could still enumerate every live room over time. Match the admin
# auth limits — 5 failures per minute per WS — to keep the surface
# area uniform.
_JOIN_ROOM_MAX_ATTEMPTS = 5
_JOIN_ROOM_WINDOW_SECONDS = 60.0

# Chat-command bridge (``!cheer`` / ``!cookie`` / ``!boo``) — per-viewer
# throttle so a single spectator can't spam reactions and drown out the
# swarm. 4 seconds is short enough to feel responsive (one command per
# typing cadence) and long enough that an inadvertent double-send drops
# the second copy. Keyed on session id, which is stable for the lifetime
# of a WS connection.
_VIEWER_COMMAND_THROTTLE_SECONDS = 4.0
_VIEWER_COMMAND_ICONS: dict[str, str] = {
    "cheer": "✨",
    "cookie": "🍪",
    "boo": "👎",
}

# ── Director-hint guardrails ─────────────────────────────────────────────
# Viewers can post short suggestions that surface in the Director's next
# situation report. The text reaches an LLM prompt, so the sanitizer has
# to be conservative: cap length, kill control chars, and defang JSON /
# prompt-delimiter tokens that would otherwise let a hostile viewer
# break out of the "viewer suggestions" section into the system harness.
# 10 seconds per viewer keeps a single spectator from drowning the
# situation report on their own — a busy room of 10 viewers can still
# push ~60 hints/min into the buffer (the deque drops the oldest).
_DIRECTOR_HINT_MAX_CHARS = 140
_DIRECTOR_HINT_THROTTLE_SECONDS = 10.0
_DIRECTOR_HINT_BUFFER_SIZE = 5


def _sanitize_director_hint(raw: str) -> str:
    """Defang a viewer-submitted hint before it reaches the Director's prompt.

    Rules (in order):
      1. Trim, drop empties.
      2. Strip ASCII control chars (keep regular text + spaces).
      3. Collapse internal whitespace runs to a single space so a viewer
         can't smuggle a multi-line prompt past a single-line UI.
      4. Replace ``{`` / ``}`` with ``(`` / ``)`` so the hint can't close
         the JSON/dict syntax the harness prompt uses to frame examples.
      5. Replace ``==`` runs with ``= =`` so a hint can't forge a new
         ``== SECTION ==`` header inside the situation report.
      6. Clamp to ``_DIRECTOR_HINT_MAX_CHARS``.

    Returns "" when the hint is empty after sanitization — the caller
    treats that as a silent no-op (the throttle counter is left alone so
    accidentally pressing send on a blank input doesn't lock the viewer
    out for 10s).
    """
    if not raw:
        return ""
    cleaned = raw.strip()
    if not cleaned:
        return ""
    # 2: map ASCII whitespace (newlines, tabs, CR, FF, VT) to a plain
    # space FIRST so the next step preserves word boundaries when the
    # control char dies. Without this, ``"foo\nbar"`` collapses into
    # ``"foobar"``, which is uglier and slightly easier to weaponise
    # (smuggling adjacent tokens through a single-line UI).
    cleaned = "".join(" " if ch in "\n\r\t\v\f" else ch for ch in cleaned)
    # 3: drop the remaining non-printable / control chars, then collapse
    # whitespace runs to a single space.
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() or ch == " ")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    # 4 + 5: defang section delimiters.
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    while "==" in cleaned:
        cleaned = cleaned.replace("==", "= =")
    return cleaned[:_DIRECTOR_HINT_MAX_CHARS]


async def _handle_viewer_command(
    text: str,
    *,
    session_id: int,
    room_id: int,
    last_command_at: float,
    now: float | None = None,
) -> tuple[bool, float]:
    """Parse and dispatch a ``!verb target`` chat-command bridge line.

    Pure-ish helper extracted from the WS chat handler so the parser /
    throttle / emit fan-out can be unit-tested without driving a full
    WebSocket lifecycle. Returns ``(emitted, new_last_command_at)``:

    * ``emitted`` is True only when an ``agent.emote`` actually fired
      (known verb, known agent in the room's live swarm, and the
      throttle window has elapsed since the previous successful
      command).
    * ``new_last_command_at`` is the timestamp the caller should write
      back to ``session.last_viewer_command_at``; unchanged when nothing
      emitted, set to ``now`` when an emit went through.

    Unknown verbs / missing target / no live swarm / unknown agent
    name are all silently no-ops (the chat fan-out already broadcast
    the raw text as ``viewer.chat`` upstream).
    """
    if not text or not text.startswith("!"):
        return False, last_command_at
    body = text[1:].strip()
    parts = body.split(maxsplit=1)
    verb = parts[0].lower() if parts else ""
    target_name = parts[1].strip() if len(parts) > 1 else ""
    icon = _VIEWER_COMMAND_ICONS.get(verb)
    if icon is None or not target_name:
        return False, last_command_at

    room = _rooms.get(room_id)
    swarm = room.swarm if room is not None else None
    if swarm is None:
        return False, last_command_at
    try:
        agents_map = swarm.agents
    except Exception:
        return False, last_command_at
    if target_name not in agents_map:
        return False, last_command_at

    fire_at = time.time() if now is None else now
    if (fire_at - last_command_at) < _VIEWER_COMMAND_THROTTLE_SECONDS:
        # Throttled — chat line still surfaced upstream, but no emote.
        return False, last_command_at

    token = _current_session_id.set(session_id)
    try:
        await bus.emit(
            "agent.emote",
            agent=target_name,
            icon=icon,
            ttl_ms=2500,
        )
    finally:
        _current_session_id.reset(token)
    return True, fire_at


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
            worker_model=settings.resolved_worker_model(),
            effort=settings.director_effort or None,
            worker_effort=settings.worker_effort or None,
            decision_protocol=settings.decision_protocol,
            advisor_model=settings.advisor_model,
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
            worker_model=settings.resolved_worker_model(),
            effort=settings.director_effort or None,
            worker_effort=settings.worker_effort or None,
            decision_protocol=settings.decision_protocol,
            advisor_model=settings.advisor_model,
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
            worker_model=settings.resolved_worker_model(),
            effort=settings.director_effort or None,
            worker_effort=settings.worker_effort or None,
            decision_protocol=settings.decision_protocol,
            advisor_model=settings.advisor_model,
        )
    return None


# ── Connection Manager / serializers ─────────────────────────────────────
#
# Lifted into ``autonoma._api_ws`` so this module can stay focused on
# routing and lifespan. Re-exported here so call sites and tests that
# do ``from autonoma.api import manager`` (etc.) continue to work.

from autonoma._api_ws import (  # noqa: F401 — re-exported for callers
    ConnectionManager,
    _make_event_message,
    _serialize,
    manager,
)

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
    "agent.speech_token",
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
    # 2026-10: refusals, delegated-session tool calls, and deep-work
    # lifecycle (see agents/base.py, agents/deepwork.py).
    "agent.refusal",
    "agent.tool_use",
    "deepwork.started",
    "deepwork.finished",
    "deepwork.cancelled",
    "director.intervention",
    "dream.started",
    "dream.finished",
    "managed.webhook",
    "gesture.generated",
    "gesture.retired",
    "agent.level_up",
    "agent.dream",
    "file.created",
    "task.assigned",
    "task.started",
    "task.completed",
    "help.requested",
    "review.started",
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
    # Spectator → Director advisory hints. Fanned out so every viewer
    # sees other viewers' submissions land (read-only — only the Director
    # reads the buffered text into its prompt; the broadcast is purely
    # presentational).
    "director.hint",
    # Debate Arena (Feature 20)
    "debate.started",
    "debate.resolved",
    # Mocap — global character binding changes (broadcast site-wide).
    "mocap.bindings.updated",
    # Mocap — admin-fired manual trigger. Viewers listen and briefly
    # play the bound clip on top of the normal mood/state/emote chain.
    "mocap.trigger.fired",
    # Mocap — clip library mutations (create/rename/delete). Broadcast
    # site-wide so other clients can invalidate their client-side
    # ``clipCache`` before the 5-minute TTL expires.
    "mocap.clips.updated",
    # Voice — global character voice-binding changes (broadcast site-wide).
    "voice.bindings.updated",
    # Live viewer reactions (one-tap emoji bursts). Broadcast to every
    # viewer in the originating room so the floating-emoji animation
    # plays for everyone, not just the sender.
    "live.reaction",
    # Multi-character podcast (Wave C). Same broadcast model as the
    # swarm's agent.speech_audio_* events — the /podcast page subscribes
    # to these to render captions + drive playback.
    "podcast.started",
    "podcast.line_started",
    "podcast.line_audio_start",
    "podcast.line_audio_chunk",
    "podcast.line_audio_end",
    "podcast.line_failed",
    "podcast.user_input",
    "podcast.paused",
    "podcast.resumed",
    "podcast.ended",
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
            except Exception as exc:
                logger.warning(
                    "[ws] dropping viewer sid=%s in room=%s on event=%s: %s",
                    viewer.session_id,
                    room_id,
                    event_type,
                    exc,
                )
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
        if not preset.is_default and user_id is not None and preset.owner_user_id != user_id:
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
        logger.warning(f"[Swarm] session {session_id} vanished before run could start")
        return

    name = goal.lower().replace(" ", "-")[:40]
    project = ProjectState(name=name, description=goal)
    swarm = AgentSwarm(policy=policy, llm_config=llm_config)

    session.swarm = swarm
    session.project = project

    # Bind the external input router to this swarm so any voice/live
    # chat ingress can inject directly into the run. We rebind on every
    # start (the singleton is module-level) and unbind on shutdown so a
    # stale reference can't keep the swarm pinned in memory.
    try:
        from autonoma.external_input import router as _ext_router

        _ext_router.bind_swarm(swarm)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"[Swarm:{session_id}] external_input bind failed: {exc}")

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

    # ── Feature 9: register file.created bus handler to capture history ──
    # Defined inline so it closes over ``session_id`` and the DB helper.
    async def _on_file_created(**kwargs: Any) -> None:
        # Only intercept events belonging to this session (bus emits have
        # session id on the ContextVar).
        from autonoma.context import current_session_id as _csi

        if _csi.get() != session_id:
            return
        path = kwargs.get("path", "")
        agent = kwargs.get("agent", "")
        # Retrieve content from the live project — the bus payload only
        # carries path/size/description to keep the event compact.
        sess = _sessions.get(session_id)
        if sess is None or sess.project is None:
            return
        artifact = next((f for f in sess.project.files if f.path == path), None)
        if artifact is not None:
            await _insert_file_history(
                session_id=session_id,
                path=path,
                content=artifact.content,
                created_by=agent,
            )

    bus.on("file.created", _on_file_created)

    # ── Feature 30: Periodic ProjectState checkpoint every 5 rounds ──────
    # Runs as a background task alongside the swarm loop.
    async def _checkpoint_loop() -> None:
        last_round = -1
        try:
            while True:
                await asyncio.sleep(30)  # poll every 30 s
                sess = _sessions.get(session_id)
                if sess is None or sess.swarm is None or sess.project is None:
                    continue
                current_round = getattr(sess.swarm, "_round", 0)
                # Save every 5 rounds (and avoid saving the same round twice)
                if current_round > 0 and current_round % 5 == 0 and current_round != last_round:
                    last_round = current_round
                    try:
                        state_json = sess.project.to_json()
                        await _upsert_checkpoint(session_id, current_round, state_json)
                        logger.debug("[checkpoint:%s] saved at round %s", session_id, current_round)
                    except Exception as cp_exc:
                        logger.warning("[checkpoint:%s] failed: %s", session_id, cp_exc)
        except asyncio.CancelledError:
            # On clean shutdown, save a final checkpoint.
            sess = _sessions.get(session_id)
            if sess is not None and sess.project is not None:
                try:
                    current_round = getattr(sess.swarm, "_round", 0) if sess.swarm else 0
                    state_json = sess.project.to_json()
                    await _upsert_checkpoint(session_id, current_round, state_json)
                except Exception as cp_exc:
                    logger.warning(
                        "[checkpoint:%s] final shutdown checkpoint failed: %s",
                        session_id,
                        cp_exc,
                    )
            raise

    state_checkpoint_task = asyncio.create_task(_checkpoint_loop())

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
        # Cancel the two background tasks.
        if checkpoint_task is not None:
            checkpoint_task.cancel()
        state_checkpoint_task.cancel()
        try:
            await state_checkpoint_task
        except (asyncio.CancelledError, Exception):
            pass

        # Un-register the per-run file.created handler.
        bus.off("file.created", _on_file_created)

        # ── Feature 12: persist run summary ──────────────────────────────
        try:
            tasks_done = sum(1 for t in project.tasks if t.status.value == "done")
            tasks_failed = sum(1 for t in project.tasks if t.status.value == "blocked")
            import hashlib as _hl
            import json as _json

            policy_hash = (
                _hl.md5(
                    _json.dumps(policy.model_dump(mode="json"), sort_keys=True).encode()
                ).hexdigest()
                if policy is not None
                else ""
            )
            await _record_run_summary(
                session_id=session_id,
                goal=goal,
                agent_count=len(swarm.agents),
                task_count=len(project.tasks),
                tasks_done=tasks_done,
                tasks_failed=tasks_failed,
                total_rounds=swarm._round,
                llm_calls=0,  # placeholder — token tracking pending
                preset_id=preset_id or "",
                policy_hash=policy_hash,
            )
        except Exception as rs_exc:
            logger.warning("[run_summary] could not persist: %s", rs_exc)

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


def _apply_policy_side_effects(session_id: int, policy: HarnessPolicyContent | None) -> None:
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
                    [a.name for a in sess.swarm.agents.values()] if shape_variant == "on" else []
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
            return await asyncio.get_event_loop().run_in_executor(None, _get_snapshot, session_id)
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
                    relationships.append(
                        {
                            "from": a,
                            "to": b,
                            "trust": rel.trust,
                        }
                    )

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
                cookies.append(
                    {
                        "recipient": name,
                        "fortune": cookie.fortune,
                        "bonus_xp": cookie.bonus_xp,
                    }
                )

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
    # 2026-05 feature pack — surface which optional surfaces are on so
    # operators can sanity-check from the very first log line. Each is
    # a one-tag "feature=on/off" entry rather than a paragraph so the
    # log stays scannable.
    logger.info(
        "[startup:2026-05] mcp=%s vmc=%s otel=%s prom=%s betting=%s "
        "anomaly=%s ci_loop=%s retirement=%s highlights=%s inspire=%s "
        "memoir_every=%s",
        "on" if settings.mcp_server_enabled else "off",
        "on" if settings.vmc_bridge_enabled else "off",
        "on" if settings.otel_endpoint else "off",
        "on" if settings.prometheus_metrics_enabled else "off",
        "on" if settings.viewer_betting_enabled else "off",
        "on" if settings.anomaly_detection_enabled else "off",
        "on" if settings.ci_loop_enabled else "off",
        "on" if settings.retirement_enabled else "off",
        "on" if settings.highlights_enabled else "off",
        "on" if settings.inspire_enabled else "off",
        settings.memoir_compact_every_rounds or "off",
    )


async def _warmup_omnivoice() -> None:
    """Kick off the configured TTS model load at startup.

    Multi-GB models otherwise pay the first-request 30–60 s cold-load
    cost in front of nginx's default 60 s read timeout, which surfaces
    as a 504 to the user. Gated on ``settings.tts_provider`` so a
    TTS-disabled deploy avoids the resident-memory cost.

    Renamed-but-not-renamed: the function is still ``_warmup_omnivoice``
    so the existing ``lifespan`` callsite doesn't churn, but it now
    dispatches by provider so VibeVoice gets the same treatment.
    """
    provider = settings.tts_provider
    if provider == "omnivoice":
        try:
            from autonoma.tts_omnivoice import warmup_shared_client
        except ImportError:
            return
        await warmup_shared_client()
    elif provider == "omnivoice-mlx":
        try:
            from autonoma.tts_omnivoice_mlx import warmup_shared_client
        except ImportError:
            return
        await warmup_shared_client()
    elif provider == "vibevoice":
        try:
            from autonoma.tts_vibevoice import warmup_shared_client
        except ImportError:
            return
        await warmup_shared_client()


async def _warmup_asr() -> None:
    """Kick off Cohere ASR model load in the background at startup.

    Same rationale as ``_warmup_omnivoice``: the first push-to-talk
    otherwise pays HuggingFace download (multi-GB on a cold cache) and
    model load — together easily over a minute. Gated on
    ``settings.voice_asr_provider`` so ASR-disabled deploys skip both
    the bandwidth and resident memory cost.
    """
    if getattr(settings, "voice_asr_provider", "cohere") == "none":
        return
    try:
        from autonoma.voice.asr import warmup_asr_provider
    except ImportError:
        return
    await warmup_asr_provider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_event_bridge()
    await _bootstrap_admin_user()
    await _bootstrap_default_harness_policy()
    _log_startup_summary()
    # Live/broadcast: route milestone events to clip-recorder triggers.
    from autonoma.routers.live import register_autoclip_hooks

    register_autoclip_hooks()
    warmup_task: asyncio.Task[None] | None = None
    if settings.tts_provider in ("omnivoice", "omnivoice-mlx", "vibevoice"):
        warmup_task = asyncio.create_task(_warmup_omnivoice())
    asr_warmup_task: asyncio.Task[None] | None = None
    if getattr(settings, "voice_asr_provider", "cohere") != "none":
        asr_warmup_task = asyncio.create_task(_warmup_asr())
    # Boot the scheduler poll loop. Stopping it on shutdown prevents test
    # runners from leaving a stray task hanging across repeated app
    # instantiation. Subscribe the headless swarm launcher to scheduler
    # fires here (not at module load) so the bus has its handler list
    # initialised in the same event loop the swarm runs on.
    from autonoma.scheduler import scheduler_runner

    scheduler_runner.start()
    bus.on("schedule.fire_requested", _on_schedule_fire_requested)

    # Sandbox runtime (srt) for run_code: install the pinned npm package
    # into data_dir on first boot when it is missing. Background, best
    # effort — the sandbox falls back to the OS-native backend until it
    # lands (see autonoma.sandbox_runtime).
    from autonoma.sandbox_runtime import schedule_ensure_srt

    srt_task = schedule_ensure_srt()

    # 2026-05 feature pack lifespan hooks. Each is best-effort —
    # individual failures must never block the rest of the boot since
    # they only power optional surfaces (metrics scrape, OBS overlay,
    # VRChat bridge).
    try:
        from autonoma.observability_otel import setup_otel

        setup_otel()
    except Exception:
        logger.exception("[startup] OTel setup failed; continuing without it")

    try:
        from autonoma.highlights import get_recorder as _get_highlight_recorder

        _highlight_recorder = _get_highlight_recorder()
        _highlight_recorder.start()
    except Exception:
        _highlight_recorder = None
        logger.exception("[startup] highlights recorder failed; continuing")

    vmc_task: asyncio.Task[None] | None = None
    if getattr(settings, "vmc_bridge_enabled", False):
        try:
            from autonoma.vmc import start_listening_to_bus as _vmc_start

            vmc_task = asyncio.create_task(_vmc_start(), name="vmc-bridge")
        except Exception:
            logger.exception("[startup] VMC bridge boot failed; disabling")

    # MCP streamable-HTTP transport needs a running session manager for
    # the app's lifetime; the legacy JSON-RPC endpoint does not.
    mcp_stack = AsyncExitStack()
    if settings.mcp_server_enabled:
        try:
            from autonoma.mcp import server as _mcp_server

            await mcp_stack.enter_async_context(_mcp_server.lifespan())
        except Exception:
            logger.exception("[startup] MCP transport boot failed; /mcp will answer 503")

    try:
        yield
    finally:
        await mcp_stack.aclose()
        bus.off("schedule.fire_requested", _on_schedule_fire_requested)
        await scheduler_runner.stop()
        if _highlight_recorder is not None:
            try:
                _highlight_recorder.stop()
            except Exception:
                logger.exception("[shutdown] highlights stop failed")
        if vmc_task is not None and not vmc_task.done():
            vmc_task.cancel()
            try:
                await vmc_task
            except (asyncio.CancelledError, Exception):
                pass
        # Warmup tasks: cancel and await so the shutdown sequence is
        # actually quiescent — bare ``cancel()`` returns immediately
        # while the underlying HF model download (potentially
        # multi-GB) is still mid-syscall, which would otherwise leave
        # the cache in a partial state across container restarts.
        # ``return_exceptions=True`` so any single warmup failure
        # doesn't block the rest of the cleanup.
        pending_warmups = [
            t for t in (warmup_task, asr_warmup_task, srt_task) if t is not None and not t.done()
        ]
        for t in pending_warmups:
            t.cancel()
        if pending_warmups:
            await asyncio.gather(*pending_warmups, return_exceptions=True)
        _unregister_event_bridge()


app = FastAPI(title="Autonoma API", version="0.1.0", lifespan=lifespan)

# Session cookie + CORS helpers live in ``_api_cookies`` — re-exported
# here so the existing call sites (``auth_login``/``auth_logout``/
# ``auth_guest`` etc.) keep working unchanged.
from autonoma._api_cookies import (  # noqa: E402, F401 — re-exported
    _clear_session_cookie,
    _cookie_is_secure,
    _cookie_samesite,
    _resolve_cors_origins,
    _set_session_cookie,
)

# ── Extracted route groups ───────────────────────────────────────────
# Auth, harness, workspace, runs, health, models and mocap routes live
# in ``autonoma.routers.*`` (one APIRouter each). They are included here,
# at the point where they used to be declared, so registration order —
# and therefore FastAPI's first-match behaviour — is unchanged. Each
# router lazy-imports the live registries from this module.
from autonoma.routers import auth as _auth_router
from autonoma.routers import harness as _harness_router
from autonoma.routers import health as _health_router
from autonoma.routers import mocap as _mocap_router
from autonoma.routers import models as _models_router
from autonoma.routers import runs as _runs_router
from autonoma.routers import workspace as _workspace_router

# Names that moved with their routers but are still imported from
# ``autonoma.api`` by tests and other modules.
from autonoma.routers.auth import USERNAME_PATTERN  # noqa: F401 — re-exported
from autonoma.routers.mocap import (  # noqa: F401 — re-exported
    _MOCAP_QUOTA_BYTES,
    _MOCAP_QUOTA_CLIPS,
    _check_mocap_upload_rate,
    _mocap_upload_history,
)
from autonoma.routers.workspace import (  # noqa: F401 — re-exported
    _require_session,
    _require_session_owner,
    _session_project,
)

app.include_router(_auth_router.router)
app.include_router(_harness_router.router)
app.include_router(_workspace_router.router)
app.include_router(_runs_router.router)
app.include_router(_health_router.router)
app.include_router(_models_router.router)
app.include_router(_mocap_router.router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cleanup_session(session_id: int) -> None:
    """Remove a session entry and dispose of session-scoped workers.

    Safe to call multiple times — ``shutdown_worker`` is a no-op when
    the key is already absent. The swarm task's own finally-block also
    shuts the worker down on normal completion; this belt-and-suspenders
    call catches the disconnect path where the swarm was never started
    (no task → no finally) and the edge case where a BaseException
    escaped the task body before its finally ran.
    """
    _sessions.pop(session_id, None)
    try:
        from autonoma.tts_worker import shutdown_worker

        shutdown_worker(session_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"[_cleanup_session:{session_id}] shutdown_worker failed: {exc!r}")


async def _cancel_session_task(session: SessionState) -> None:
    """Cancel the session's running swarm task (if any) and await its exit."""
    if session.task is not None and not session.task.done():
        session.task.cancel()
        try:
            await session.task
        except (asyncio.CancelledError, Exception):
            pass
    session.task = None


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


async def _create_room_for(session: SessionState) -> RoomState:
    """Create a new room owned by ``session`` and reroute the session
    into it. Returns the room (also stored in ``_rooms``).

    Holds ``_get_rooms_lock()`` across the short-code allocation and the
    two-map insert so a concurrent disconnect cleanup can't race with the
    room becoming visible — see the comment on ``_rooms_lock``.
    """
    async with _get_rooms_lock():
        code = _generate_short_code()
        room = RoomState(
            room_id=session.session_id,
            owner_session_id=session.session_id,
            short_code=code,
            started_at=time.time(),
        )
        _rooms[room.room_id] = room
        _short_codes[code] = room.room_id
        session.room_id = room.room_id
        return room


def _viewers_in_room(room_id: int) -> list[SessionState]:
    """Return every live session currently bound to ``room_id``.

    Iteration over ``_sessions.values()`` happens without a lock — in
    CPython the dict iteration is safe (no segfault on concurrent
    mutation) but the resulting list may include sessions that were
    removed mid-iteration or miss ones that joined mid-iteration. Both
    outcomes are acceptable for our use cases: callers either
    broadcast (a missed viewer just gets the next event) or count for
    UX (a one-frame stale count is invisible). Adding a lock here was
    measured to introduce contention on the hot bus-event path with
    no observable benefit; the snapshot is taken to a list so the
    iteration is bounded.
    """
    # ``list(...)`` snapshots the dict view so subsequent iteration in
    # callers can't observe later mutations of ``_sessions``.
    return [s for s in list(_sessions.values()) if s.room_id == room_id]


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


@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    # Using a monotonic counter instead of ``id(ws)`` so the id can't
    # collide with a previous (GC'd) connection's session, which would
    # let a fresh viewer inherit the former room's bus subscriptions.
    session = SessionState(ws=ws, session_id=next(_next_session_id))
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
        await ws.send_text(
            json.dumps(
                {
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
                }
            )
        )

        # ── Cookie-based auto-auth ───────────────────────────────────
        # If the WS handshake carried a valid HTTP session cookie, promote
        # the session automatically instead of forcing the viewer back
        # through the legacy `authenticate` command. Without this the
        # AuthModal reopens right after HTTP login and only the "Legacy
        # admin" tab can satisfy the WS, which is what users were seeing.
        #
        # Guest cookies (``guest:<uuid>``) are noted but NOT promoted
        # via the server's LLM config — handing the server's API key to
        # an anonymous guest would be a credential leak. Guests still
        # need to send a follow-up ``type=user`` authenticate message
        # carrying their own provider/api_key, which the existing flow
        # below handles.
        cookie_token = ws.cookies.get(SESSION_COOKIE_NAME)
        cookie_user_id = read_session_token(cookie_token or "")
        if cookie_user_id and is_guest_user_id(cookie_user_id):
            session.owner_user_id = cookie_user_id
            logger.info(
                f"[WS:{session.session_id}] Guest cookie present "
                f"(user_id={cookie_user_id}); awaiting user-supplied API key"
            )
        else:
            cookie_user = await get_user_by_id(cookie_user_id) if cookie_user_id else None
            if cookie_user is not None and cookie_user.status == "active":
                llm_cfg = _build_admin_llm_config()
                if llm_cfg is not None:
                    session.llm_config = llm_cfg
                    session.is_admin = cookie_user.role == "admin"
                    session.owner_user_id = cookie_user.id
                    logger.info(
                        f"[WS:{session.session_id}] Cookie auth ok "
                        f"(user={cookie_user.username}, role={cookie_user.role}, "
                        f"provider={llm_cfg.provider})"
                    )
                    await manager.send_to_ws(
                        ws,
                        "auth.success",
                        {
                            "is_admin": session.is_admin,
                            "provider": llm_cfg.provider,
                            "model": llm_cfg.model,
                        },
                    )

        # ── Initial state snapshot (always idle for a fresh connection) ──
        snapshot = await _get_snapshot_coalesced(session.session_id)
        await ws.send_text(json.dumps({"event": "snapshot", "data": snapshot}))

        # ── Command loop ──
        while True:
            data = await ws.receive_text()
            # Malformed frames must not drop the WS — otherwise any
            # viewer can crash their own room by sending garbage, and a
            # buggy frontend takes down the whole connection on a single
            # bad message instead of surfacing a normal error toast.
            try:
                msg = json.loads(data)
            except (json.JSONDecodeError, ValueError) as parse_err:
                logger.warning(f"[WS:{session.session_id}] Ignored malformed frame: {parse_err}")
                await manager.send_to_ws(
                    ws,
                    "error",
                    {
                        "message": "Malformed command (invalid JSON).",
                    },
                )
                continue
            if not isinstance(msg, dict):
                await manager.send_to_ws(
                    ws,
                    "error",
                    {
                        "message": "Malformed command (expected JSON object).",
                    },
                )
                continue
            cmd = msg.get("command")

            # ── authenticate ──────────────────────────────────────────
            if cmd == "authenticate":
                auth_type = msg.get("type")

                if auth_type == "admin":
                    # Throttle per-connection brute force. A fresh window
                    # opens once the cooldown elapses so an honest typo
                    # doesn't permanently lock the viewer out.
                    now = time.monotonic()
                    if (
                        session.failed_auth_attempts >= _ADMIN_AUTH_MAX_ATTEMPTS
                        and (now - session.last_failed_auth_at) < _ADMIN_AUTH_WINDOW_SECONDS
                    ):
                        retry_in = int(
                            _ADMIN_AUTH_WINDOW_SECONDS - (now - session.last_failed_auth_at)
                        )
                        logger.warning(
                            f"[WS:{session.session_id}] Admin auth rate-limited "
                            f"(attempts={session.failed_auth_attempts})"
                        )
                        await manager.send_to_ws(
                            ws,
                            "auth.failed",
                            {
                                "message": (
                                    f"너무 많은 시도가 감지되었습니다. "
                                    f"{retry_in}초 뒤에 다시 시도해주세요."
                                ),
                            },
                        )
                        continue
                    # Drop the failure count if the window already expired.
                    if (
                        session.failed_auth_attempts > 0
                        and (now - session.last_failed_auth_at) >= _ADMIN_AUTH_WINDOW_SECONDS
                    ):
                        session.failed_auth_attempts = 0

                    if not settings.admin_password:
                        await manager.send_to_ws(
                            ws,
                            "auth.failed",
                            {
                                "message": "관리자 계정이 서버에 설정되어 있지 않습니다.",
                            },
                        )
                    else:
                        # Constant-time comparison — a plain ``!=`` on
                        # strings leaks the password's length and first
                        # mismatching byte via timing.
                        submitted = msg.get("password") or ""
                        if not hmac.compare_digest(str(submitted), settings.admin_password):
                            session.failed_auth_attempts += 1
                            session.last_failed_auth_at = now
                            await manager.send_to_ws(
                                ws,
                                "auth.failed",
                                {
                                    "message": "관리자 비밀번호가 올바르지 않습니다.",
                                },
                            )
                        else:
                            llm_cfg = _build_admin_llm_config()
                            if llm_cfg is None:
                                await manager.send_to_ws(
                                    ws,
                                    "auth.failed",
                                    {
                                        "message": "서버에 API 키가 설정되어 있지 않습니다. 관리자에게 문의하세요.",
                                    },
                                )
                            else:
                                session.failed_auth_attempts = 0
                                session.llm_config = llm_cfg
                                session.is_admin = True
                                logger.info(
                                    f"[WS:{session.session_id}] Admin authenticated "
                                    f"(provider={llm_cfg.provider})"
                                )
                                await manager.send_to_ws(
                                    ws,
                                    "auth.success",
                                    {
                                        "is_admin": True,
                                        "provider": llm_cfg.provider,
                                        "model": llm_cfg.model,
                                    },
                                )

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
                        logger.info(f"[WS:{session.session_id}] User auth rejected: {error}")
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
                            # User-supplied keys: workers use the cheap tier
                            # only on Anthropic (the configured worker model
                            # is a Claude id); other providers reuse ``model``.
                            worker_model=(
                                settings.resolved_worker_model()
                                if provider == "anthropic"
                                else model
                            ),
                            effort=settings.director_effort or None,
                            worker_effort=settings.worker_effort or None,
                            decision_protocol=settings.decision_protocol,
                            advisor_model=settings.advisor_model,
                        )
                        session.llm_config = llm_cfg
                        logger.info(
                            f"[WS:{session.session_id}] User authenticated "
                            f"(provider={provider}, model={model}, "
                            f"key_len={len(api_key)}, base_url="
                            f"{effective_base_url or '(n/a)'})"
                        )
                        await manager.send_to_ws(
                            ws,
                            "auth.success",
                            {
                                "is_admin": False,
                                "provider": provider,
                                "model": model,
                            },
                        )
                else:
                    await manager.send_to_ws(
                        ws,
                        "auth.failed",
                        {
                            "message": "인증 방식이 올바르지 않습니다 (admin 또는 user).",
                        },
                    )

            # ── get_snapshot ──────────────────────────────────────────
            elif cmd == "get_snapshot":
                snap = await _get_snapshot_coalesced(session.session_id)
                await manager.send_to_ws(ws, "snapshot", snap)

            # ── start ─────────────────────────────────────────────────
            elif cmd == "start":
                if session.llm_config is None:
                    await manager.send_to_ws(
                        ws,
                        "auth.required",
                        {
                            "message": "스웜을 시작하려면 먼저 로그인해주세요.",
                        },
                    )
                    continue

                goal = msg.get("goal", "").strip()
                if not goal:
                    await manager.send_to_ws(
                        ws,
                        "error",
                        {
                            "message": "Goal is required",
                        },
                    )
                elif session.task is not None and not session.task.done():
                    await manager.send_to_ws(
                        ws,
                        "error",
                        {
                            "message": "Swarm is already running in this session",
                        },
                    )
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
                        await manager.send_to_ws(
                            ws,
                            "error",
                            {
                                "message": policy_err,
                            },
                        )
                        continue

                    # Materialize a real room so other viewers can join via
                    # short code. ``room_id == session.session_id`` is
                    # preserved so the existing ContextVar routing keeps
                    # working without a parallel id space.
                    room = _rooms.get(session.session_id)
                    if room is None:
                        room = await _create_room_for(session)
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
                    await manager.send_to_ws(
                        ws,
                        "swarm.starting",
                        {
                            "goal": goal,
                            "room_code": room.short_code,
                        },
                    )

            # ── join_room ─────────────────────────────────────────────
            # Other viewers attach to an already-running swarm by short
            # code. The host's `start` reply contains the code; the
            # frontend either renders it as a sharable link
            # (?room=ABCXYZ) or shows it for read-aloud copy.
            elif cmd == "join_room":
                code = (msg.get("code") or "").strip().upper()
                # Per-WS throttle: refuse further attempts once the
                # caller has burned through ``_JOIN_ROOM_MAX_ATTEMPTS``
                # failures inside the rolling window. Mirrors the admin
                # auth guard above. Reset whenever the window expires.
                _now = time.time()
                if (
                    session.failed_join_attempts >= _JOIN_ROOM_MAX_ATTEMPTS
                    and (_now - session.last_failed_join_at) < _JOIN_ROOM_WINDOW_SECONDS
                ):
                    await manager.send_to_ws(
                        ws,
                        "room.join_failed",
                        {
                            "message": "Too many join attempts. Wait a moment and try again.",
                        },
                    )
                elif not code:
                    await manager.send_to_ws(
                        ws,
                        "room.join_failed",
                        {
                            "message": "Room code required.",
                        },
                    )
                else:
                    target_room_id = _short_codes.get(code)
                    target_room = _rooms.get(target_room_id) if target_room_id else None
                    if target_room is None:
                        # Bump the failure counter so brute-forcers hit
                        # the throttle quickly. ``last_failed_join_at``
                        # establishes the window's right edge.
                        session.failed_join_attempts += 1
                        session.last_failed_join_at = _now
                        await manager.send_to_ws(
                            ws,
                            "room.join_failed",
                            {
                                "message": "Room not found or already ended.",
                            },
                        )
                    elif target_room_id == session.session_id:
                        await manager.send_to_ws(
                            ws,
                            "room.join_failed",
                            {
                                "message": "You're already the host of this room.",
                            },
                        )
                    else:
                        # ``target_room`` was looked up by this id, so it
                        # is a real room id here (narrows ``int | None``).
                        assert target_room_id is not None
                        # Successful join — reset the counter so a user
                        # who joins a room legitimately isn't penalised
                        # for typos earlier in the session.
                        session.failed_join_attempts = 0
                        session.last_failed_join_at = 0.0
                        # Fetch the snapshot BEFORE joining the room so the
                        # client receives a consistent state baseline before
                        # any live events from the room can reach it.
                        # (Setting session.room_id first would let bus events
                        # fan-out to this viewer during the async snapshot
                        # fetch, causing events to arrive before the snapshot.)
                        snap = await _get_snapshot_coalesced(target_room_id)

                        # Leave any prior room (and notify it) before
                        # joining the new one. The "private" default room
                        # has no other viewers so this is a no-op there.
                        prev_room_id = session.room_id
                        session.room_id = target_room_id
                        await manager.send_to_ws(
                            ws,
                            "room.joined",
                            {
                                "code": code,
                                "is_owner": False,
                            },
                        )
                        # Send the pre-fetched snapshot immediately after
                        # join confirmation so the client can render the
                        # current scene before the live event stream starts.
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
            #
            # Chat-command bridge: lines beginning with ``!`` are
            # interpreted as viewer reactions (`!cheer Alex`, `!cookie
            # Alex`, `!boo Alex`) and trigger an ``agent.emote`` so the
            # swarm reacts visibly. The message still fans out as chat
            # so other viewers see what was typed; an unknown command
            # or unknown agent is silently ignored (no error reply).
            elif cmd == "chat":
                text = (msg.get("text") or "").strip()[:280]
                if not text:
                    pass
                else:
                    name = session.display_name or f"anon-{session.session_id % 1000}"
                    is_command = text.startswith("!")
                    payload = {
                        "from": name,
                        "text": text,
                        "is_owner": session.session_id == session.room_id,
                        "is_command": is_command,
                    }
                    for v in _viewers_in_room(session.room_id):
                        await manager.send_to_ws(v.ws, "viewer.chat", payload)

                    # Viewer command bridge — parse + dispatch as emote.
                    # Logic factored into ``_handle_viewer_command`` so the
                    # parser/throttle path can be unit-tested without
                    # standing up a full WS lifecycle.
                    if is_command:
                        _emitted, session.last_viewer_command_at = await _handle_viewer_command(
                            text,
                            session_id=session.session_id,
                            room_id=session.room_id,
                            last_command_at=session.last_viewer_command_at,
                        )

            # ── director_hint ─────────────────────────────────────────
            # Spectator suggestion that lands in the Director's advisory
            # buffer for its NEXT think_and_act round. Heavily guarded
            # because the text reaches an LLM prompt:
            #   - per-viewer cooldown (separate from chat throttle)
            #   - sanitizer defangs JSON / section-header tokens
            #   - capped buffer drops oldest hints under load
            #   - rendered with explicit "advisory only" framing so a
            #     hostile hint is weighed, not executed
            # The Director itself does the buffer-and-drain; the WS layer
            # just authenticates the room, applies the throttle, and
            # broadcasts a presentational ``director.hint`` event so other
            # viewers can see whose hint landed.
            elif cmd == "director_hint":
                room = _rooms.get(session.room_id)
                _now = time.time()
                cooldown_remaining = max(
                    0.0,
                    _DIRECTOR_HINT_THROTTLE_SECONDS - (_now - session.last_director_hint_at),
                )
                cleaned = _sanitize_director_hint(msg.get("text") or "")
                if not cleaned:
                    # Empty / whitespace-only hint — silent no-op, do NOT
                    # bump the throttle so a fat-fingered enter on an empty
                    # composer doesn't lock the viewer out for 10s.
                    pass
                elif room is None or room.swarm is None:
                    await manager.send_to_ws(
                        ws,
                        "director.hint_rejected",
                        {"reason": "no_active_swarm"},
                    )
                elif cooldown_remaining > 0:
                    await manager.send_to_ws(
                        ws,
                        "director.hint_rejected",
                        {
                            "reason": "throttled",
                            "retry_in_ms": int(cooldown_remaining * 1000),
                        },
                    )
                else:
                    viewer_name = session.display_name or f"anon-{session.session_id % 1000}"
                    queued = False
                    try:
                        queued = room.swarm.add_viewer_hint(viewer_name, cleaned)
                    except (AttributeError, RuntimeError) as e:
                        # Swarm went stale between the guard and the call.
                        # Surface a soft reject so the UI can re-enable the
                        # composer, then move on.
                        logger.warning(
                            f"[WS:{session.session_id}] director_hint "
                            f"add_viewer_hint race (swarm stale): {e!r}"
                        )
                    if queued:
                        session.last_director_hint_at = _now
                        token = _current_session_id.set(session.session_id)
                        try:
                            await bus.emit(
                                "director.hint",
                                viewer=viewer_name,
                                text=cleaned,
                            )
                        finally:
                            _current_session_id.reset(token)
                    else:
                        await manager.send_to_ws(
                            ws,
                            "director.hint_rejected",
                            {"reason": "no_director"},
                        )

            # ── viewer_overlay (cursors + stickers, feature #6) ──
            # Multi-viewer overlay broadcast: spectators publish their
            # mouse position (kind="cursor") or fling an emoji sticker
            # (kind="sticker"); we relay to every other viewer in the
            # same room. Self-echo is excluded. Coordinates are
            # normalised 0..1 by the client; we trust+clamp here so a
            # malformed sender can't push payloads off-screen for
            # everyone else. The kind enum is whitelisted so future
            # additions need a server-side decision before they fan out.
            elif cmd == "viewer_overlay":
                kind = str(msg.get("kind") or "")
                if kind not in ("cursor", "sticker"):
                    pass
                else:

                    def _clamp(v: object) -> float:
                        try:
                            f = float(v)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            return 0.5
                        return max(0.0, min(1.0, f))

                    sender_id = (
                        str(msg.get("viewerId"))
                        if msg.get("viewerId") is not None
                        else f"sess-{session.session_id}"
                    )
                    payload = {
                        "kind": kind,
                        "viewerId": sender_id[:64],
                        "displayName": (session.display_name or str(msg.get("displayName") or ""))[
                            :32
                        ],
                        "x": _clamp(msg.get("x")),
                        "y": _clamp(msg.get("y")),
                    }
                    if kind == "sticker":
                        payload["emoji"] = str(msg.get("emoji") or "✨")[:8]
                    for v in _viewers_in_room(session.room_id):
                        if v.session_id == session.session_id:
                            continue  # don't echo back to sender
                        await manager.send_to_ws(v.ws, "viewer_overlay", payload)

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
                logger.info(f"[WS:{session.session_id}] Session reset — returning to idle")
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
                        logger.error(f"[WS:{session.session_id}] Failed to pickup cookie: {e!r}")
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
                            "title": ("The audience cheers wildly! Agents feel inspired!"),
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
                        await manager.send_to_ws(
                            ws,
                            "chat.message",
                            {
                                "text": "/cookie: swarm not running",
                                "source": "system",
                                "target": "",
                            },
                        )
                    else:
                        swarm = session.swarm
                        if not target_name:
                            candidates = [n for n in swarm.agents.keys() if n != "Director"]
                            target_name = candidates[0] if candidates else ""
                        if target_name not in swarm.agents:
                            await manager.send_to_ws(
                                ws,
                                "chat.message",
                                {
                                    "text": f"/cookie: unknown agent '{target_name}'",
                                    "source": "system",
                                    "target": "",
                                },
                            )
                        else:
                            token = _current_session_id.set(session.session_id)
                            try:
                                cookie = swarm.fortune_jar.give_cookie(target_name, swarm._round)
                                if cookie is None:
                                    await manager.send_to_ws(
                                        ws,
                                        "chat.message",
                                        {
                                            "text": f"/cookie: {target_name} already has one",
                                            "source": "system",
                                            "target": "",
                                        },
                                    )
                                else:
                                    await bus.emit(
                                        "fortune.given",
                                        agent=target_name,
                                        fortune=cookie.fortune,
                                    )
                            finally:
                                _current_session_id.reset(token)
                else:
                    await manager.send_to_ws(
                        ws,
                        "chat.message",
                        {
                            "text": text,
                            "source": "user",
                            "target": target or "",
                        },
                    )
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
                            await session.swarm.inject_human_message(text, target=target)
                        except (AttributeError, RuntimeError) as e:
                            logger.warning(
                                f"[WS:{session.session_id}] "
                                f"inject_human_message race "
                                f"(swarm stale): {e!r}"
                            )
                        except Exception as e:
                            logger.error(
                                f"[WS:{session.session_id}] Failed to inject human message: {e!r}"
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
        # disconnecting must not stop the show. The lookup + pop happens
        # under ``_rooms_lock`` so a concurrent ``_create_room_for`` can't
        # interleave and leave a half-registered room behind.
        async with _get_rooms_lock():
            owned_room = _rooms.pop(session.session_id, None)
            if owned_room is not None:
                _short_codes.pop(owned_room.short_code, None)
        if owned_room is not None:
            # Cancelling the task touches the swarm and can await a while;
            # do it OUTSIDE the lock so viewer-side reads stay responsive.
            await _cancel_session_task(session)
        manager.disconnect(ws)
        _cleanup_session(session.session_id)
        # If this session was a *viewer* (not the owner), notify the
        # remaining viewers so the audience count updates.
        if owned_room is None and room_id != session.session_id:
            await _notify_room_membership(room_id)


# ── /api/voice/* — moved to autonoma.routers.voice ───────────────────
# Voice endpoints + helpers live in ``src/autonoma/routers/voice.py``
# and are mounted on ``app`` via ``include_router``. Kept this breadcrumb
# so grepping for ``voice`` in this file still points the right way.

# ── Feature routers ──────────────────────────────────────────────────
# Every other route group is an ``APIRouter`` under ``autonoma.routers``
# (voice, streaming/vision/bridges, personas, battle, playback, standup,
# sign language, the 2026-05 feature pack, ...). All are additive and
# live under distinct prefixes; each lazy-imports what it needs from this
# module so importing a router is cheap and cycle-free.
import autonoma.routers.ab_compare as _ab_compare_router
import autonoma.routers.achievements as _achievements_router
import autonoma.routers.agents as _agents_router
import autonoma.routers.anomalies as _anomalies_router
import autonoma.routers.bridges as _bridges_router
import autonoma.routers.clips as _clips_router
import autonoma.routers.coordinator as _coordinator_router
import autonoma.routers.custom_achievements as _custom_achievements_router
import autonoma.routers.cutscenes as _cutscenes_router
import autonoma.routers.draft as _draft_router
import autonoma.routers.fingerspell as _fingerspell_router
import autonoma.routers.gestures as _gestures_router
import autonoma.routers.highlights as _highlights_router
import autonoma.routers.inspire as _inspire_router
import autonoma.routers.leaderboard as _leaderboard_router
import autonoma.routers.live as _live_router
import autonoma.routers.live_share as _live_share_router
import autonoma.routers.metrics as _metrics_router
import autonoma.routers.mocap_live as _mocap_live_router
import autonoma.routers.persona_breed as _persona_breed_router
import autonoma.routers.personas as _personas_router
import autonoma.routers.playback as _playback_router
import autonoma.routers.podcast as _podcast_router
import autonoma.routers.points as _points_router
import autonoma.routers.quest_templates as _quest_templates_router
import autonoma.routers.quests as _quests_router
import autonoma.routers.scheduler as _scheduler_router
import autonoma.routers.sign as _sign_router
import autonoma.routers.standup as _standup_router
import autonoma.routers.swarm_battle as _battle_router
import autonoma.routers.translate as _translate_router
import autonoma.routers.viewer_betting as _viewer_betting_router
import autonoma.routers.vision as _vision_router
import autonoma.routers.voice_consent as _voice_consent_router

# ── Headless swarm launcher (extracted) ──────────────────────────────
# Re-export so call sites (``mcp/server.py``, scheduler bus handlers,
# tests that patch ``api_module._on_schedule_fire_requested``) keep
# working unchanged.
from autonoma._api_headless import (  # noqa: F401 — re-exported
    _HeadlessWebSocket,
    _next_headless_session_id,
    _on_schedule_fire_requested,
    _run_swarm_headless,
)
from autonoma.routers import voice as _voice_router  # noqa: E402

app.include_router(_voice_router.router)
app.include_router(_agents_router.router)
app.include_router(_bridges_router.router)
app.include_router(_cutscenes_router.router)
app.include_router(_scheduler_router.router)
app.include_router(_gestures_router.router)
import autonoma.routers.managed as _managed_router  # noqa: E402

app.include_router(_managed_router.router)
app.include_router(_translate_router.router)
app.include_router(_live_router.router)
app.include_router(_personas_router.router)
app.include_router(_playback_router.router)
app.include_router(_sign_router.router)
app.include_router(_standup_router.router)
app.include_router(_battle_router.router)
app.include_router(_podcast_router.router)
app.include_router(_vision_router.router)
app.include_router(_coordinator_router.router)
app.include_router(_highlights_router.router)
app.include_router(_inspire_router.router)
app.include_router(_achievements_router.router)
app.include_router(_custom_achievements_router.router)
app.include_router(_persona_breed_router.router)
app.include_router(_quests_router.router)
app.include_router(_points_router.router)
app.include_router(_quest_templates_router.router)
app.include_router(_voice_consent_router.router)
app.include_router(_fingerspell_router.router)
app.include_router(_anomalies_router.router)
app.include_router(_ab_compare_router.router)
app.include_router(_metrics_router.router)
app.include_router(_viewer_betting_router.router)
app.include_router(_mocap_live_router.router)
app.include_router(_live_share_router.router)
app.include_router(_leaderboard_router.router)
app.include_router(_clips_router.router)
app.include_router(_draft_router.router)

# MCP server is feature-flagged because it changes the security surface
# (different auth header, JSON-RPC envelope). Off by default.
if settings.mcp_server_enabled:
    from autonoma.mcp import server as _mcp_server

    # Streamable HTTP at ``/mcp`` + legacy ``POST /mcp/jsonrpc``. The
    # streamable transport's session manager is started in ``lifespan``.
    _mcp_server.mount(app)
