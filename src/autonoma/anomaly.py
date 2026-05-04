"""Session anomaly detection — Feature #18.

Detects when a swarm session is going off the rails *earlier* than the
existing 3-round stall counter so the AB-compare view (#19) and the
operator dashboard can flag drift in real time. Four rules are evaluated
at the end of every round:

  1. ``repetition``   — an agent keeps saying the same thing.
  2. ``mood_drift``   — the room collectively slides toward despair.
  3. ``file_churn``   — the same path is being overwritten over and over.
  4. ``llm_error_burst`` — the LLM layer is throwing errors in bursts.

All bookkeeping is in-process (a small sliding window of the last 10
rounds per session). When a rule fires we INSERT a row into
``session_anomalies`` and emit ``session.anomaly`` on the bus so live
viewers can see it. A 5-round per-(kind, key) cooldown stops the same
finding from spamming the bus every round.

Stdlib only — Jaccard tokenization is lowercase + whitespace split with
ASCII punctuation stripped on the edges of each token. Good enough for
"is this the same paragraph again", which is what we're after.

Bus event shape (``session.anomaly``)::

    {
      "session_id":   int,
      "round_number": int,
      "kind":         "repetition" | "mood_drift"
                    | "file_churn" | "llm_error_burst",
      "severity":     "info" | "warn" | "crit",
      "details":      {...},     # rule-specific
    }

Rule parameters are constants below — exposed so the AB-compare view can
quote them in its tooltip ("4 writes in 5 rounds = churn"). Keep them in
sync with the docstring above when tuning.
"""

from __future__ import annotations

import json
import logging
import string
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert, select

from autonoma.config import settings
from autonoma.db.engine import get_engine
from autonoma.db.schema import session_anomalies
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ── Rule parameters (single source of truth) ──────────────────────────
# Sliding window depth — rules that look back further than this are
# clamped automatically by the deque's maxlen.
WINDOW_ROUNDS = 10

# repetition
REPETITION_LOOKBACK = 5            # last N utterances per agent
REPETITION_MIN_HITS = 3            # ≥ M near-duplicate utterances
REPETITION_JACCARD_THRESHOLD = 0.6

# mood_drift
MOOD_DRIFT_LOOKBACK_ROUNDS = 3
MOOD_DRIFT_RATIO = 0.6             # ≥ 60% of agents
NEGATIVE_MOODS = frozenset({"tired", "frustrated", "worried", "despair"})

# file_churn
FILE_CHURN_LOOKBACK_ROUNDS = 5
FILE_CHURN_MIN_WRITES = 4

# llm_error_burst
LLM_ERROR_LOOKBACK_ROUNDS = 2
LLM_ERROR_MIN_COUNT = 3

# Cooldown — how many rounds the same (kind, key) is suppressed after a
# fire. Prevents the bus / DB from being flooded when the underlying
# condition is sticky.
COOLDOWN_ROUNDS = 5


# ── Public dataclass ──────────────────────────────────────────────────


@dataclass
class Anomaly:
    """One detection event; see module docstring for schema."""

    session_id: int
    round_number: int
    kind: str
    severity: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "round_number": self.round_number,
            "kind": self.kind,
            "severity": self.severity,
            "details": dict(self.details),
        }


# ── Helpers ───────────────────────────────────────────────────────────


_PUNCT = string.punctuation


def _tokenize(text: str) -> set[str]:
    """Lowercase + whitespace split + strip ASCII punctuation off edges.

    Returns a set so Jaccard becomes ``len(a & b) / len(a | b)`` directly.
    Empty strings collapse to the empty set, which yields a Jaccard of
    0 against any non-empty set (we treat 0/0 as 0 to avoid surprises).
    """
    out: set[str] = set()
    for raw in text.lower().split():
        tok = raw.strip(_PUNCT)
        if tok:
            out.add(tok)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


# ── Detector ──────────────────────────────────────────────────────────


