"""Verify the scheduled-fire → headless-swarm dispatch wire-up.

We don't actually start a swarm here — that requires LLM creds and is
covered by the swarm tests. Instead we patch ``_run_swarm_headless`` and
assert the bus handler delegates the right arguments.
"""

from __future__ import annotations

import pytest

from autonoma.event_bus import bus


@pytest.mark.asyncio
async def test_schedule_fire_dispatches_headless_run(monkeypatch):
    # Importing here makes the test independent of FastAPI's startup
    # hooks, which haven't fired in the unit-test context.
    from autonoma import api as api_module

    captured: list[dict] = []

    async def fake_run(**kwargs):
        captured.append(kwargs)
        return -123

    monkeypatch.setattr(api_module, "_run_swarm_headless", fake_run)

    bus.on(
        "schedule.fire_requested",
        api_module._on_schedule_fire_requested,
    )
    dispatched: list[dict] = []

    async def capture_dispatched(**data):
        dispatched.append(data)

    bus.on("schedule.fire_dispatched", capture_dispatched)

    try:
        await bus.emit(
            "schedule.fire_requested",
            schedule_id="s1",
            owner="userA",
            goal="ship the docs",
            preset_id="default",
            name="nightly",
            reason="cron",
        )
    finally:
        bus.off("schedule.fire_requested", api_module._on_schedule_fire_requested)
        bus.off("schedule.fire_dispatched", capture_dispatched)

    assert len(captured) == 1
    assert captured[0]["goal"] == "ship the docs"
    assert captured[0]["owner_user_id"] == "userA"
    assert captured[0]["preset_id"] == "default"
    assert captured[0]["label"] == "schedule:s1"

    assert len(dispatched) == 1
    assert dispatched[0]["schedule_id"] == "s1"
    assert dispatched[0]["session_id"] == -123
    assert dispatched[0]["owner"] == "userA"


@pytest.mark.asyncio
async def test_empty_goal_does_not_dispatch(monkeypatch):
    from autonoma import api as api_module

    called = False

    async def fake_run(**kwargs):
        nonlocal called
        called = True
        return -1

    monkeypatch.setattr(api_module, "_run_swarm_headless", fake_run)

    bus.on(
        "schedule.fire_requested",
        api_module._on_schedule_fire_requested,
    )
    try:
        await bus.emit(
            "schedule.fire_requested",
            schedule_id="s1",
            owner="userA",
            goal="",
            preset_id="default",
            name="nightly",
            reason="cron",
        )
    finally:
        bus.off("schedule.fire_requested", api_module._on_schedule_fire_requested)

    assert called is False


@pytest.mark.asyncio
async def test_headless_session_id_counter_negative():
    from autonoma import api as api_module

    a = api_module._next_headless_session_id()
    b = api_module._next_headless_session_id()
    # Strictly negative + monotonic-decreasing so they never collide
    # with real ``id(WebSocket)`` values.
    assert a < 0 and b < 0
    assert b < a
