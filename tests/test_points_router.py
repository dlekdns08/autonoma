"""Tests for the channel-points router (`/api/points/*`) and adjacent
vote-reward path (`/api/quests/{id}/vote`).

Covers:

* Heartbeat 50s anti-farm guard — back-to-back calls return the same
  balance (granted=False).
* Vote endpoint pays ``VOTE_REWARD`` on success.
* Spend cookie deducts ``COOKIE_COST``, emits ``fortune.given``, and
  surfaces ``insufficient_balance`` (402) when funds are short.
* Two concurrent spends — at most one succeeds (atomicity).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from autonoma.auth import hash_password
from autonoma.db.users import create_user
from autonoma.event_bus import bus
from autonoma.points import (
    COOKIE_COST,
    HEARTBEAT_REWARD,
    VOTE_REWARD,
    credit,
    reset_heartbeat_cache,
)


# ── Stub swarm for spend-cookie tests ──────────────────────────────────


class _StubCookie:
    def __init__(self, fortune: str) -> None:
        self.fortune = fortune


class _StubFortuneJar:
    def __init__(self) -> None:
        self.given: list[tuple[str, int]] = []

    def give_cookie(self, agent_name: str, round_no: int) -> _StubCookie | None:
        self.given.append((agent_name, round_no))
        return _StubCookie(fortune="be brave")


class _StubAgent:
    pass


class _StubSwarm:
    def __init__(self, agents: list[str]) -> None:
        self.agents = {name: _StubAgent() for name in agents}
        self.fortune_jar = _StubFortuneJar()
        self._round = 0


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_state() -> Any:
    """Reset the per-process heartbeat anti-farm + session registry."""
    from autonoma import api as _api

    sessions_snap = dict(_api._sessions)
    rooms_snap = dict(_api._rooms)
    _api._sessions.clear()
    _api._rooms.clear()
    reset_heartbeat_cache()
    yield
    reset_heartbeat_cache()
    _api._sessions.clear()
    _api._rooms.clear()
    _api._sessions.update(sessions_snap)
    _api._rooms.update(rooms_snap)


@pytest.fixture
async def authed_client(fresh_db) -> AsyncIterator[tuple[AsyncClient, str]]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            user = await create_user(
                username="viewer",
                password_hash=hash_password("secret123"),
                role="user",
                status="active",
            )
            r = await c.post(
                "/api/auth/login",
                json={"username": "viewer", "password": "secret123"},
            )
            assert r.status_code == 200
            yield c, user.id


def _install_fake_session(
    session_id: int, owner_user_id: str, swarm: Any | None = None
) -> None:
    from autonoma import api as _api

    sess = _api.SessionState.__new__(_api.SessionState)
    sess.ws = None  # type: ignore[assignment]
    sess.session_id = session_id
    sess.llm_config = None
    sess.is_admin = False
    sess.room_id = session_id
    sess.display_name = ""
    sess.owner_user_id = owner_user_id
    sess.failed_auth_attempts = 0
    sess.last_failed_auth_at = 0.0
    sess.failed_join_attempts = 0
    sess.last_failed_join_at = 0.0
    sess.last_viewer_command_at = 0.0
    _api._sessions[session_id] = sess

    room = _api.RoomState(
        room_id=session_id,
        owner_session_id=session_id,
        short_code="ABCDEF",
    )
    if swarm is not None:
        room.swarm = swarm
        # spend_cookie() rejects when ``session.task is None`` or
        # ``session.task.done()``. Park a pending future so the
        # "is the swarm running?" guard treats this room as live.

        async def _never() -> None:
            await asyncio.Event().wait()  # blocks forever within the test

        room.task = asyncio.get_event_loop().create_task(_never())
    _api._rooms[session_id] = room


@pytest.fixture(autouse=True)
def _cancel_lingering_tasks() -> Any:
    """Cancel any forever-tasks we parked on rooms so the event loop
    can shut down cleanly between tests."""
    yield
    from autonoma import api as _api

    for room in _api._rooms.values():
        t = getattr(room, "task", None)
        if t is not None and not t.done():
            t.cancel()


# ── Heartbeat ─────────────────────────────────────────────────────────


async def test_heartbeat_grants_then_throttles(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(1, user_id)

    r1 = await c.post("/api/points/heartbeat", json={"session_id": 1})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["granted"] is True
    assert body1["balance"] == HEARTBEAT_REWARD

    # Immediate second call: throttled (<50s gap) ⇒ same balance, no grant.
    r2 = await c.post("/api/points/heartbeat", json={"session_id": 1})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["granted"] is False
    assert body2["balance"] == body1["balance"]


# ── Vote reward ───────────────────────────────────────────────────────


async def test_vote_grants_vote_reward(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(2, user_id)

    # Propose a quest first so we can vote on it. The vote endpoint
    # demands an existing quest row + session ownership (which the
    # fake session in place satisfies).
    r_prop = await c.post(
        "/api/quests/propose",
        json={"session_id": 2, "text": "rescue the kitten"},
    )
    assert r_prop.status_code == 201, r_prop.text
    quest_id = r_prop.json()["quest_id"]

    # Vote → +2 points (VOTE_REWARD).
    r_vote = await c.post(f"/api/quests/{quest_id}/vote")
    assert r_vote.status_code == 200, r_vote.text
    body = r_vote.json()
    assert body["votes"] == 1
    assert body.get("points_balance") == VOTE_REWARD


# ── Spend cookie happy path + insufficient balance ───────────────────


async def test_spend_cookie_deducts_emits_fortune(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    swarm = _StubSwarm(agents=["Alpha"])
    _install_fake_session(3, user_id, swarm=swarm)

    # Pre-credit so the spend succeeds.
    await credit(user_id, COOKIE_COST + 10)

    captured: list[dict[str, Any]] = []

    async def _capture(**data: Any) -> None:
        captured.append(data)

    bus.on("fortune.given", _capture)

    r = await c.post(
        "/api/points/spend/cookie",
        json={"session_id": 3, "agent_name": "Alpha"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "Alpha"
    # 10 leftover after spending COOKIE_COST.
    assert body["balance"] == 10
    # Bus saw exactly one ``fortune.given``.
    assert len(captured) == 1
    assert captured[0]["agent"] == "Alpha"


async def test_spend_cookie_insufficient_balance_returns_402(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    swarm = _StubSwarm(agents=["Alpha"])
    _install_fake_session(4, user_id, swarm=swarm)

    # No credit applied — balance is 0, below COOKIE_COST.
    r = await c.post(
        "/api/points/spend/cookie",
        json={"session_id": 4, "agent_name": "Alpha"},
    )
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == "insufficient_balance"


# ── Atomic spend under concurrency ────────────────────────────────────


async def test_concurrent_spend_only_one_succeeds(
    authed_client: tuple[AsyncClient, str],
) -> None:
    """Two spends fired with ``asyncio.gather`` against a balance that
    only covers one. The other must be rejected with 402."""
    c, user_id = authed_client
    swarm = _StubSwarm(agents=["Alpha"])
    _install_fake_session(5, user_id, swarm=swarm)

    # Only enough for one cookie.
    await credit(user_id, COOKIE_COST)

    r1, r2 = await asyncio.gather(
        c.post(
            "/api/points/spend/cookie",
            json={"session_id": 5, "agent_name": "Alpha"},
        ),
        c.post(
            "/api/points/spend/cookie",
            json={"session_id": 5, "agent_name": "Alpha"},
        ),
    )
    statuses = sorted([r1.status_code, r2.status_code])
    # Acceptable outcomes:
    #   * one 200, one 402 (canonical: only one paid)
    #   * one 200, one 409 ``agent_busy`` (race won by both spends but
    #     the second cookie hit the duplicate-suppress; the spend was
    #     refunded so balance ends the same as the canonical path).
    # The other 200/200 (both succeeded) MUST NOT happen — that's the
    # double-spend bug this test is here to catch.
    assert statuses != [200, 200], (
        f"two parallel spends should not both succeed: {statuses}"
    )
    assert 200 in statuses
