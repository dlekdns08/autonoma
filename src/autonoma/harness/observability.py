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
