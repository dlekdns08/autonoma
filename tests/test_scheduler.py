"""Tests for the schedule store + due-detection logic (Phase 4-A)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autonoma.scheduler import Schedule, ScheduleNotFound, ScheduleStore


@pytest.fixture
def store(tmp_path):
    return ScheduleStore(root=tmp_path / "schedules")


def _make(**overrides) -> Schedule:
    base = {
        "owner_user_id": "u1",
        "name": "Nightly",
        "goal": "rebuild docs",
        "preset_id": "default",
        "daily_at_utc": "02:00",
    }
    base.update(overrides)
    return Schedule.model_validate(base)


def test_invalid_hhmm_rejected():
    with pytest.raises(ValueError):
        _make(daily_at_utc="25:99")
    with pytest.raises(ValueError):
        _make(daily_at_utc="2:00")


def test_round_trip(store):
    sched = store.save(_make())
    again = store.get("u1", sched.id)
    assert again.goal == "rebuild docs"
    assert again.daily_at_utc == "02:00"


def test_unsafe_id_rejected(store):
    s = _make()
    s.id = "../etc/passwd"
    with pytest.raises(ValueError):
        store.save(s)


def test_due_inside_window():
    s = _make(daily_at_utc="14:00")
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    assert s.is_due(now)


def test_not_due_outside_window():
    s = _make(daily_at_utc="14:00")
    now = datetime(2026, 4, 25, 14, 5, tzinfo=timezone.utc)
    assert not s.is_due(now)


def test_disabled_never_due():
    s = _make(daily_at_utc="14:00", enabled=False)
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    assert not s.is_due(now)


def test_recently_fired_suppresses_redundant_fires():
    s = _make(daily_at_utc="14:00")
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    s.mark_fired(now)
    # Same minute → suppressed.
    assert not s.is_due(now)
    # 23h later → still suppressed.
    later = datetime(2026, 4, 26, 13, 0, tzinfo=timezone.utc)
    assert not s.is_due(later)
    # 24h later in the firing window → due again.
    next_day = datetime(2026, 4, 26, 14, 0, tzinfo=timezone.utc)
    assert s.is_due(next_day)


def test_cron_expr_blocks_default_path():
    # Cron isn't implemented yet — anything with cron_expr stays
    # silent rather than firing through the daily path.
    s = _make(cron_expr="0 14 * * *")
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    assert not s.is_due(now)


def test_list_for_owner_orders_by_time(store):
    store.save(_make(daily_at_utc="03:30"))
    store.save(_make(daily_at_utc="01:15"))
    items = store.list_for_owner("u1")
    assert [s.daily_at_utc for s in items] == ["01:15", "03:30"]


def test_get_missing_raises(store):
    with pytest.raises(ScheduleNotFound):
        store.get("u1", "missing")