class AnomalyDetector:
    """Per-session rolling-window detector.

    Cheap to construct, cheap to feed. Hold one per active session in the
    coordinator. Call ``record_*`` as events arrive and ``tick`` at the
    end of each round; ``tick`` returns any anomalies that fired this
    round (already deduped against the cooldown). Persisting / emitting
    is the caller's responsibility — call ``record_anomaly`` for each.
    """

    def __init__(self, session_id: int, window_rounds: int | None = None) -> None:
        self.session_id = session_id
        # ``window_rounds`` precedence (highest to lowest):
        #   1. explicit constructor argument
        #   2. ``settings.anomaly_window_rounds`` if configured
        #   3. module default ``WINDOW_ROUNDS``
        # The settings field is intentionally *not* declared in
        # ``config.py`` (a shared file) — the ``getattr`` fallback keeps
        # this module forward-compatible: an operator can add the field
        # later for tuning without an edit here.
        if window_rounds is None:
            window_rounds = getattr(settings, "anomaly_window_rounds", WINDOW_ROUNDS)
        self.window_rounds = max(1, int(window_rounds))

        # Per-agent ring of recent utterances. Each entry is
        # ``(round_number, raw_text, tokenized_set)`` so we don't have
        # to re-tokenize on every tick.
        self._utterances: dict[str, deque[tuple[int, str, set[str]]]] = defaultdict(
            lambda: deque(maxlen=REPETITION_LOOKBACK)
        )

        # Per-agent latest mood per round. ``(round, mood)`` — a single
        # agent moodings out twice in the same round overwrites; the
        # latest sample wins.
        self._moods: dict[str, dict[int, str]] = defaultdict(dict)

        # Per-path file write rounds. We keep a deque of round numbers
        # per path; pruning happens lazily at tick time.
        self._file_writes: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=FILE_CHURN_LOOKBACK_ROUNDS * 4)
        )

        # LLM errors keyed by round.
        self._llm_errors: dict[int, int] = defaultdict(int)

        # Cooldown — last round each ``(kind, dedupe_key)`` fired.
        self._cooldowns: dict[tuple[str, str], int] = {}

    # ── Recording ─────────────────────────────────────────────────────

    def record_speech(self, agent: str, text: str, round_number: int) -> None:
        if not agent:
            return
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self._utterances[agent].append(
            (int(round_number), cleaned, _tokenize(cleaned))
        )

    def record_mood(self, agent: str, mood: str, round_number: int) -> None:
        if not agent:
            return
        m = (mood or "").strip().lower()
        if not m:
            return
        self._moods[agent][int(round_number)] = m

    def record_file_write(self, path: str, round_number: int) -> None:
        if not path:
            return
        self._file_writes[path].append(int(round_number))

    def record_llm_error(self, round_number: int) -> None:
        self._llm_errors[int(round_number)] += 1

    # ── Cooldown gate ─────────────────────────────────────────────────

    def _on_cooldown(self, kind: str, key: str, round_number: int) -> bool:
        last = self._cooldowns.get((kind, key))
        if last is None:
            return False
        return (round_number - last) < COOLDOWN_ROUNDS

    def _arm_cooldown(self, kind: str, key: str, round_number: int) -> None:
        self._cooldowns[(kind, key)] = round_number

    # ── Pruning ───────────────────────────────────────────────────────

    def _prune(self, round_number: int) -> None:
        """Drop samples older than the sliding window so the rules look
        only at recent activity. Idempotent — safe to call before every
        tick.
        """
        cutoff = round_number - self.window_rounds + 1

        # Moods: drop old per-round entries.
        for agent, by_round in list(self._moods.items()):
            for r in list(by_round.keys()):
                if r < cutoff:
                    del by_round[r]
            if not by_round:
                del self._moods[agent]

        # File writes: drop rounds outside the file-churn window
        # specifically (its lookback is shorter than the global window).
        churn_cutoff = round_number - FILE_CHURN_LOOKBACK_ROUNDS + 1
        for path, rounds in list(self._file_writes.items()):
            while rounds and rounds[0] < churn_cutoff:
                rounds.popleft()
            if not rounds:
                del self._file_writes[path]

        # LLM errors: only keep the burst window.
        err_cutoff = round_number - LLM_ERROR_LOOKBACK_ROUNDS + 1
        for r in list(self._llm_errors.keys()):
            if r < err_cutoff:
                del self._llm_errors[r]

        # Utterances: deque already capped to REPETITION_LOOKBACK; we
        # additionally drop entries older than the global window so a
        # silent agent doesn't carry stale text forever.
        for agent, utt in list(self._utterances.items()):
            while utt and utt[0][0] < cutoff:
                utt.popleft()
            if not utt:
                del self._utterances[agent]

    # ── Tick (rule evaluation) ────────────────────────────────────────

    def tick(self, round_number: int) -> list[Anomaly]:
        """Evaluate every rule for ``round_number``; return new fires.

        Each finding is stamped with ``round_number`` (not the round the
        offending event happened) so consumers can plot anomalies on a
        per-round timeline. Cooldown keys are rule-specific and chosen
        so two distinct paths in the same session don't suppress each
        other.
        """
        round_number = int(round_number)
        self._prune(round_number)

        fired: list[Anomaly] = []

        fired.extend(self._check_repetition(round_number))
        fired.extend(self._check_mood_drift(round_number))
        fired.extend(self._check_file_churn(round_number))
        fired.extend(self._check_llm_error_burst(round_number))

        return fired

    # ── Individual rules ──────────────────────────────────────────────

    def _check_repetition(self, round_number: int) -> list[Anomaly]:
        out: list[Anomaly] = []
        for agent, utt in self._utterances.items():
            if len(utt) < REPETITION_MIN_HITS:
                continue
            recent = list(utt)[-REPETITION_LOOKBACK:]
            # For each utterance, count how many *other* utterances in the
            # window cross the Jaccard threshold. If any one utterance has
            # >= REPETITION_MIN_HITS-1 near-duplicates, that's M of N.
            best_count = 0
            best_samples: list[str] = []
            for i, (_r_i, text_i, toks_i) in enumerate(recent):
                hits = [text_i]
                for j, (_r_j, text_j, toks_j) in enumerate(recent):
                    if i == j:
                        continue
                    if _jaccard(toks_i, toks_j) >= REPETITION_JACCARD_THRESHOLD:
                        hits.append(text_j)
                if len(hits) > best_count:
                    best_count = len(hits)
                    best_samples = hits
            if best_count >= REPETITION_MIN_HITS:
                if self._on_cooldown("repetition", agent, round_number):
                    continue
                self._arm_cooldown("repetition", agent, round_number)
                out.append(
                    Anomaly(
                        session_id=self.session_id,
                        round_number=round_number,
                        kind="repetition",
                        severity="warn",
                        details={
                            "agent": agent,
                            "samples": best_samples[:REPETITION_MIN_HITS],
                            "count": best_count,
                            "threshold": REPETITION_JACCARD_THRESHOLD,
                        },
                    )
                )
        return out

    def _check_mood_drift(self, round_number: int) -> list[Anomaly]:
        cutoff = round_number - MOOD_DRIFT_LOOKBACK_ROUNDS + 1

        # Each agent contributes its *latest* mood inside the lookback
        # window; an agent with no mood reading in that window is
        # ignored (we can't say they're sad if they didn't speak).
        latest_by_agent: dict[str, str] = {}
        for agent, by_round in self._moods.items():
            in_window = {r: m for r, m in by_round.items() if r >= cutoff}
            if not in_window:
                continue
            latest_round = max(in_window)
            latest_by_agent[agent] = in_window[latest_round]

        if not latest_by_agent:
            return []

        negative = [a for a, m in latest_by_agent.items() if m in NEGATIVE_MOODS]
        ratio = len(negative) / len(latest_by_agent)
        if ratio < MOOD_DRIFT_RATIO:
            return []

        if self._on_cooldown("mood_drift", "room", round_number):
            return []
        self._arm_cooldown("mood_drift", "room", round_number)
        return [
            Anomaly(
                session_id=self.session_id,
                round_number=round_number,
                kind="mood_drift",
                severity="warn",
                details={
                    "ratio": round(ratio, 3),
                    "threshold": MOOD_DRIFT_RATIO,
                    "agents_total": len(latest_by_agent),
                    "agents_negative": len(negative),
                    "negative_agents": sorted(negative),
                    "lookback_rounds": MOOD_DRIFT_LOOKBACK_ROUNDS,
                },
            )
        ]

    def _check_file_churn(self, round_number: int) -> list[Anomaly]:
        cutoff = round_number - FILE_CHURN_LOOKBACK_ROUNDS + 1
        out: list[Anomaly] = []
        for path, rounds in self._file_writes.items():
            count = sum(1 for r in rounds if r >= cutoff)
            if count < FILE_CHURN_MIN_WRITES:
                continue
            if self._on_cooldown("file_churn", path, round_number):
                continue
            self._arm_cooldown("file_churn", path, round_number)
            out.append(
                Anomaly(
                    session_id=self.session_id,
                    round_number=round_number,
                    kind="file_churn",
                    severity="warn",
                    details={
                        "path": path,
                        "count": count,
                        "threshold": FILE_CHURN_MIN_WRITES,
                        "lookback_rounds": FILE_CHURN_LOOKBACK_ROUNDS,
                    },
                )
            )
        return out

    def _check_llm_error_burst(self, round_number: int) -> list[Anomaly]:
        cutoff = round_number - LLM_ERROR_LOOKBACK_ROUNDS + 1
        total = sum(c for r, c in self._llm_errors.items() if r >= cutoff)
        if total < LLM_ERROR_MIN_COUNT:
            return []
        if self._on_cooldown("llm_error_burst", "global", round_number):
            return []
        self._arm_cooldown("llm_error_burst", "global", round_number)
        return [
            Anomaly(
                session_id=self.session_id,
                round_number=round_number,
                kind="llm_error_burst",
                severity="crit",
                details={
                    "count": total,
                    "threshold": LLM_ERROR_MIN_COUNT,
                    "lookback_rounds": LLM_ERROR_LOOKBACK_ROUNDS,
                },
            )
        ]


