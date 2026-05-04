"""A/B preset comparison — Feature #19.

Operators frequently want to ask "did harness preset X beat preset Y on
the same goal?". The data we need already lives in two tables:

* ``run_summary``      — one row per completed swarm run, contributed by
  feature #12. Carries the headline KPIs: agent_count, task_count,
  tasks_done, tasks_failed, total_rounds, llm_calls, plus the
  ``preset_id`` and ``policy_hash`` so we can label each run with the
  harness configuration it was using.
* ``session_anomalies`` — feature #18, one row per anomaly emitted
  per round. Severity drives the winner heuristic — a run that
  hit ``crit`` anomalies should not beat one that finished cleanly.

This module only *reads* — it never mutates either table. The router
in ``autonoma.routers.ab_compare`` exposes it; tests in
``tests/test_ab_compare.py`` exercise the heuristic directly.

About ``world_event_log``
─────────────────────────
The brief notes "double-check by reading the schema". The current
table has ``round`` but no ``session_id`` column, so we *cannot* key
world events to a specific run. Per the brief we ignore world events
for now. If a future migration adds session keying, extending
:func:`compare_sessions` to count world events per run is a small,
local change.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import desc, func, select

from autonoma.db.engine import get_engine
from autonoma.db.schema import run_summary, session_anomalies

logger = logging.getLogger(__name__)


# ── Public dataclass ───────────────────────────────────────────────────


@dataclass
class ABReport:
    """Side-by-side comparison of two completed swarm runs.

    Attributes
    ----------
    session_a, session_b
        The two ``run_summary.session_id`` values being compared.
    summary_a, summary_b
        The full ``run_summary`` row, JSON-serialisable. Empty dict
        when no row exists for that session.
    deltas
        b-minus-a numeric deltas for derived KPIs:
        ``tasks_done_pct`` (float, [0,1]),
        ``rounds_to_goal``  (int),
        ``llm_calls_per_round`` (float).
        Each metric also contributes a ``*_a`` and ``*_b`` field so the
        UI doesn't have to recompute the per-side values.
    anomaly_counts
        ``{session_id: {kind: count}}`` — counts grouped by the
        ``session_anomalies.kind`` string. Crit anomalies are counted
        separately under the synthetic key ``"_crit_total"`` so the
        winner heuristic can break ties on severity without reaching
        back into the DB.
    winner
        ``"a"``, ``"b"``, ``"tie"``, or ``"unknown"`` (only when at
        least one ``run_summary`` row is missing).
    """

    session_a: int
    session_b: int
    summary_a: dict[str, Any] = field(default_factory=dict)
    summary_b: dict[str, Any] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    anomaly_counts: dict[int, dict[str, int]] = field(default_factory=dict)
    winner: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form. ``anomaly_counts`` keys become strings
        because JSON object keys must be strings."""
        out = asdict(self)
        out["anomaly_counts"] = {
            str(k): v for k, v in self.anomaly_counts.items()
        }
        return out


