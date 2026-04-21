"""Tests for ``harness.observability`` — per-run metadata + global counters.

Storage is process-global so each test resets the module before
recording. The counter assertions are deliberately precise about the
exact pick keys; a refactor that renames ``section.field`` would fail
here, which is the point — the key shape is part of the API contract.
"""

from __future__ import annotations

import pytest

from autonoma.harness.observability import (
    clear_session,
    get_global_counters,
    get_session_metadata,
    metadata_to_dict,
    record_run_end,
    record_run_start,
    reset_global_counters,
)
from autonoma.harness.policy import default_policy_content


@pytest.fixture(autouse=True)
def _clear_state():
    reset_global_counters()
    yield
    reset_global_counters()


def test_record_run_start_captures_effective_content() -> None:
    content = default_policy_content()
    record_run_start(
        session_id=1,
        preset_id="p-abc",
        overrides={"routing": {"strategy": "broadcast"}},
        content=content,
    )
    meta = get_session_metadata(1)
    assert meta is not None
    assert meta.session_id == 1
    assert meta.preset_id == "p-abc"
    assert meta.overrides_sections == ["routing"]
    # effective_content mirrors the resolved policy, not the raw
    # override — so callers know exactly what ran.
    assert meta.effective_content["routing"]["strategy"] == content.routing.strategy
    assert meta.started_at > 0
    assert meta.ended_at is None


def test_record_run_end_sets_ended_at() -> None:
    record_run_start(
        session_id=2,
        preset_id=None,
        overrides=None,
        content=default_policy_content(),
    )
    record_run_end(2)
    meta = get_session_metadata(2)
    assert meta is not None
    assert meta.ended_at is not None
    assert meta.ended_at >= meta.started_at


def test_record_run_end_is_idempotent() -> None:
    record_run_start(
        session_id=3,
        preset_id=None,
        overrides=None,
        content=default_policy_content(),
    )
    record_run_end(3)
    first = get_session_metadata(3).ended_at  # type: ignore[union-attr]
    record_run_end(3)
    second = get_session_metadata(3).ended_at  # type: ignore[union-attr]
    assert first == second


def test_record_run_end_noop_for_unknown_session() -> None:
    # No exception, no entry created.
    record_run_end(999)
    assert get_session_metadata(999) is None


def test_global_counters_accumulate_picks_across_runs() -> None:
    content = default_policy_content()
    record_run_start(session_id=10, preset_id=None, overrides=None, content=content)
    record_run_start(session_id=11, preset_id=None, overrides=None, content=content)
    counters = get_global_counters()
    # Default routing.strategy is "priority" — each run contributes 1
    # pick per enum slot, so two runs = 2.
    assert counters["routing.strategy=priority"] == 2


def test_per_run_picks_match_effective_content() -> None:
    content = default_policy_content().model_copy(deep=True)
    content.routing.strategy = "broadcast"
    record_run_start(
        session_id=20,
        preset_id=None,
        overrides={"routing": {"strategy": "broadcast"}},
        content=content,
    )
    meta = get_session_metadata(20)
    assert meta is not None
    assert meta.strategy_picks["routing.strategy=broadcast"] == 1
    # The global counter bumps too.
    assert get_global_counters()["routing.strategy=broadcast"] == 1


def test_clear_session_drops_metadata_but_keeps_global_counter() -> None:
    record_run_start(
        session_id=30,
        preset_id=None,
        overrides=None,
        content=default_policy_content(),
    )
    clear_session(30)
    assert get_session_metadata(30) is None
    # Global counters still reflect the run — clear_session is scoped
    # to per-session metadata, not to the global rollup.
    assert get_global_counters().get("routing.strategy=priority", 0) >= 1


def test_metadata_to_dict_is_json_safe() -> None:
    """The API returns this shape directly — verify it's all primitives."""
    import json

    record_run_start(
        session_id=40,
        preset_id="p-x",
        overrides=None,
        content=default_policy_content(),
    )
    record_run_end(40)
    meta = get_session_metadata(40)
    assert meta is not None
    payload = metadata_to_dict(meta)
    # Must serialize without TypeError.
    json.dumps(payload)
    assert payload["session_id"] == 40
    assert payload["preset_id"] == "p-x"
    assert payload["ended_at"] is not None
    assert isinstance(payload["strategy_picks"], dict)


def test_re_recording_same_session_replaces_metadata() -> None:
    """Supports the WS ``reset`` → ``start`` loop — a second run on the
    same connection should overwrite the first, not append."""
    content = default_policy_content()
    record_run_start(
        session_id=50, preset_id="first", overrides=None, content=content
    )
    record_run_start(
        session_id=50, preset_id="second", overrides=None, content=content
    )
    meta = get_session_metadata(50)
    assert meta is not None
    assert meta.preset_id == "second"
    # Global counters bump twice (one per run).
    assert get_global_counters()["routing.strategy=priority"] == 2
