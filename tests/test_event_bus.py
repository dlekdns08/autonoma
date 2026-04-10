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