# ── Helpers ────────────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Turn a ``run_summary`` Row into a JSON-friendly dict.

    DateTime columns get ``isoformat()``-rendered so the dict survives
    a ``json.dumps`` round-trip without a custom encoder.
    """
    if row is None:
        return {}
    d: dict[str, Any] = {}
    for key, value in dict(row._mapping).items():
        if hasattr(value, "isoformat"):
            d[key] = value.isoformat()
        else:
            d[key] = value
    return d


def _safe_div(numerator: float, denominator: float) -> float:
    """Integer/float division that returns 0.0 when ``denominator`` is 0
    instead of raising — relevant for ``tasks_done / task_count`` on
    runs that never enqueued tasks."""
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def _derive_metrics(summary: dict[str, Any]) -> dict[str, float]:
    """Compute the three derived KPIs from a raw run_summary dict.

    ``tasks_done_pct``     — completion ratio, in [0, 1].
    ``rounds_to_goal``     — total_rounds (kept as a metric-ready int
                              so the UI doesn't special-case it).
    ``llm_calls_per_round`` — average LLM calls per round, useful for
                              cost-conscious preset comparisons.
    """
    if not summary:
        return {
            "tasks_done_pct": 0.0,
            "rounds_to_goal": 0.0,
            "llm_calls_per_round": 0.0,
        }
    tasks_done = int(summary.get("tasks_done") or 0)
    task_count = int(summary.get("task_count") or 0)
    total_rounds = int(summary.get("total_rounds") or 0)
    llm_calls = int(summary.get("llm_calls") or 0)
    return {
        "tasks_done_pct": _safe_div(tasks_done, task_count),
        "rounds_to_goal": float(total_rounds),
        "llm_calls_per_round": _safe_div(llm_calls, total_rounds),
    }


def _winner(
    summary_a: dict[str, Any],
    summary_b: dict[str, Any],
    anomalies_a: dict[str, int],
    anomalies_b: dict[str, int],
) -> str:
    """Pick the winning side using the documented heuristic ladder.

    Order of comparison (first non-tie wins):
      1. higher ``tasks_done_pct``
      2. fewer ``total_rounds``
      3. fewer ``crit`` anomalies
      4. fewer ``llm_calls``

    A complete tie across all four returns ``"tie"``.
    """
    if not summary_a or not summary_b:
        return "unknown"

    metrics_a = _derive_metrics(summary_a)
    metrics_b = _derive_metrics(summary_b)

    # 1) tasks_done_pct — higher wins.
    if metrics_a["tasks_done_pct"] != metrics_b["tasks_done_pct"]:
        return "a" if metrics_a["tasks_done_pct"] > metrics_b["tasks_done_pct"] else "b"

    # 2) total_rounds — fewer wins.
    rounds_a = int(summary_a.get("total_rounds") or 0)
    rounds_b = int(summary_b.get("total_rounds") or 0)
    if rounds_a != rounds_b:
        return "a" if rounds_a < rounds_b else "b"

    # 3) crit anomalies — fewer wins.
    crit_a = int(anomalies_a.get("_crit_total", 0))
    crit_b = int(anomalies_b.get("_crit_total", 0))
    if crit_a != crit_b:
        return "a" if crit_a < crit_b else "b"

    # 4) llm_calls — fewer wins.
    llm_a = int(summary_a.get("llm_calls") or 0)
    llm_b = int(summary_b.get("llm_calls") or 0)
    if llm_a != llm_b:
        return "a" if llm_a < llm_b else "b"

    return "tie"


async def _load_summary(conn, session_id: int) -> dict[str, Any]:
    """Most-recent ``run_summary`` for ``session_id`` as a dict.

    Same session can in theory have multiple rows (re-run, recovery);
    we want the latest. Empty dict when nothing matches — the caller
    surfaces that as ``winner="unknown"`` in :func:`compare_sessions`.
    """
    result = await conn.execute(
        select(run_summary)
        .where(run_summary.c.session_id == session_id)
        .order_by(desc(run_summary.c.id))
        .limit(1)
    )
    row = result.first()
    return _row_to_dict(row) if row is not None else {}


async def _load_anomaly_counts(conn, session_id: int) -> dict[str, int]:
    """Group ``session_anomalies`` by ``kind`` for one session.

    Adds a synthetic ``_crit_total`` so the winner ladder can read the
    severity tally without a second query.
    """
    by_kind_result = await conn.execute(
        select(session_anomalies.c.kind, func.count())
        .where(session_anomalies.c.session_id == session_id)
        .group_by(session_anomalies.c.kind)
    )
    counts: dict[str, int] = {kind: int(n) for kind, n in by_kind_result.all()}

    crit_result = await conn.execute(
        select(func.count())
        .select_from(session_anomalies)
        .where(session_anomalies.c.session_id == session_id)
        .where(session_anomalies.c.severity == "crit")
    )
    counts["_crit_total"] = int(crit_result.scalar() or 0)
    return counts


# ── Public API ─────────────────────────────────────────────────────────


async def compare_sessions(session_a: int, session_b: int) -> ABReport:
    """Compare two completed swarm runs by their ``session_id``.

    The function never raises on missing rows — instead it returns
    an :class:`ABReport` with empty summaries and ``winner="unknown"``
    so the UI can render an explanatory empty state.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        summary_a = await _load_summary(conn, session_a)
        summary_b = await _load_summary(conn, session_b)
        anomalies_a = await _load_anomaly_counts(conn, session_a)
        anomalies_b = await _load_anomaly_counts(conn, session_b)

    metrics_a = _derive_metrics(summary_a)
    metrics_b = _derive_metrics(summary_b)
    deltas: dict[str, float] = {}
    for key in ("tasks_done_pct", "rounds_to_goal", "llm_calls_per_round"):
        a_val = metrics_a[key]
        b_val = metrics_b[key]
        deltas[f"{key}_a"] = a_val
        deltas[f"{key}_b"] = b_val
        deltas[key] = b_val - a_val

    return ABReport(
        session_a=session_a,
        session_b=session_b,
        summary_a=summary_a,
        summary_b=summary_b,
        deltas=deltas,
        anomaly_counts={session_a: anomalies_a, session_b: anomalies_b},
        winner=_winner(summary_a, summary_b, anomalies_a, anomalies_b),
    )


async def list_recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return the N most-recent ``run_summary`` rows, newest first.

    Powers the AB-compare picker UI: the operator selects the two runs
    they want to compare from this list. Capped at 200 rows even when
    a caller passes a larger ``limit`` — a comparison picker that
    needs more than that should paginate, not stream.
    """
    if limit <= 0:
        limit = 20
    limit = min(limit, 200)

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            select(run_summary)
            .order_by(desc(run_summary.c.id))
            .limit(limit)
        )
        rows = result.all()
    return [_row_to_dict(r) for r in rows]
