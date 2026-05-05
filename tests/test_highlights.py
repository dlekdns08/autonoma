"""Tests for auto highlight reel detection (Feature #5).

Covers:
- the recorder subscribes correctly and ranks events by score weight
- ``settings.highlights_max_clips`` caps the snapshot output
- events without a ``session_id`` are ignored
- ``start()`` / ``stop()`` are idempotent
- small donations don't qualify; large ones do
- recency boost breaks ties between same-kind events
"""

from __future__ import annotations

import pytest

from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.highlights import HighlightRecorder, get_recorder


@pytest.fixture
def recorder() -> HighlightRecorder:
    """A fresh ``HighlightRecorder`` wired into the (autouse-cleared) bus.

    The conftest ``_reset`` autouse fixture already clears handlers
    before each test, so we just instantiate, ``start()`` and yield.
    """
    rec = HighlightRecorder()
    rec.start()
    try:
        yield rec
    finally:
        rec.stop()


@pytest.mark.asyncio
async def test_snapshot_ranks_by_score(recorder: HighlightRecorder) -> None:
    sid = "sess-1"
    # Emit one of each kind. Boss should top the list (weight 10).
    await bus.emit("agent.level_up", session_id=sid, name="Coder", level=5)
    await bus.emit("world.event_recorded", session_id=sid, title="A storm")
    await bus.emit("pr.opened", session_id=sid, title="Add highlights")
    await bus.emit("raid.victory", session_id=sid, title="Server raid")
    await bus.emit("boss.defeated", session_id=sid, name="Dragon")

    cands = recorder.snapshot(sid)
    kinds = [c.kind for c in cands]
    assert kinds[0] == "boss.defeated"
    # Order should follow the documented weight ladder:
    # boss > raid > donation? (none here) > pr > level_up > world_event
    assert kinds == [
        "boss.defeated",
        "raid.victory",
        "pr.opened",
        "agent.level_up",
        "world.event_recorded",
    ]


@pytest.mark.asyncio
async def test_max_clips_cap(recorder: HighlightRecorder, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "sess-cap"
    # Force a tight cap so we can verify truncation.
    monkeypatch.setattr(settings, "highlights_max_clips", 2)
    for i in range(6):
        await bus.emit("boss.defeated", session_id=sid, name=f"Boss-{i}")
    cands = recorder.snapshot(sid)
    assert len(cands) == 2
    # Both winners should be the most recent two thanks to the recency
    # boost — names "Boss-5" and "Boss-4" in that order.
    assert cands[0].payload["name"] == "Boss-5"
    assert cands[1].payload["name"] == "Boss-4"


@pytest.mark.asyncio
async def test_events_without_session_id_are_ignored(
    recorder: HighlightRecorder,
) -> None:
    # No session_id at all → dropped on the floor.
    await bus.emit("boss.defeated", name="Lonely boss")
    # Empty string also counts as missing.
    await bus.emit("boss.defeated", session_id="", name="Still nobody")
    # A real session that *does* fire shouldn't see the noise above.
    await bus.emit("boss.defeated", session_id="sess-real", name="Real boss")

    assert recorder.snapshot("sess-real") and len(recorder.snapshot("sess-real")) == 1
    # Truly nothing buffered for blank/unset sessions.
    assert recorder.snapshot("") == []


@pytest.mark.asyncio
async def test_donation_threshold(recorder: HighlightRecorder) -> None:
    sid = "sess-donate"
    # $1 donation — too small, does not qualify.
    await bus.emit(
        "live.donation_received",
        session_id=sid,
        username="smalltipper",
        amount_usd=1.0,
    )
    # $25 donation — clearly reel-worthy.
    await bus.emit(
        "live.donation_received",
        session_id=sid,
        username="bigfan",
        amount_usd=25.0,
    )
    # Cents-form payload, $10 → also in.
    await bus.emit(
        "live.donation_received",
        session_id=sid,
        username="centsfan",
        amount_cents=1000,
    )

    cands = recorder.snapshot(sid)
    titles = [c.title for c in cands]
    assert all("smalltipper" not in t for t in titles)
    assert any("bigfan" in t for t in titles)
    assert any("centsfan" in t for t in titles)
    # Both qualifying donations share the donation weight, so the more
    # recent (centsfan) outranks bigfan thanks to the recency boost.
    assert titles[0].startswith("centsfan")


@pytest.mark.asyncio
async def test_start_stop_idempotent() -> None:
    rec = HighlightRecorder()
    # Repeated start: still only one handler set wired up.
    rec.start()
    rec.start()
    sid = "sess-idem"
    await bus.emit("boss.defeated", session_id=sid, name="Boss")
    cands = rec.snapshot(sid)
    # Exactly one candidate (handlers weren't double-registered).
    assert len(cands) == 1

    rec.stop()
    rec.stop()
    # After stop, further events are ignored entirely.
    await bus.emit("boss.defeated", session_id=sid, name="After stop")
    assert len(rec.snapshot(sid)) == 1


@pytest.mark.asyncio
async def test_disabled_setting_blocks_recording(
    recorder: HighlightRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "highlights_enabled", False)
    await bus.emit("boss.defeated", session_id="sess-off", name="Boss")
    assert recorder.snapshot("sess-off") == []


def test_singleton_returns_same_instance() -> None:
    a = get_recorder()
    b = get_recorder()
    assert a is b
