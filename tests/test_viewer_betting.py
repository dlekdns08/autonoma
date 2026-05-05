"""Tests for viewer betting (Feature #4).

Covers both the DB-layer module (``autonoma.viewer_betting``) and the
FastAPI router (``autonoma.routers.viewer_betting``):

DB layer
~~~~~~~~
- ``open_market`` then ``list_open_markets`` round-trips the row.
- ``place_bet`` accepts the three sanctioned stakes and rejects
  anything else with ``ValueError("invalid_stake")``.
- The unique constraint surfaces as ``ValueError("already_bet")``.
- ``resolve_market`` pays winners 3x, zeroes losers, and returns a
  summary with the right totals.
- ``leaderboard`` is sorted by net winnings descending.
- ``balance`` is ``STARTING_BALANCE + sum(payout) - sum(stake)``.

Router
~~~~~~
- Every endpoint returns 503 with ``{"code": "betting_disabled"}``
  when the feature flag is off, mounted on a throw-away FastAPI app
  with auth deps stubbed (mirrors ``tests/test_inspire.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from autonoma.viewer_betting import (
    PAYOUT_MULTIPLIER,
    STARTING_BALANCE,
    balance,
    leaderboard,
    list_open_markets,
    lock_market,
    open_market,
    place_bet,
    resolve_market,
)

# ── Test doubles ──────────────────────────────────────────────────────


class _StubUser:
    """Quacks like ``autonoma.auth.User`` for the FastAPI deps."""

    id = "viewer-1"
    username = "tester"
    status = "active"
    role = "admin"


# ── DB-layer tests ────────────────────────────────────────────────────


async def test_open_market_then_list(fresh_db):
    """A freshly opened market shows up in ``list_open_markets``."""
    market = await open_market(
        session_id=1,
        market_id="m1",
        question="will the swarm win?",
        closes_at_round=5,
    )
    assert market.market_id == "m1"
    assert market.status == "open"
    assert market.question == "will the swarm win?"
    assert market.closes_at_round == 5

    rows = await list_open_markets(session_id=1)
    assert len(rows) == 1
    assert rows[0].market_id == "m1"


async def test_place_bet_valid_stake(fresh_db):
    """All three sanctioned stake amounts are accepted."""
    await open_market(1, "m1", "?", 5)
    for i, stake in enumerate((10, 50, 100)):
        entry = await place_bet(
            session_id=1,
            market_id="m1",
            viewer_id=f"viewer-{i}",
            display_name=f"v{i}",
            option="yes",
            stake=stake,
        )
        assert entry.stake == stake
        assert entry.payout == 0
        assert entry.option == "yes"


async def test_place_bet_invalid_stake_raises(fresh_db):
    """Anything outside {10, 50, 100} → ValueError('invalid_stake')."""
    await open_market(1, "m1", "?", 5)
    with pytest.raises(ValueError) as exc_info:
        await place_bet(1, "m1", "viewer-1", "v1", "yes", 7)
    assert str(exc_info.value) == "invalid_stake"


async def test_place_bet_market_not_open_raises(fresh_db):
    """Betting on a non-existent or locked market is refused."""
    with pytest.raises(ValueError) as exc_info:
        await place_bet(1, "ghost", "viewer-1", "v1", "yes", 10)
    assert str(exc_info.value) == "market_not_open"

    await open_market(1, "m1", "?", 5)
    await lock_market(1, "m1")
    with pytest.raises(ValueError) as exc_info:
        await place_bet(1, "m1", "viewer-1", "v1", "yes", 10)
    assert str(exc_info.value) == "market_not_open"


async def test_duplicate_bet_raises_already_bet(fresh_db):
    """The unique constraint surfaces as ValueError('already_bet')."""
    await open_market(1, "m1", "?", 5)
    await place_bet(1, "m1", "viewer-1", "v1", "yes", 10)
    with pytest.raises(ValueError) as exc_info:
        await place_bet(1, "m1", "viewer-1", "v1", "no", 50)
    assert str(exc_info.value) == "already_bet"


async def test_resolve_market_payouts_and_summary(fresh_db):
    """Winners get 3x, losers 0; summary tallies totals correctly."""
    await open_market(1, "m1", "?", 5)
    # Two winners on "yes", one loser on "no".
    await place_bet(1, "m1", "v1", "v1", "yes", 100)
    await place_bet(1, "m1", "v2", "v2", "yes", 50)
    await place_bet(1, "m1", "v3", "v3", "no", 10)

    summary = await resolve_market(1, "m1", "yes")

    assert summary["market_id"] == "m1"
    assert summary["winning_option"] == "yes"
    assert summary["winners"] == 2
    assert summary["losers"] == 1
    assert summary["total_stake"] == 100 + 50 + 10
    expected_total_payout = (100 + 50) * PAYOUT_MULTIPLIER
    assert summary["total_payout"] == expected_total_payout

    # Per-viewer balance reflects the payouts.
    assert await balance(1, "v1") == STARTING_BALANCE - 100 + 100 * PAYOUT_MULTIPLIER
    assert await balance(1, "v2") == STARTING_BALANCE - 50 + 50 * PAYOUT_MULTIPLIER
    # Loser: stake gone, payout 0; floored balance still correct.
    assert await balance(1, "v3") == STARTING_BALANCE - 10


async def test_leaderboard_sorted_by_net_desc(fresh_db):
    """Leaderboard rows arrive ordered by net winnings, biggest first."""
    await open_market(1, "m1", "?", 5)
    await place_bet(1, "m1", "alice", "Alice", "yes", 100)
    await place_bet(1, "m1", "bob", "Bob", "yes", 10)
    await place_bet(1, "m1", "carol", "Carol", "no", 50)
    await resolve_market(1, "m1", "yes")

    board = await leaderboard(1)
    # Three viewers participated.
    assert len(board) == 3
    # Net winnings: alice +200, bob +20, carol -50.
    nets = [row["net"] for row in board]
    assert nets == sorted(nets, reverse=True)
    assert board[0]["viewer_id"] == "alice"
    assert board[0]["net"] == 100 * (PAYOUT_MULTIPLIER - 1)
    assert board[-1]["viewer_id"] == "carol"
    assert board[-1]["net"] == -50

    # Display names round-tripped through the GROUP BY.
    assert board[0]["display_name"] == "Alice"


async def test_balance_starts_at_starting_balance(fresh_db):
    """A viewer with no entries gets exactly STARTING_BALANCE."""
    assert await balance(1, "viewer-with-no-bets") == STARTING_BALANCE


async def test_balance_floored_at_zero(fresh_db):
    """A losing streak can't take a viewer below zero."""
    await open_market(1, "m1", "?", 5)
    await open_market(1, "m2", "?", 5)
    await open_market(1, "m3", "?", 5)
    await place_bet(1, "m1", "loser", "L", "no", 100)
    await place_bet(1, "m2", "loser", "L", "no", 100)
    await place_bet(1, "m3", "loser", "L", "no", 100)
    await resolve_market(1, "m1", "yes")
    await resolve_market(1, "m2", "yes")
    await resolve_market(1, "m3", "yes")
    # 1000 - 300 = 700, still positive — bump up another 8 markets to
    # exhaust the wallet and confirm the floor.
    for i in range(8):
        mid = f"extra-{i}"
        await open_market(1, mid, "?", 5)
        await place_bet(1, mid, "loser", "L", "no", 100)
        await resolve_market(1, mid, "yes")
    bal = await balance(1, "loser")
    assert bal == 0


