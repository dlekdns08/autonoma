"""Auto highlight reel detection — Feature #5.

Server-side detection of *moments worth a highlight*: boss kills, raid
victories, first PR opened, large donations, level-ups, and notable
world events. This module does **not** record any video — the frontend
keeps a rolling MediaRecorder buffer and pulls timestamps from us via
the ``/api/highlights/{session_id}`` endpoint to slice the actual MP4.

Design
──────
- ``HighlightRecorder`` subscribes to a fixed set of bus events and
  buffers ``HighlightCandidate`` rows per ``session_id``. Events whose
  payload omits ``session_id`` are ignored — highlights are
  session-scoped by definition.
- Scoring is a fixed weight per event kind plus a tiny recency boost so
  later candidates outrank earlier ones of the same kind.
- ``snapshot(session_id)`` returns the top-N (``settings.highlights_max_clips``)
  candidates sorted by score desc, then timestamp desc.
- Lifecycle is idempotent: ``start()`` registers handlers once,
  ``stop()`` unregisters them. Re-calling either is a no-op.
- A module-level singleton (``get_recorder()``) keeps the API router
  and any test fixtures pointing at the same buffers.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from autonoma.config import settings
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ── Score weights ─────────────────────────────────────────────────────
# Fixed base score per event kind. Donations get the donation-base only
# when ``amount_usd >= 5`` (or its cents equivalent ≥ 500); smaller tips
# don't make the reel.
_SCORE_BOSS_DEFEATED = 10.0
_SCORE_RAID_VICTORY = 8.0
_SCORE_PR_OPENED = 6.0
_SCORE_DONATION_LARGE = 7.0
_SCORE_LEVEL_UP = 3.0
_SCORE_WORLD_EVENT = 2.0

# Donation threshold for auto-highlight inclusion. Anything below this
# is skipped — small tips are nice but they aren't reel-worthy.
_DONATION_USD_THRESHOLD = 5.0

# Recency boost: a small monotonic bump per candidate so ties break in
# favour of the most recent moment. Capped to stay below the smallest
# weight delta (1.0) so it never inverts the kind ordering.
_RECENCY_BOOST_STEP = 0.001
_RECENCY_BOOST_MAX = 0.5

# Per-session in-memory buffer cap (I11). Distinct from
# ``settings.highlights_max_clips`` — that one bounds the *snapshot*
# return; this one bounds how many candidates we hold internally so a
# long-running session can't grow the buffer without limit. When the
# cap is exceeded we keep the highest-scoring candidates (with the
# usual recency tie-break) and drop the rest.
MAX_BUFFER_PER_SESSION = 500


@dataclass
class HighlightCandidate:
    """One reel-worthy moment captured from the bus."""

    timestamp: datetime
    round_number: int
    kind: str
    title: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "round_number": self.round_number,
            "kind": self.kind,
            "title": self.title,
            "score": self.score,
            "payload": self.payload,
        }


def _coerce_session_id(payload: dict[str, Any]) -> str | None:
    """Pull a usable session_id out of an event payload, or None."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        return sid
    return None


def _coerce_round(payload: dict[str, Any]) -> int:
    raw = payload.get("round_number") or payload.get("round") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _donation_usd(payload: dict[str, Any]) -> float:
    """Best-effort: extract donation amount in USD from a payload.

    Accepts either ``amount_usd`` (float-ish) or ``amount_cents`` (int).
    Returns 0.0 if neither is present / parseable.
    """
    if "amount_usd" in payload:
        try:
            return float(payload["amount_usd"])
        except (TypeError, ValueError):
            pass
    if "amount_cents" in payload:
        try:
            return float(payload["amount_cents"]) / 100.0
        except (TypeError, ValueError):
            pass
    return 0.0


