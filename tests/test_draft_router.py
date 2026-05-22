"""Tests for the Viewer Fantasy Draft router (`/api/sessions/.../draft*`).

Covers:

* ``normalize_picks`` rejects every malformed payload shape.
* POST then GET round-trip persists the roster and renders on the
  scoreboard.
* Re-submit overwrites picks in place (no duplicate viewer rows).
* Scoreboard score reflects durable ``character_run_xp`` + 10 ×
  ``earned_achievements`` counts.
* Live-swarm fallback fires when both durable counts are 0 — the
  in-memory swarm stub supplies the score instead.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select

from autonoma.auth import hash_password
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import (
    character_run_xp,
    characters,
    earned_achievements,
    viewer_drafts,
)
from autonoma.db.users import create_user
from autonoma.draft import (
    PICK_COUNT,
    InvalidPicks,
    normalize_picks,
)

# ── Validation (pure functions, no DB needed) ─────────────────────────


def test_normalize_picks_rejects_fewer_than_three() -> None:
    with pytest.raises(InvalidPicks):
        normalize_picks(["a", "b"])
    with pytest.raises(InvalidPicks):
        normalize_picks([])


def test_normalize_picks_rejects_more_than_three() -> None:
    with pytest.raises(InvalidPicks):
        normalize_picks(["a", "b", "c", "d"])


def test_normalize_picks_rejects_duplicates() -> None:
    with pytest.raises(InvalidPicks):
        normalize_picks(["alpha", "beta", "alpha"])


def test_normalize_picks_rejects_empty_strings() -> None:
    with pytest.raises(InvalidPicks):
        normalize_picks(["alpha", "  ", "gamma"])
    with pytest.raises(InvalidPicks):
        normalize_picks(["alpha", "", "gamma"])


def test_normalize_picks_rejects_non_strings() -> None:
    with pytest.raises(InvalidPicks):
        normalize_picks(["alpha", 42, "gamma"])
    with pytest.raises(InvalidPicks):
        normalize_picks(["alpha", None, "gamma"])
    # The whole payload must be list-like.
    with pytest.raises(InvalidPicks):
        normalize_picks("alpha,beta,gamma")


def test_normalize_picks_strips_and_returns_clean() -> None:
    out = normalize_picks(["  alpha ", "beta", "gamma "])
    assert out == ["alpha", "beta", "gamma"]
    assert len(out) == PICK_COUNT


# ── HTTP fixtures ─────────────────────────────────────────────────────


class _StubAgent:
    """Lightweight stand-in for an ``AutonomousAgent`` in swarm tests."""

    def __init__(self, name: str, character_uuid: str = "", xp: int = 0) -> None:
        self.name = name
        self.character_uuid = character_uuid

        class _Persona:
            emoji = "🦊"
            role = "tester"

        self.persona = _Persona()
        self.mood = None

        class _Stats:
            def __init__(self, _xp: int) -> None:
                self.total_xp_earned = _xp
                self.achievements: list[str] = []

        self.stats = _Stats(xp)


class _StubSwarm:
    def __init__(self, agents: dict[str, _StubAgent]) -> None:
        self.agents = agents
        self.registry = type("R", (), {"project_uuid": None})()
        self._round = 0


@pytest.fixture
async def authed_client(fresh_db) -> AsyncIterator[tuple[AsyncClient, str]]:
    """Cookie-authenticated client for a freshly created active user."""
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            user = await create_user(
                username="drafter",
                password_hash=hash_password("secret123"),
                role="user",
                status="active",
            )
            r = await c.post(
                "/api/auth/login",
                json={"username": "drafter", "password": "secret123"},
            )
            assert r.status_code == 200, r.text
            yield c, user.id


@pytest.fixture(autouse=True)
def _isolate_session_registry() -> Any:
    """The draft router queries ``api._sessions`` to validate session
    existence. Stash + clear so the test can install its own session
    fixtures, then restore on teardown."""
    from autonoma import api as _api

    snapshot = dict(_api._sessions)
    _api._sessions.clear()
    yield
    _api._sessions.clear()
    _api._sessions.update(snapshot)


def _install_fake_session(
    session_id: int, owner_user_id: str, swarm: Any = None
) -> None:
    """Drop a minimal ``SessionState`` into ``_sessions`` so owner +
    existence checks pass for this test."""
    from autonoma import api as _api

    sess = _api.SessionState.__new__(_api.SessionState)
    sess.ws = None  # type: ignore[assignment]
    sess.session_id = session_id
    sess.llm_config = None
    sess.is_admin = False
    sess.room_id = session_id
    sess.display_name = "drafter"
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
        short_code="TESTCD",
    )
    if swarm is not None:
        room.swarm = swarm
    _api._rooms[session_id] = room


# ── Round-trip & overwrite ─────────────────────────────────────────────


async def test_post_then_get_roundtrip(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(101, user_id)

    body = {"picks": ["Alpha", "Beta", "Gamma"]}
    r = await c.post("/api/sessions/101/draft", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["draft"]["picks"] == ["Alpha", "Beta", "Gamma"]

    # Scoreboard reflects the draft.
    s = await c.get("/api/sessions/101/draft/scoreboard")
    assert s.status_code == 200, s.text
    sb = s.json()
    assert sb["my_rank"] == 1
    assert sb["my_picks"] == ["Alpha", "Beta", "Gamma"]
    assert len(sb["rows"]) == 1
    assert sb["rows"][0]["picks"] == ["Alpha", "Beta", "Gamma"]


async def test_resubmit_overwrites_no_duplicate_rows(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(202, user_id)

    await c.post(
        "/api/sessions/202/draft",
        json={"picks": ["A", "B", "C"]},
    )
    await c.post(
        "/api/sessions/202/draft",
        json={"picks": ["X", "Y", "Z"]},
    )

    # Only one row, with the latest picks.
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(viewer_drafts).where(viewer_drafts.c.session_id == 202)
            )
        ).all()
    assert len(rows) == 1
    decoded = json.loads(rows[0]._mapping["picks_json"])
    assert decoded == ["X", "Y", "Z"]


# ── Durable scoring (xp + achievements) ────────────────────────────────


async def _seed_character(name: str) -> str:
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
                is_alive=1,
            )
        )
    return uid


async def test_durable_score_uses_run_xp_and_achievements(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client

    # Three characters; each gets durable xp + maybe achievements.
    uid_a = await _seed_character("Alpha")
    uid_b = await _seed_character("Beta")
    uid_c = await _seed_character("Gamma")

    session_id = 303

    # Install a swarm whose agents carry the same character_uuids so
    # the resolver can map names → uuids. Use 0 xp/achievements live so
    # the in-memory fallback can't accidentally inflate the score.
    swarm = _StubSwarm(
        {
            "Alpha": _StubAgent("Alpha", character_uuid=uid_a, xp=0),
            "Beta": _StubAgent("Beta", character_uuid=uid_b, xp=0),
            "Gamma": _StubAgent("Gamma", character_uuid=uid_c, xp=0),
        }
    )
    _install_fake_session(session_id, user_id, swarm=swarm)

    engine = get_engine()
    async with engine.begin() as conn:
        # XP: Alpha=10, Beta=20, Gamma=30 → sum 60.
        await conn.execute(
            insert(character_run_xp).values(
                session_id=session_id, character_uuid=uid_a, xp=10
            )
        )
        await conn.execute(
            insert(character_run_xp).values(
                session_id=session_id, character_uuid=uid_b, xp=20
            )
        )
        await conn.execute(
            insert(character_run_xp).values(
                session_id=session_id, character_uuid=uid_c, xp=30
            )
        )
        # Achievements: Alpha=2 → +20, Beta=1 → +10, Gamma=0.
        await conn.execute(
            insert(earned_achievements).values(
                character_uuid=uid_a, achievement_id="ach_a1", tier="bronze"
            )
        )
        await conn.execute(
            insert(earned_achievements).values(
                character_uuid=uid_a, achievement_id="ach_a2", tier="bronze"
            )
        )
        await conn.execute(
            insert(earned_achievements).values(
                character_uuid=uid_b, achievement_id="ach_b1", tier="bronze"
            )
        )

    # Submit the roster and read the scoreboard.
    await c.post(
        f"/api/sessions/{session_id}/draft",
        json={"picks": ["Alpha", "Beta", "Gamma"]},
    )
    sb = (await c.get(f"/api/sessions/{session_id}/draft/scoreboard")).json()

    # 10 + 20 + 30 + (2+1)*10 = 90
    assert sb["rows"][0]["score"] == 90


async def test_live_swarm_fallback_when_durable_counts_are_zero(
    authed_client: tuple[AsyncClient, str],
) -> None:
    """No ``character_run_xp`` / ``earned_achievements`` rows: the
    scoreboard should fall back to the live swarm stats."""
    c, user_id = authed_client

    session_id = 404
    swarm = _StubSwarm(
        {
            # No character_uuid → resolver fails durable lookup,
            # full live fallback hits ``_live_stats_score``.
            "Alpha": _StubAgent("Alpha", character_uuid="", xp=42),
            "Beta": _StubAgent("Beta", character_uuid="", xp=8),
            "Gamma": _StubAgent("Gamma", character_uuid="", xp=0),
        }
    )
    _install_fake_session(session_id, user_id, swarm=swarm)

    await c.post(
        f"/api/sessions/{session_id}/draft",
        json={"picks": ["Alpha", "Beta", "Gamma"]},
    )
    sb = (await c.get(f"/api/sessions/{session_id}/draft/scoreboard")).json()
    # 42 + 8 + 0 (all from live stats; no achievements).
    assert sb["rows"][0]["score"] == 50


# ── DraftError handling on the wire ────────────────────────────────────


async def test_draft_error_returns_400(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(505, user_id)

    # Pydantic enforces exactly 3; pass duplicates instead.
    r = await c.post(
        "/api/sessions/505/draft",
        json={"picks": ["A", "A", "A"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_picks"
