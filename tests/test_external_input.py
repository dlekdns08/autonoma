"""Tests for the external input router."""

from __future__ import annotations

import pytest

from autonoma.external_input import (
    ExternalInputRouter,
    ExternalMessage,
    RouteAction,
)


class _FakeSwarm:
    def __init__(self, accept: bool = True) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.accept = accept

    async def inject_human_message(self, text: str, target: str | None = None) -> bool:
        self.calls.append((text, target))
        return self.accept


@pytest.fixture
def router():
    r = ExternalInputRouter()
    # Push the test source's cap up so tests don't false-positive on
    # rate limits when they aren't the subject under test.
    r.set_rate_cap("test", 1000)
    return r


@pytest.mark.asyncio
async def test_dropped_when_no_swarm_bound(router):
    res = await router.submit(
        ExternalMessage(source="test", user="alice", text="hello")
    )
    assert res.action is RouteAction.DROPPED_NO_SWARM


@pytest.mark.asyncio
async def test_injected_when_swarm_bound(router):
    swarm = _FakeSwarm()
    router.bind_swarm(swarm)
    res = await router.submit(
        ExternalMessage(source="test", user="alice", text="hello")
    )
    assert res.action is RouteAction.INJECTED
    assert len(swarm.calls) == 1
    assert "[test:alice] hello" in swarm.calls[0][0]


@pytest.mark.asyncio
async def test_blocked_source(router):
    swarm = _FakeSwarm()
    router.bind_swarm(swarm)
    router.block("test")
    res = await router.submit(
        ExternalMessage(source="test", user="alice", text="hi")
    )
    assert res.action is RouteAction.DROPPED_BLOCKED_SOURCE
    assert swarm.calls == []


@pytest.mark.asyncio
async def test_rate_limit(router):
    swarm = _FakeSwarm()
    router.bind_swarm(swarm)
    router.set_rate_cap("test", 2)
    a = await router.submit(ExternalMessage(source="test", user="u1", text="1"))
    b = await router.submit(ExternalMessage(source="test", user="u1", text="2"))
    c = await router.submit(ExternalMessage(source="test", user="u1", text="3"))
    assert a.action is RouteAction.INJECTED
    assert b.action is RouteAction.INJECTED
    assert c.action is RouteAction.DROPPED_RATE_LIMIT
    # Per-user isolation: u2 still has full quota.
    d = await router.submit(ExternalMessage(source="test", user="u2", text="4"))
    assert d.action is RouteAction.INJECTED


@pytest.mark.asyncio
async def test_active_poll_consumes_message(router):
    swarm = _FakeSwarm()
    router.bind_swarm(swarm)
    router.open_poll("p1", "Color?", ["blue", "red"], duration_sec=5)
    res = await router.submit(
        ExternalMessage(source="test", user="alice", text="blue please!")
    )
    assert res.action is RouteAction.VOTED
    assert res.detail == "blue"
    # Poll consumes the message — swarm shouldn't have seen it.
    assert swarm.calls == []


@pytest.mark.asyncio
async def test_poll_one_vote_per_user(router):
    router.bind_swarm(_FakeSwarm())
    router.open_poll("p2", "?", ["a", "b"], duration_sec=5)
    res1 = await router.submit(ExternalMessage(source="test", user="a", text="a"))
    res2 = await router.submit(ExternalMessage(source="test", user="a", text="b"))
    assert res1.action is RouteAction.VOTED
    # Second vote falls through to swarm injection (no longer counts).
    assert res2.action is RouteAction.INJECTED


@pytest.mark.asyncio
async def test_poll_numeric_index(router):
    router.bind_swarm(_FakeSwarm())
    poll = router.open_poll("p3", "?", ["alpha", "beta", "gamma"], duration_sec=5)
    res = await router.submit(ExternalMessage(source="test", user="x", text="2"))
    assert res.action is RouteAction.VOTED
    assert poll.tallies["beta"] == 1


@pytest.mark.asyncio
async def test_swarm_rejects_then_dropped(router):
    swarm = _FakeSwarm(accept=False)
    router.bind_swarm(swarm)
    res = await router.submit(
        ExternalMessage(source="test", user="alice", text="hello")
    )
    assert res.action is RouteAction.DROPPED_NO_SWARM
