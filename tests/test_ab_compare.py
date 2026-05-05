"""Tests for A/B preset comparison — Feature #19.

We exercise the heuristic directly via :func:`compare_sessions`, seeding
both ``run_summary`` and ``session_anomalies`` rows on a fresh per-test
SQLite database. The ``fresh_db`` fixture (in ``conftest.py``) routes
``settings.data_dir`` at ``tmp_path`` so every test gets a clean slate.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import insert

from autonoma.ab_compare import ABReport, compare_sessions, list_recent_runs
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import run_summary, session_anomalies

# ── Helpers ────────────────────────────────────────────────────────────


async def _seed_run(
    *,
    session_id: int,
    goal: str = "test goal",
    agent_count: int = 4,
    task_count: int = 10,
    tasks_done: int = 10,
    tasks_failed: int = 0,
    total_rounds: int = 5,
    llm_calls: int = 100,
    preset_id: str = "preset-x",
    policy_hash: str = "deadbeef",
) -> int:
    """Insert a ``run_summary`` row and return the row id.

    Defaults are chosen so a test can override only the dimensions
    that matter to the assertion under test.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            insert(run_summary).values(
                session_id=session_id,
                goal=goal,
                completed_at=datetime.utcnow(),
                agent_count=agent_count,
                task_count=task_count,
                tasks_done=tasks_done,
                tasks_failed=tasks_failed,
                total_rounds=total_rounds,
                llm_calls=llm_calls,
                preset_id=preset_id,
                policy_hash=policy_hash,
            )
        )
        return int(result.lastrowid or 0)


async def _seed_anomaly(
    *,
    session_id: int,
    kind: str,
    severity: str = "warn",
    round_number: int = 1,
) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(session_anomalies).values(
                session_id=session_id,
                round_number=round_number,
                kind=kind,
                severity=severity,
                details_json="{}",
            )
        )


# ── Tests ──────────────────────────────────────────────────────────────


async def test_winner_is_a_when_completion_higher(fresh_db) -> None:
    """Run A finishes 100% of tasks, B only 50%. A wins regardless of
    tying on any other axis — completion is the top priority."""
    await init_db()
    await _seed_run(
        session_id=101,
        task_count=10,
        tasks_done=10,
        total_rounds=8,
        llm_calls=200,
    )
    await _seed_run(
        session_id=102,
        task_count=10,
        tasks_done=5,
        total_rounds=8,
        llm_calls=200,
    )
    # Even an extra crit anomaly on A should not flip the result —
    # crit only matters when tasks_done_pct + rounds tie.
    await _seed_anomaly(session_id=101, kind="stall", severity="crit")

    report = await compare_sessions(101, 102)
    assert isinstance(report, ABReport)
    assert report.winner == "a"
    assert report.summary_a["session_id"] == 101
    assert report.summary_b["session_id"] == 102
    # Derived metric sanity.
    assert report.deltas["tasks_done_pct_a"] == 1.0
    assert report.deltas["tasks_done_pct_b"] == 0.5
    assert report.deltas["tasks_done_pct"] == pytest.approx(-0.5)
    assert report.deltas["rounds_to_goal"] == 0.0
    # Anomaly counts include the synthetic _crit_total tally.
    assert report.anomaly_counts[101]["stall"] == 1
    assert report.anomaly_counts[101]["_crit_total"] == 1
    assert report.anomaly_counts[102] == {"_crit_total": 0}


async def test_tie_when_metrics_match(fresh_db) -> None:
    """Identical KPIs and zero anomalies on both sides → tie."""
    await init_db()
    await _seed_run(session_id=201, task_count=8, tasks_done=8, total_rounds=6, llm_calls=120)
    await _seed_run(session_id=202, task_count=8, tasks_done=8, total_rounds=6, llm_calls=120)
    report = await compare_sessions(201, 202)
    assert report.winner == "tie"
    assert report.deltas["tasks_done_pct"] == 0.0
    assert report.deltas["rounds_to_goal"] == 0.0
    assert report.deltas["llm_calls_per_round"] == 0.0


async def test_rounds_break_completion_tie(fresh_db) -> None:
    """Same completion, fewer rounds wins (B in this case)."""
    await init_db()
    await _seed_run(session_id=301, task_count=10, tasks_done=10, total_rounds=12, llm_calls=200)
    await _seed_run(session_id=302, task_count=10, tasks_done=10, total_rounds=7, llm_calls=200)
    report = await compare_sessions(301, 302)
    assert report.winner == "b"


async def test_crit_anomalies_break_round_tie(fresh_db) -> None:
    """Tied on completion AND rounds; A has a crit anomaly, so B wins."""
    await init_db()
    await _seed_run(session_id=401, task_count=10, tasks_done=10, total_rounds=5, llm_calls=150)
    await _seed_run(session_id=402, task_count=10, tasks_done=10, total_rounds=5, llm_calls=150)
    await _seed_anomaly(session_id=401, kind="repetition", severity="crit")
    await _seed_anomaly(session_id=402, kind="repetition", severity="warn")
    report = await compare_sessions(401, 402)
    assert report.winner == "b"
    # warn-severity anomalies do NOT count toward _crit_total.
    assert report.anomaly_counts[402]["_crit_total"] == 0
    assert report.anomaly_counts[402]["repetition"] == 1


async def test_llm_calls_break_crit_tie(fresh_db) -> None:
    """All higher-priority axes tied; lower llm_calls wins (A)."""
    await init_db()
    await _seed_run(session_id=501, task_count=10, tasks_done=10, total_rounds=5, llm_calls=80)
    await _seed_run(session_id=502, task_count=10, tasks_done=10, total_rounds=5, llm_calls=160)
    report = await compare_sessions(501, 502)
    assert report.winner == "a"


async def test_unknown_winner_when_summary_missing(fresh_db) -> None:
    """Compare against a non-existent session → ``winner='unknown'``,
    summaries stay empty, and the call does not raise."""
    await init_db()
    await _seed_run(session_id=601, tasks_done=10, task_count=10)
    report = await compare_sessions(601, 999_999)
    assert report.winner == "unknown"
    assert report.summary_a["session_id"] == 601
    assert report.summary_b == {}


async def test_list_recent_runs_returns_newest_first(fresh_db) -> None:
    """``list_recent_runs`` should hand back the most-recently-inserted
    rows first so the picker UI shows fresh runs at the top."""
    await init_db()
    await _seed_run(session_id=701, preset_id="alpha")
    await _seed_run(session_id=702, preset_id="beta")
    await _seed_run(session_id=703, preset_id="gamma")
    runs = await list_recent_runs(limit=10)
    assert [r["session_id"] for r in runs] == [703, 702, 701]
    assert runs[0]["preset_id"] == "gamma"


async def test_to_dict_round_trip(fresh_db) -> None:
    """``ABReport.to_dict`` must produce a fully JSON-serialisable
    structure — int keys in ``anomaly_counts`` get coerced to strings."""
    import json

    await init_db()
    await _seed_run(session_id=801, task_count=4, tasks_done=4)
    await _seed_run(session_id=802, task_count=4, tasks_done=2)
    await _seed_anomaly(session_id=801, kind="file_churn", severity="warn")
    report = await compare_sessions(801, 802)
    payload = report.to_dict()
    encoded = json.dumps(payload)  # would raise if non-serialisable
    decoded = json.loads(encoded)
    assert decoded["winner"] == "a"
    assert "801" in decoded["anomaly_counts"]
    assert decoded["anomaly_counts"]["801"]["file_churn"] == 1
