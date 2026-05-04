"""Unit tests for the session anomaly detector (Feature #18).

Each test exercises one of the four detection rules and one final test
verifies that the cooldown machinery prevents the same finding from
firing every round while the underlying condition is still true.

The detector is in-memory only; the DB-backed ``record_anomaly`` /
``list_anomalies`` are exercised separately so the rule logic stays
isolated from the storage layer (no need for ``fresh_db``).
"""

from __future__ import annotations

from autonoma.anomaly import (
    COOLDOWN_ROUNDS,
    FILE_CHURN_LOOKBACK_ROUNDS,
    FILE_CHURN_MIN_WRITES,
    LLM_ERROR_LOOKBACK_ROUNDS,
    LLM_ERROR_MIN_COUNT,
    MOOD_DRIFT_LOOKBACK_ROUNDS,
    REPETITION_LOOKBACK,
    REPETITION_MIN_HITS,
    Anomaly,
    AnomalyDetector,
    list_anomalies,
    record_anomaly,
)
from autonoma.db.engine import init_db
from autonoma.event_bus import bus


# ── repetition ────────────────────────────────────────────────────────


def test_repetition_fires_on_near_duplicates() -> None:
    det = AnomalyDetector(session_id=1)
    repeated = "we should refactor the parser before adding new rules"
    for r in range(1, REPETITION_MIN_HITS + 1):
        det.record_speech("alice", repeated, round_number=r)

    fires = det.tick(round_number=REPETITION_MIN_HITS)
    kinds = [f.kind for f in fires]
    assert "repetition" in kinds, f"expected repetition fire, got {kinds}"

    rep = next(f for f in fires if f.kind == "repetition")
    assert rep.severity == "warn"
    assert rep.details["agent"] == "alice"
    assert rep.details["count"] >= REPETITION_MIN_HITS
    assert len(rep.details["samples"]) == REPETITION_MIN_HITS


def test_repetition_silent_on_distinct_utterances() -> None:
    det = AnomalyDetector(session_id=1)
    det.record_speech("alice", "shipping the schema migration", 1)
    det.record_speech("alice", "wiring up the websocket route", 2)
    det.record_speech("alice", "writing unit tests for parser", 3)
    fires = det.tick(round_number=3)
    assert all(f.kind != "repetition" for f in fires)


def test_repetition_window_caps_at_lookback() -> None:
    """Old utterances roll out of the per-agent window."""
    det = AnomalyDetector(session_id=1)
    # First two rounds are unique — would otherwise dilute the count.
    det.record_speech("alice", "alpha bravo charlie", 1)
    det.record_speech("alice", "delta echo foxtrot", 2)
    # Then REPETITION_LOOKBACK identical lines.
    for r in range(3, 3 + REPETITION_LOOKBACK):
        det.record_speech("alice", "we should refactor parser before rules", r)
    fires = det.tick(round_number=3 + REPETITION_LOOKBACK - 1)
    assert any(f.kind == "repetition" for f in fires)


# ── mood_drift ────────────────────────────────────────────────────────


def test_mood_drift_fires_on_room_wide_negativity() -> None:
    det = AnomalyDetector(session_id=2)
    # 3 of 3 agents in the most recent rounds are despair-adjacent.
    det.record_mood("alice", "frustrated", 1)
    det.record_mood("bear", "tired", 1)
    det.record_mood("cat", "worried", 1)
    fires = det.tick(round_number=1)
    drift = [f for f in fires if f.kind == "mood_drift"]
    assert len(drift) == 1
    f = drift[0]
    assert f.severity == "warn"
    assert f.details["agents_total"] == 3
    assert f.details["agents_negative"] == 3
    assert f.details["ratio"] >= 0.6
    assert f.details["lookback_rounds"] == MOOD_DRIFT_LOOKBACK_ROUNDS


def test_mood_drift_silent_when_room_is_mostly_calm() -> None:
    det = AnomalyDetector(session_id=2)
    det.record_mood("alice", "happy", 1)
    det.record_mood("bear", "focused", 1)
    det.record_mood("cat", "frustrated", 1)
    fires = det.tick(round_number=1)
    assert all(f.kind != "mood_drift" for f in fires)


# ── file_churn ────────────────────────────────────────────────────────


def test_file_churn_fires_on_repeated_writes() -> None:
    det = AnomalyDetector(session_id=3)
    path = "src/autonoma/main.py"
    # Spread the writes across the lookback window.
    rounds = list(range(1, FILE_CHURN_MIN_WRITES + 1))
    for r in rounds:
        det.record_file_write(path, r)
    fires = det.tick(round_number=rounds[-1])
    churn = [f for f in fires if f.kind == "file_churn"]
    assert len(churn) == 1
    assert churn[0].details["path"] == path
    assert churn[0].details["count"] >= FILE_CHURN_MIN_WRITES
    assert churn[0].details["lookback_rounds"] == FILE_CHURN_LOOKBACK_ROUNDS


def test_file_churn_isolates_paths() -> None:
    """Writes to *different* paths don't aggregate into churn."""
    det = AnomalyDetector(session_id=3)
    for r in range(1, FILE_CHURN_MIN_WRITES + 1):
        det.record_file_write(f"src/file_{r}.py", r)
    fires = det.tick(round_number=FILE_CHURN_MIN_WRITES)
    assert all(f.kind != "file_churn" for f in fires)


# ── llm_error_burst ───────────────────────────────────────────────────


