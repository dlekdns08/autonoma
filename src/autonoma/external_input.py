"""External input router — single ingress point for non-typed sources.

Phase 2 brings live chat (Twitch/YouTube) and voice commands to the swarm.
Both share the same plumbing: rate-limit by source, sanitize, optionally
gate on a permission tier, and finally either:

    1. inject_human_message(text, target) — routes a chat-style message
       into a specific agent's inbox (or the Director if no target).
    2. cast a vote in the active poll (one ballot per user).
    3. emit a ``viewer.event`` so the room sees the source on the stage.

The router lives for as long as the swarm. It is intentionally light:
no DB, no persistence — votes are kept in memory and reset when a poll
closes. This file is designed so Phase 2 only has to wire a transport
(WS/HTTP/STT) to ``submit()``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from autonoma.event_bus import bus

if TYPE_CHECKING:
    from autonoma.agents.swarm import AgentSwarm

logger = logging.getLogger(__name__)


SourceKind = Literal[
    "twitch",
    "youtube",
    "voice",
    "slack",
    "discord",
    "webhook",
    "test",
]


class ExternalMessage(BaseModel):
    """A single inbound message from any external source.

    ``source`` identifies the transport, ``user`` the human (so the rate
    limiter can isolate spammers), and ``text`` is the raw payload. The
    optional ``target`` mirrors :py:meth:`AgentSwarm.inject_human_message`
    semantics — when None the Director gets the message.
    """

    source: SourceKind
    user: str
    text: str = Field(..., min_length=1, max_length=2000)
    target: str | None = None
    metadata: dict = Field(default_factory=dict)


class RouteAction(str, Enum):
    INJECTED = "injected"  # delivered as human feedback to an agent
    VOTED = "voted"        # counted toward an active poll
    DROPPED_RATE_LIMIT = "dropped_rate_limit"
    DROPPED_NO_SWARM = "dropped_no_swarm"
    DROPPED_BLOCKED_SOURCE = "dropped_blocked_source"
    DROPPED_INVALID = "dropped_invalid"


@dataclass
class RouteResult:
    action: RouteAction
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.action in (RouteAction.INJECTED, RouteAction.VOTED)


# ── Rate limiter ──────────────────────────────────────────────────────


@dataclass
class _SlidingWindow:
    """Fixed-window sliding rate limit. Keep tiny; per (source, user)."""

    capacity: int
    window_sec: float
    timestamps: deque[float] = field(default_factory=deque)

    def allow(self, now: float) -> bool:
        cutoff = now - self.window_sec
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()
        if len(self.timestamps) >= self.capacity:
            return False
        self.timestamps.append(now)
        return True


# ── Vote bucket ───────────────────────────────────────────────────────


@dataclass
class Poll:
    """A simple keyword-based poll. Each user gets one ballot."""

    poll_id: str
    question: str
    options: list[str]
    closes_at: float
    tallies: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    voters: set[str] = field(default_factory=set)

    def cast(self, user_key: str, text: str) -> str | None:
        """Return the matched option name or None if no option fits."""
        if user_key in self.voters or time.monotonic() >= self.closes_at:
            return None
        # Match the first option whose keyword appears in the text.
        # Options may be expressed with a leading "1) " etc — strip prefix
        # before comparison so "1" / "blue" / "blue!" all count.
        normalized = text.strip().lower()
        for idx, opt in enumerate(self.options, start=1):
            opt_lower = opt.lower()
            if normalized == str(idx) or opt_lower in normalized:
                self.tallies[opt] += 1
                self.voters.add(user_key)
                return opt
        return None


# ── Router ────────────────────────────────────────────────────────────


class ExternalInputRouter:
    """Single ingress for live chat / voice / webhook input.

    The router holds a weak-ish reference to the swarm — call sites must
    re-attach via :py:meth:`bind_swarm` whenever a new run starts. This
    sidesteps the lifecycle mismatch between long-lived transports
    (a Twitch socket) and short-lived swarm runs.
    """

    DEFAULT_RATE_PER_MIN: dict[SourceKind, int] = {
        "twitch": 6,
        "youtube": 6,
        "voice": 12,        # voice command bursts come from the room owner
        "slack": 30,
        "discord": 30,
        "webhook": 60,
        "test": 1000,
    }

    DEFAULT_BLOCKED: frozenset[SourceKind] = frozenset()

    def __init__(
        self,
        rate_per_min: dict[SourceKind, int] | None = None,
        blocked_sources: set[SourceKind] | None = None,
    ) -> None:
        self._rate_caps: dict[SourceKind, int] = {
            **self.DEFAULT_RATE_PER_MIN,
            **(rate_per_min or {}),
        }
        self._blocked: set[SourceKind] = set(blocked_sources or self.DEFAULT_BLOCKED)
        self._windows: dict[tuple[str, str], _SlidingWindow] = {}
        self._swarm: "AgentSwarm | None" = None
        self._poll: Poll | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def bind_swarm(self, swarm: "AgentSwarm | None") -> None:
        self._swarm = swarm

    def block(self, source: SourceKind) -> None:
        self._blocked.add(source)

    def unblock(self, source: SourceKind) -> None:
        self._blocked.discard(source)

    def set_rate_cap(self, source: SourceKind, per_min: int) -> None:
        self._rate_caps[source] = max(0, per_min)

    # ── Polls ─────────────────────────────────────────────────────────

    def open_poll(
        self,
        poll_id: str,
        question: str,
        options: list[str],
        duration_sec: float = 30.0,
    ) -> Poll:
        if duration_sec <= 0 or not options:
            raise ValueError("Poll needs positive duration and at least one option")
        self._poll = Poll(
            poll_id=poll_id,
            question=question,
            options=list(options),
            closes_at=time.monotonic() + duration_sec,
        )
        return self._poll

    def close_poll(self) -> Poll | None:
        poll, self._poll = self._poll, None
        return poll

    @property
    def active_poll(self) -> Poll | None:
        if self._poll is None:
            return None
        if time.monotonic() >= self._poll.closes_at:
            return None
        return self._poll

    # ── Submission ────────────────────────────────────────────────────

    async def submit(self, message: ExternalMessage) -> RouteResult:
        """Route a single inbound message. Never raises."""
        if message.source in self._blocked:
            return RouteResult(RouteAction.DROPPED_BLOCKED_SOURCE, message.source)

        text = message.text.strip()
        if not text:
            return RouteResult(RouteAction.DROPPED_INVALID, "empty text")

        async with self._lock:
            if not self._allow(message.source, message.user):
                await bus.emit(
                    "external.dropped",
                    source=message.source,
                    user=message.user,
                    reason="rate_limit",
                )
                return RouteResult(RouteAction.DROPPED_RATE_LIMIT, "per-source cap")

            poll = self.active_poll
            if poll is not None:
                user_key = f"{message.source}:{message.user}"
                option = poll.cast(user_key, text)
                if option is not None:
                    await bus.emit(
                        "external.vote",
                        poll_id=poll.poll_id,
                        option=option,
                        source=message.source,
                        user=message.user,
                        tallies=dict(poll.tallies),
                    )
                    return RouteResult(RouteAction.VOTED, option)

            swarm = self._swarm
            if swarm is None:
                return RouteResult(RouteAction.DROPPED_NO_SWARM, "no swarm bound")

            # Format the display string so the LLM can naturally
            # address the sender by name in its reply ("greet by name"
            # is a recurring streamer-VTuber pattern). The previous
            # ``[twitch:alice]`` shape was technically correct but
            # read like a log line; agents would echo it back literally
            # ("twitch:alice asked..."). The shapes below put the name
            # in a more conversational frame so the agent's response
            # tends toward "Alice asked ...".
            user_label = message.user.strip() or "anonymous"
            if message.source == "live_chat":
                # Treat external chat (Twitch/YouTube/Discord) as a
                # named viewer. The metadata may carry the original
                # platform; keep it short so the LLM can still
                # mention the channel if it wants ("Alice from
                # Twitch asked...").
                chat_origin = str(message.metadata.get("chat_source") or "")
                origin_tag = f" ({chat_origin})" if chat_origin else ""
                display = f"[viewer {user_label}{origin_tag}] {text[:200]}"
            elif message.source == "voice":
                # Voice utterances come from the same logged-in user
                # the agent has been talking to — the in-room human.
                # Mark them so a future ``viewer_name`` gating can
                # distinguish "the streamer themselves" from a chat
                # spectator.
                display = f"[voice {user_label}] {text[:200]}"
            else:
                display = f"[{message.source}:{user_label}] {text[:200]}"
            ok = await swarm.inject_human_message(display, target=message.target)
            if not ok:
                return RouteResult(RouteAction.DROPPED_NO_SWARM, "swarm not running")

            await bus.emit(
                "external.injected",
                source=message.source,
                user=message.user,
                target=message.target or "Director",
                preview=text[:80],
            )
            return RouteResult(RouteAction.INJECTED, message.target or "Director")

    # ── Internals ─────────────────────────────────────────────────────

    def _allow(self, source: SourceKind, user: str) -> bool:
        cap = self._rate_caps.get(source, 0)
        if cap <= 0:
            return False
        key = (source, user)
        win = self._windows.get(key)
        if win is None or win.capacity != cap:
            win = _SlidingWindow(capacity=cap, window_sec=60.0)
            self._windows[key] = win
        return win.allow(time.monotonic())


# ── Module-level singleton ────────────────────────────────────────────
#
# Most consumers (FastAPI routers, voice STT bridge) only need one
# router. We expose a default singleton so they don't all have to thread
# the instance through DI just to call submit(). Tests can construct
# their own ExternalInputRouter() directly.

router = ExternalInputRouter()
