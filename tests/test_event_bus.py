"""Tests for event bus."""

import pytest
from autonoma.event_bus import EventBus


@pytest.fixture
def ebus():
    return EventBus()


@pytest.mark.asyncio
async def test_basic(ebus):
    received = []

    async def handler(**data):
        received.append(data)

    ebus.on("test", handler)
    await ebus.emit("test", value=42)
    assert len(received) == 1
    assert received[0]["value"] == 42


@pytest.mark.asyncio
async def test_wildcard(ebus):
    received = []

    async def handler(**data):
        received.append(1)

    ebus.on("*", handler)
    await ebus.emit("anything")
    await ebus.emit("else")
    assert len(received) == 2


@pytest.mark.asyncio
async def test_prefix_wildcard(ebus):
    received = []

    async def handler(**data):
        received.append(1)

    ebus.on("agent.*", handler)
    await ebus.emit("agent.started")
    await ebus.emit("agent.done")
    await ebus.emit("task.done")
    assert len(received) == 2


@pytest.mark.asyncio
async def test_off(ebus):
    received = []

    async def handler(**data):
        received.append(1)

    ebus.on("e", handler)
    ebus.off("e", handler)
    await ebus.emit("e")
    assert received == []


@pytest.mark.asyncio
async def test_tap_sees_every_event(ebus):
    seen: list[tuple[str, dict]] = []

    async def tap(event_name: str, data: dict) -> None:
        seen.append((event_name, data))

    ebus.tap(tap)
    await ebus.emit("a", x=1)
    await ebus.emit("b.c", y=2)
    assert seen == [("a", {"x": 1}), ("b.c", {"y": 2})]


@pytest.mark.asyncio
async def test_tap_runs_alongside_handlers(ebus):
    handler_calls = []
    tap_calls = []

    async def handler(**data):
        handler_calls.append(data)

    async def tap(event_name, data):
        tap_calls.append(event_name)

    ebus.on("only-this", handler)
    ebus.tap(tap)
    await ebus.emit("only-this", v=1)
    await ebus.emit("other", v=2)
    assert len(handler_calls) == 1
    assert tap_calls == ["only-this", "other"]


@pytest.mark.asyncio
async def test_untap(ebus):
    seen = []

    async def tap(event_name, data):
        seen.append(event_name)

    ebus.tap(tap)
    await ebus.emit("a")
    ebus.untap(tap)
    await ebus.emit("b")
    assert seen == ["a"]


@pytest.mark.asyncio
async def test_tap_error_does_not_break_emit(ebus):
    other_calls = []

    async def bad_tap(event_name, data):
        raise RuntimeError("boom")

    async def good_handler(**data):
        other_calls.append(1)

    ebus.tap(bad_tap)
    ebus.on("e", good_handler)
    await ebus.emit("e", v=1)
    # Despite the tap raising, the regular handler should have run.
    assert other_calls == [1]