# ── Router tests ──────────────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    """Tiny app with just the betting router mounted.

    Auth dependencies are overridden to a stub user. We are only
    testing the feature-flag short circuit here; the DB-layer tests
    above cover the business logic.
    """
    from autonoma.auth import require_active_user, require_admin
    from autonoma.routers import viewer_betting as router_mod

    a = FastAPI()
    a.include_router(router_mod.router)
    a.dependency_overrides[require_active_user] = lambda: _StubUser()
    a.dependency_overrides[require_admin] = lambda: _StubUser()
    return a


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_router_503_when_disabled_post_market(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /api/betting/markets`` short-circuits to 503 when off."""
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.post(
        "/api/betting/markets",
        json={
            "session_id": 1,
            "market_id": "m1",
            "question": "?",
            "closes_at_round": 5,
        },
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"


async def test_router_503_when_disabled_get_markets(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.get("/api/betting/markets", params={"session_id": 1})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"


async def test_router_503_when_disabled_place_bet(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.post(
        "/api/betting/markets/m1/bet",
        params={"session_id": 1},
        json={"option": "yes", "stake": 10},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"


async def test_router_503_when_disabled_lock(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.post(
        "/api/betting/markets/m1/lock",
        params={"session_id": 1},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"


async def test_router_503_when_disabled_resolve(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.post(
        "/api/betting/markets/m1/resolve",
        params={"session_id": 1},
        json={"winning_option": "yes"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"


async def test_router_503_when_disabled_leaderboard(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.get(
        "/api/betting/leaderboard",
        params={"session_id": 1, "limit": 10},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"


async def test_router_503_when_disabled_balance(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "viewer_betting_enabled", False)

    r = await client.get(
        "/api/betting/balance",
        params={"session_id": 1},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "betting_disabled"
