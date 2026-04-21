"""Lightweight in-process telemetry for harness policy usage.

Two signals:

- **Per-run metadata** — the preset id, which sections were overridden,
  the effective policy that actually ran, plus start/end timestamps.
  Recorded at ``start``-time and sealed at ``end``-time so a caller can
  fetch it mid-run without racing the swarm loop.
- **Global strategy-pick counters** — across every run on this server
  process, how often has each (section, field, value) been selected.
  Useful for "which policies do users actually reach for" without
  instrumenting every strategy call site. Counted once per run when the
  policy is recorded, not per inner-loop invocation.

Storage is a module-level dict — this is a single-process FastAPI app,
and the data is deliberately ephemeral (wiped on restart). Nothing about
the model prevents swapping in Redis/Postgres later.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from time import time
from typing import Any, Literal, get_args, get_origin

from autonoma.harness.policy import HarnessPolicyContent


# ── Per-run metrics accumulator ──────────────────────────────────────────
# Tracks harness-level events within the *current* run. Reset each time a
# new run starts (``record_run_start`` calls ``_RUN_METRICS.reset()``).
# Populated by ``record_*`` helpers that the swarm/agent call at event
# time — no bus coupling needed, all callers are in-process.

@dataclass
class _RunMetrics:
    """Mutable accumulator for a single run's harness signals.

    All fields are reset by ``reset()``. Thread-safety is not required
    because the swarm loop is single-threaded asyncio.
    """
    preset_id: str | None = None
    parse_attempts: int = 0
    parse_failures: int = 0
    stall_count: int = 0
    # Maps action name → block count
    blocked_actions: Counter = field(default_factory=Counter)
    # Maps llm_error_type → count ("timeout", "rate_limit", "other")
    llm_errors: Counter = field(default_factory=Counter)

    def reset(self) -> None:
        self.preset_id = None
        self.parse_attempts = 0
        self.parse_failures = 0
        self.stall_count = 0
        self.blocked_actions.clear()
        self.llm_errors.clear()


_RUN_METRICS: _RunMetrics = _RunMetrics()


def record_parse_attempt(*, success: bool) -> None:
    """Called by agents each time they try to extract JSON from an LLM response."""
    _RUN_METRICS.parse_attempts += 1
    if not success:
        _RUN_METRICS.parse_failures += 1


def record_stall() -> None:
    """Called by the swarm loop when a stall is detected."""
    _RUN_METRICS.stall_count += 1


def record_blocked_action(action: str) -> None:
    """Called by the harness enforcement layer when an action is blocked."""
    _RUN_METRICS.blocked_actions[action] += 1


def record_llm_error(error_type: str) -> None:
    """Called by agents when an LLM call fails.

    ``error_type`` should be one of ``"timeout"``, ``"rate_limit"``, or
    ``"other"`` to keep the breakdown bucketed without unbounded keys.
    """
    _RUN_METRICS.llm_errors[error_type] += 1


def get_metrics_summary(*, num_runs: int = 1) -> dict[str, Any]:
    """Build the structured summary consumed by ``GET /api/harness/metrics/summary``.

    ``num_runs`` is used to divide raw stall/parse counts into per-run
    averages. The caller (the API endpoint) can pass the number of
    completed sessions it's aware of from ``_SESSIONS`` length.
    """
    parse_rate = (
        round(
            (_RUN_METRICS.parse_attempts - _RUN_METRICS.parse_failures)
            / max(1, _RUN_METRICS.parse_attempts),
            4,
        )
        if _RUN_METRICS.parse_attempts > 0
        else 1.0
    )
    avg_stalls = round(_RUN_METRICS.stall_count / max(1, num_runs), 2)
    top_blocked = [
        {"action": action, "count": count}
        for action, count in _RUN_METRICS.blocked_actions.most_common(10)
    ]
    # Aggregate preset usage from the global session registry
    by_preset: Counter = Counter()
    for meta in _SESSIONS.values():
        key = meta.preset_id or "default"
        by_preset[key] += 1

    return {
        "by_preset": dict(by_preset),
        "parse_success_rate": parse_rate,
        "avg_stalls_per_run": avg_stalls,
        "top_blocked_actions": top_blocked,
        "llm_error_breakdown": dict(_RUN_METRICS.llm_errors),
    }


@dataclass
class SessionMetadata:
    """What we recorded for one ``start`` → ``finished`` span."""

    session_id: int
    preset_id: str | None
    overrides_sections: list[str]
    effective_content: dict[str, Any]
    started_at: float
    ended_at: float | None = None
    # Per-run picks — same shape as the global counter but scoped to
    # just this run. Makes the /api/session/metadata payload useful on
    # its own without cross-referencing global metrics.
    strategy_picks: dict[str, int] = field(default_factory=dict)


# Keyed by session_id. Entries live until ``clear_session`` is called
# (on room teardown) so a caller can still read a run's metadata after
# the swarm has finished.
_SESSIONS: dict[int, SessionMetadata] = {}

# Global rollup across all runs. Keys are ``"section.field=value"``.
_GLOBAL_PICKS: Counter[str] = Counter()


def _iter_enum_picks(content: HarnessPolicyContent) -> list[tuple[str, str]]:
    """Yield (section.field, value) for every Literal field in content.

    This is the canonical "picks" enumeration — drives both the per-run
    counter and the global rollup. Numeric/bool fields aren't counted
    here because counting a continuous number doesn't make sense as a
    histogram axis.
    """
    out: list[tuple[str, str]] = []
    dump = content.model_dump(mode="json")
    for section_name, section_field in HarnessPolicyContent.model_fields.items():
        sub_model = section_field.annotation
        for field_name, field_info in sub_model.model_fields.items():
            annot = field_info.annotation
            if get_origin(annot) is Literal and get_args(annot):
                value = dump.get(section_name, {}).get(field_name)
                out.append((f"{section_name}.{field_name}", str(value)))
    return out


def record_run_start(
    *,
    session_id: int,
    preset_id: str | None,
    overrides: dict[str, Any] | None,
    content: HarnessPolicyContent,
) -> None:
    """Seed per-session metadata and bump global counters.

    Idempotent per session_id — re-calling replaces the entry, which
    matters when the WS client hits ``reset`` then ``start`` again in
    the same connection.
    """
    picks = _iter_enum_picks(content)
    per_run: dict[str, int] = {}
    for slot, value in picks:
        key = f"{slot}={value}"
        _GLOBAL_PICKS[key] += 1
        per_run[key] = per_run.get(key, 0) + 1

    overrides_sections = sorted((overrides or {}).keys())
    _SESSIONS[session_id] = SessionMetadata(
        session_id=session_id,
        preset_id=preset_id,
        overrides_sections=overrides_sections,
        effective_content=content.model_dump(mode="json"),
        started_at=time(),
        strategy_picks=per_run,
    )
    # Reset the per-run metrics accumulator so each new run starts clean.
    _RUN_METRICS.reset()
    _RUN_METRICS.preset_id = preset_id


def record_run_end(session_id: int) -> None:
    """Mark the run as finished. Safe if no metadata was recorded (e.g.
    start failed before observability was wired in)."""
    meta = _SESSIONS.get(session_id)
    if meta is not None and meta.ended_at is None:
        meta.ended_at = time()


def get_session_metadata(session_id: int) -> SessionMetadata | None:
    return _SESSIONS.get(session_id)


def get_global_counters() -> dict[str, int]:
    return dict(_GLOBAL_PICKS)


def clear_session(session_id: int) -> None:
    _SESSIONS.pop(session_id, None)


def reset_global_counters() -> None:
    """Test-only — lets a test file start from a zero baseline without
    bleeding state across modules."""
    _GLOBAL_PICKS.clear()


def metadata_to_dict(meta: SessionMetadata) -> dict[str, Any]:
    """JSON-safe projection for API responses + event payloads."""
    return {
        "session_id": meta.session_id,
        "preset_id": meta.preset_id,
        "overrides_sections": meta.overrides_sections,
        "effective_content": meta.effective_content,
        "started_at": meta.started_at,
        "ended_at": meta.ended_at,
        "strategy_picks": meta.strategy_picks,
    }