class HighlightRecorder:
    """Subscribes to bus events and buffers highlight candidates per session."""

    # The exact list of bus events we listen on. Held as a class attribute
    # so external code (tests, docs) can introspect it without booting the
    # singleton.
    SUBSCRIBED_EVENTS: tuple[str, ...] = (
        "boss.defeated",
        "raid.victory",
        "pr.opened",
        "live.donation_received",
        "agent.level_up",
        "world.event_recorded",
    )

    def __init__(self) -> None:
        self._buffers: dict[str, list[HighlightCandidate]] = defaultdict(list)
        self._handlers: dict[str, Any] = {}
        self._counter: int = 0
        self._started: bool = False

    # ── lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        """Register bus handlers. Idempotent — second call is a no-op."""
        if self._started:
            return
        # Bind one closure per event so ``bus.off`` can target it later.
        # We can't share a single handler because the bus doesn't pass
        # event-name to ``**data`` handlers.
        self._handlers = {
            "boss.defeated": self._handle_boss_defeated,
            "raid.victory": self._handle_raid_victory,
            "pr.opened": self._handle_pr_opened,
            "live.donation_received": self._handle_donation,
            "agent.level_up": self._handle_level_up,
            "world.event_recorded": self._handle_world_event,
        }
        for event_name, handler in self._handlers.items():
            bus.on(event_name, handler)
        self._started = True
        logger.info(
            "HighlightRecorder started; subscribed to %d events",
            len(self._handlers),
        )

    def stop(self) -> None:
        """Unregister bus handlers. Idempotent."""
        if not self._started:
            return
        for event_name, handler in self._handlers.items():
            bus.off(event_name, handler)
        self._handlers = {}
        self._started = False
        logger.info("HighlightRecorder stopped")

    # ── public API ────────────────────────────────────────────────────
    def snapshot(self, session_id: str) -> list[HighlightCandidate]:
        """Return the top-N candidates for ``session_id``, ranked by score.

        The cap is ``settings.highlights_max_clips``. When there are
        fewer candidates than the cap, returns them all. Tie-break is
        timestamp descending so the most recent moment of an equal-score
        kind wins.
        """
        cands = list(self._buffers.get(session_id, ()))
        cands.sort(key=lambda c: (c.score, c.timestamp), reverse=True)
        cap = max(0, int(settings.highlights_max_clips))
        return cands[:cap]

    def reset(self, session_id: str | None = None) -> None:
        """Drop buffered candidates. Useful for tests + session restart."""
        if session_id is None:
            self._buffers.clear()
            self._counter = 0
            return
        self._buffers.pop(session_id, None)

    # ── internal: scoring + ingestion ─────────────────────────────────
    def _next_recency_boost(self) -> float:
        """Tiny monotonic bump so newer events outrank older ones at the
        same base weight. Capped so it never crosses kind boundaries."""
        self._counter += 1
        return min(self._counter * _RECENCY_BOOST_STEP, _RECENCY_BOOST_MAX)

    def _record(
        self,
        *,
        session_id: str,
        kind: str,
        title: str,
        base_score: float,
        payload: dict[str, Any],
    ) -> None:
        candidate = HighlightCandidate(
            timestamp=datetime.now(UTC),
            round_number=_coerce_round(payload),
            kind=kind,
            title=title,
            score=base_score + self._next_recency_boost(),
            payload=dict(payload),
        )
        buf = self._buffers[session_id]
        buf.append(candidate)
        # Cap the per-session buffer so long-running sessions don't
        # accumulate unboundedly. We keep the top-N by score (with the
        # baked-in recency boost as the tie-breaker), discarding the
        # rest. Cheap: only runs when the cap is actually exceeded.
        if len(buf) > MAX_BUFFER_PER_SESSION:
            buf.sort(key=lambda c: (c.score, c.timestamp), reverse=True)
            del buf[MAX_BUFFER_PER_SESSION:]

    # ── handlers ──────────────────────────────────────────────────────
    # Each handler signature is ``async def(**data)`` to match the bus
    # contract. Events without ``session_id`` are ignored on purpose —
    # we can't attribute them to a reel without one.

    async def _handle_boss_defeated(self, **data: Any) -> None:
        sid = _coerce_session_id(data)
        if not settings.highlights_enabled or not sid:
            return
        title = str(data.get("title") or data.get("name") or "Boss defeated")
        self._record(
            session_id=sid,
            kind="boss.defeated",
            title=title,
            base_score=_SCORE_BOSS_DEFEATED,
            payload=data,
        )

    async def _handle_raid_victory(self, **data: Any) -> None:
        sid = _coerce_session_id(data)
        if not settings.highlights_enabled or not sid:
            return
        title = str(data.get("title") or data.get("name") or "Raid victory")
        self._record(
            session_id=sid,
            kind="raid.victory",
            title=title,
            base_score=_SCORE_RAID_VICTORY,
            payload=data,
        )

    async def _handle_pr_opened(self, **data: Any) -> None:
        sid = _coerce_session_id(data)
        if not settings.highlights_enabled or not sid:
            return
        title = str(data.get("title") or data.get("pr_title") or "Pull request opened")
        self._record(
            session_id=sid,
            kind="pr.opened",
            title=title,
            base_score=_SCORE_PR_OPENED,
            payload=data,
        )

    async def _handle_donation(self, **data: Any) -> None:
        sid = _coerce_session_id(data)
        if not settings.highlights_enabled or not sid:
            return
        usd = _donation_usd(data)
        if usd < _DONATION_USD_THRESHOLD:
            return
        username = str(data.get("username") or data.get("from") or "viewer")
        title = f"{username} donated ${usd:.2f}"
        self._record(
            session_id=sid,
            kind="live.donation_received",
            title=title,
            base_score=_SCORE_DONATION_LARGE,
            payload=data,
        )

    async def _handle_level_up(self, **data: Any) -> None:
        sid = _coerce_session_id(data)
        if not settings.highlights_enabled or not sid:
            return
        agent = str(data.get("agent_name") or data.get("name") or "agent")
        level = data.get("level")
        title = f"{agent} levelled up" if level is None else f"{agent} reached lv{level}"
        self._record(
            session_id=sid,
            kind="agent.level_up",
            title=title,
            base_score=_SCORE_LEVEL_UP,
            payload=data,
        )

    async def _handle_world_event(self, **data: Any) -> None:
        sid = _coerce_session_id(data)
        if not settings.highlights_enabled or not sid:
            return
        title = str(
            data.get("title")
            or data.get("name")
            or data.get("event_type")
            or "World event"
        )
        self._record(
            session_id=sid,
            kind="world.event_recorded",
            title=title,
            base_score=_SCORE_WORLD_EVENT,
            payload=data,
        )


# ── Singleton accessor ────────────────────────────────────────────────

_recorder: HighlightRecorder | None = None


def get_recorder() -> HighlightRecorder:
    """Return the process-wide ``HighlightRecorder`` singleton.

    The first call constructs it; subsequent calls reuse the same
    instance so the API router and the bus handlers share buffers.
    Callers are responsible for invoking ``start()`` (typically once at
    app boot) — keeping that explicit avoids surprising side effects on
    pure imports during tests.
    """
    global _recorder
    if _recorder is None:
        _recorder = HighlightRecorder()
    return _recorder