# ── DB / bus side-effects ─────────────────────────────────────────────


async def record_anomaly(anomaly: Anomaly) -> None:
    """Persist ``anomaly`` and broadcast ``session.anomaly`` on the bus.

    Failure to persist does NOT swallow the bus emission — observers
    care about the event in real time even if the writer is slow.
    Bus errors propagate (the bus already isolates handler failures).
    """
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                insert(session_anomalies).values(
                    session_id=int(anomaly.session_id),
                    round_number=int(anomaly.round_number),
                    kind=str(anomaly.kind),
                    severity=str(anomaly.severity or "warn"),
                    details_json=json.dumps(anomaly.details or {}, sort_keys=True),
                )
            )
    except Exception:  # noqa: BLE001 - want to keep emitting on DB failure
        logger.exception(
            "anomaly persist failed: session=%s kind=%s",
            anomaly.session_id, anomaly.kind,
        )

    await bus.emit(
        "session.anomaly",
        session_id=int(anomaly.session_id),
        round_number=int(anomaly.round_number),
        kind=str(anomaly.kind),
        severity=str(anomaly.severity or "warn"),
        details=dict(anomaly.details or {}),
    )


async def list_anomalies(session_id: int) -> list[Anomaly]:
    """Return every anomaly for ``session_id``, oldest-first by round.

    Used by the AB-compare endpoint and the per-session anomalies view.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    session_anomalies.c.session_id,
                    session_anomalies.c.round_number,
                    session_anomalies.c.kind,
                    session_anomalies.c.severity,
                    session_anomalies.c.details_json,
                )
                .where(session_anomalies.c.session_id == int(session_id))
                .order_by(
                    session_anomalies.c.round_number.asc(),
                    session_anomalies.c.id.asc(),
                )
            )
        ).all()

    out: list[Anomaly] = []
    for row in rows:
        m = row._mapping
        try:
            details = json.loads(m["details_json"] or "{}")
        except (TypeError, ValueError):
            details = {}
        out.append(
            Anomaly(
                session_id=int(m["session_id"]),
                round_number=int(m["round_number"]),
                kind=str(m["kind"]),
                severity=str(m["severity"]),
                details=details if isinstance(details, dict) else {},
            )
        )
    return out