def test_llm_error_burst_fires_with_critical_severity() -> None:
    det = AnomalyDetector(session_id=4)
    # Spread errors across the burst window, hitting the threshold.
    det.record_llm_error(round_number=4)
    det.record_llm_error(round_number=4)
    det.record_llm_error(round_number=5)
    fires = det.tick(round_number=5)
    burst = [f for f in fires if f.kind == "llm_error_burst"]
    assert len(burst) == 1
    assert burst[0].severity == "crit"
    assert burst[0].details["count"] >= LLM_ERROR_MIN_COUNT
    assert burst[0].details["lookback_rounds"] == LLM_ERROR_LOOKBACK_ROUNDS


def test_llm_error_burst_ignores_old_errors() -> None:
    det = AnomalyDetector(session_id=4)
    det.record_llm_error(round_number=1)
    det.record_llm_error(round_number=1)
    det.record_llm_error(round_number=1)
    # tick at a much later round — the errors fall outside the lookback.
    fires = det.tick(round_number=20)
    assert all(f.kind != "llm_error_burst" for f in fires)


# ── cooldown ──────────────────────────────────────────────────────────


def test_cooldown_suppresses_back_to_back_emissions() -> None:
    det = AnomalyDetector(session_id=5)
    text = "we keep going in circles around the same idea"

    # Round 1: fill the window with near-duplicates so repetition fires.
    for r in range(1, REPETITION_MIN_HITS + 1):
        det.record_speech("alice", text, r)
    first = det.tick(round_number=REPETITION_MIN_HITS)
    assert any(f.kind == "repetition" for f in first)

    # Round 2: the agent says the same thing AGAIN — but the cooldown
    # is still active, so no new fire should be emitted.
    next_round = REPETITION_MIN_HITS + 1
    det.record_speech("alice", text, next_round)
    second = det.tick(round_number=next_round)
    assert all(f.kind != "repetition" for f in second), (
        "cooldown should suppress same-agent repeat fires for "
        f"{COOLDOWN_ROUNDS} rounds"
    )

    # After the cooldown expires the rule fires again.
    later = next_round + COOLDOWN_ROUNDS
    for r in range(later - REPETITION_LOOKBACK + 1, later + 1):
        det.record_speech("alice", text, r)
    third = det.tick(round_number=later)
    assert any(f.kind == "repetition" for f in third)


# ── DB persistence + bus emission ─────────────────────────────────────


async def test_record_and_list_round_trip(fresh_db) -> None:
    """``record_anomaly`` writes a row and ``list_anomalies`` reads it."""
    await init_db()

    received: list[dict] = []

    async def handler(**data):
        received.append(data)

    bus.on("session.anomaly", handler)

    a = Anomaly(
        session_id=42,
        round_number=7,
        kind="repetition",
        severity="warn",
        details={"agent": "alice", "count": 3},
    )
    await record_anomaly(a)

    rows = await list_anomalies(42)
    assert len(rows) == 1
    got = rows[0]
    assert got.kind == "repetition"
    assert got.severity == "warn"
    assert got.round_number == 7
    assert got.details["agent"] == "alice"
    assert got.details["count"] == 3

    # Bus event also fired with the documented payload schema.
    assert len(received) == 1
    payload = received[0]
    assert payload["session_id"] == 42
    assert payload["round_number"] == 7
    assert payload["kind"] == "repetition"
    assert payload["severity"] == "warn"
    assert payload["details"]["agent"] == "alice"


async def test_two_sessions_isolated_under_concurrent_ticks(fresh_db) -> None:
    """Two ``AnomalyDetector`` instances must not share state.

    Feeding the same agent name into both detectors should produce two
    independent anomalies, each stamped with its own ``session_id``.
    Persisting both rows must keep ``list_anomalies(s)`` strictly
    scoped to ``s``.
    """
    await init_db()

    det1 = AnomalyDetector(session_id=1)
    det2 = AnomalyDetector(session_id=2)

    repeated = "we should refactor the parser before adding new rules"

    # Interleave the speech records — same agent name in each detector.
    for r in range(1, REPETITION_MIN_HITS + 1):
        det1.record_speech("alice", repeated, round_number=r)
        det2.record_speech("alice", repeated, round_number=r)

    # Tick both — each should produce ITS OWN repetition fire.
    fires1 = det1.tick(round_number=REPETITION_MIN_HITS)
    fires2 = det2.tick(round_number=REPETITION_MIN_HITS)

    rep1 = [f for f in fires1 if f.kind == "repetition"]
    rep2 = [f for f in fires2 if f.kind == "repetition"]
    assert len(rep1) == 1, f"detector 1 missed its repetition: {fires1}"
    assert len(rep2) == 1, f"detector 2 missed its repetition: {fires2}"
    # Cross-contamination check: each fire is tagged with its own session.
    assert rep1[0].session_id == 1
    assert rep2[0].session_id == 2

    # Persist each one.
    await record_anomaly(rep1[0])
    await record_anomaly(rep2[0])

    rows1 = await list_anomalies(1)
    rows2 = await list_anomalies(2)
    assert len(rows1) == 1
    assert len(rows2) == 1
    assert rows1[0].session_id == 1
    assert rows1[0].kind == "repetition"
    assert rows2[0].session_id == 2
    assert rows2[0].kind == "repetition"
    # And no cross-leak via ``list_anomalies`` for an unused session.
    assert await list_anomalies(999) == []


async def test_list_anomalies_other_session_isolated(fresh_db) -> None:
    await init_db()
    await record_anomaly(
        Anomaly(session_id=1, round_number=1, kind="mood_drift", severity="warn")
    )
    await record_anomaly(
        Anomaly(session_id=2, round_number=1, kind="file_churn", severity="warn",
                details={"path": "x.py"})
    )
    rows = await list_anomalies(2)
    assert len(rows) == 1
    assert rows[0].kind == "file_churn"
    assert rows[0].details["path"] == "x.py"


