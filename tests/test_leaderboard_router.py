"""Tests for the cross-session character leaderboard router.

Endpoint: ``GET /api/leaderboard/characters?metric=xp|runs_survived|achievements&limit=N``.

Covers:

* Metric=xp orders rows by ``total_xp_earned`` desc.
* Metric=runs_survived re-sorts on that column.
* Metric=achievements re-sorts on the joined badge count.
* Invalid metric → Pydantic-level 422 (FastAPI default for Query enum
  validation). We assert "rejects unknown metric" without pinning the
  exact code so a future tightening to 400 stays painless.
* Limit boundaries: FastAPI's ``ge=1, le=200`` makes ``limit=0`` and
  ``limit=201`` return 422 (silently clamped is NOT the current
  behaviour). We pin the current behaviour so a future relaxation is
  intentional.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert

from autonoma.auth import hash_password
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import characters, earned_achievements
from autonoma.db.users import create_user

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def authed_client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            await create_user(
                username="lb",
                password_hash=hash_password("secret123"),
                role="user",
                status="active",
            )
            r = await c.post(
                "/api/auth/login",
                json={"username": "lb", "password": "secret123"},
            )
            assert r.status_code == 200
            yield c


async def _seed_character(
    *,
    name: str,
    xp: int = 0,
    runs_survived: int = 0,
    achievements: int = 0,
) -> str:
    """Insert a characters row and N achievements rows. Returns uuid."""
    await init_db()
    engine = get_engine()
    uid = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            insert(characters).values(
                character_uuid=uid,
                seed_hash="seed",
                name=name,
                role="tester",
                species="cat",
                species_emoji="🐱",
                catchphrase="",
                rarity="common",
                level=1,
                total_xp_earned=xp,
                runs_survived=runs_survived,
                is_alive=1,
            )
        )
        for i in range(achievements):
            await conn.execute(
                insert(earned_achievements).values(
                    character_uuid=uid,
                    achievement_id=f"ach_{name}_{i}",
                    tier="bronze",
                )
            )
    return uid


# ── metric=xp ─────────────────────────────────────────────────────────


async def test_metric_xp_orders_by_xp_desc(authed_client: AsyncClient) -> None:
    await _seed_character(name="Lo", xp=10, runs_survived=99, achievements=0)
    await _seed_character(name="Mid", xp=50, runs_survived=1, achievements=2)
    await _seed_character(name="Hi", xp=100, runs_survived=0, achievements=0)

    r = await authed_client.get("/api/leaderboard/characters?metric=xp")
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    names = [row["name"] for row in rows]
    assert names == ["Hi", "Mid", "Lo"]


# ── metric=runs_survived ──────────────────────────────────────────────


async def test_metric_runs_survived_re_sorts(authed_client: AsyncClient) -> None:
    await _seed_character(name="Lo", xp=10, runs_survived=99, achievements=0)
    await _seed_character(name="Mid", xp=50, runs_survived=1, achievements=2)
    await _seed_character(name="Hi", xp=100, runs_survived=0, achievements=0)

    r = await authed_client.get(
        "/api/leaderboard/characters?metric=runs_survived"
    )
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    names = [row["name"] for row in rows]
    assert names == ["Lo", "Mid", "Hi"]


# ── metric=achievements ───────────────────────────────────────────────


async def test_metric_achievements_re_sorts(authed_client: AsyncClient) -> None:
    await _seed_character(name="Lo", xp=10, runs_survived=99, achievements=0)
    await _seed_character(name="Mid", xp=50, runs_survived=1, achievements=2)
    await _seed_character(name="Hi", xp=100, runs_survived=0, achievements=5)

    r = await authed_client.get(
        "/api/leaderboard/characters?metric=achievements"
    )
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    names = [row["name"] for row in rows]
    assert names == ["Hi", "Mid", "Lo"]
    # achievement_count surfaces on every row regardless of metric.
    by_name = {row["name"]: row["achievement_count"] for row in rows}
    assert by_name == {"Hi": 5, "Mid": 2, "Lo": 0}


# ── invalid metric / limit clamps ─────────────────────────────────────


async def test_invalid_metric_rejected(authed_client: AsyncClient) -> None:
    r = await authed_client.get("/api/leaderboard/characters?metric=banana")
    # FastAPI returns 422 for ``Literal``/enum validation failures.
    # Accept either 400 or 422 — both communicate "bad request" and the
    # spec leaves the choice up to the router author.
    assert r.status_code in (400, 422), r.text


async def test_limit_zero_rejected(authed_client: AsyncClient) -> None:
    """``limit=0`` falls outside ``ge=1`` ⇒ 422 (Pydantic). Pin the
    current behaviour — silently clamping isn't what the router does."""
    r = await authed_client.get("/api/leaderboard/characters?limit=0")
    assert r.status_code == 422, r.text


async def test_limit_too_large_rejected(authed_client: AsyncClient) -> None:
    """``limit=201`` is above ``le=200`` ⇒ 422."""
    r = await authed_client.get("/api/leaderboard/characters?limit=201")
    assert r.status_code == 422, r.text


async def test_limit_caps_returned_rows(authed_client: AsyncClient) -> None:
    """Limit applies on the returned slice — three rows in the DB,
    limit=2 returns only the top two."""
    await _seed_character(name="Lo", xp=10)
    await _seed_character(name="Mid", xp=50)
    await _seed_character(name="Hi", xp=100)

    r = await authed_client.get("/api/leaderboard/characters?limit=2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert [row["name"] for row in body["rows"]] == ["Hi", "Mid"]
