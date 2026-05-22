"""Tests for the per-user quest-templates router (`/api/quest-templates`).

Covers:

* CRUD round-trip: POST → GET → DELETE → GET empty.
* 256-char cap enforced on POST (matches ``quests.MAX_TEXT_LEN``).
* Owner scoping: user A's templates are not visible to user B.
* Unauthenticated access — POST/DELETE return 401; GET behaviour is
  whatever ``require_active_user`` returns (we pin it).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from autonoma.auth import hash_password
from autonoma.db.users import create_user
from autonoma.quests import MAX_TEXT_LEN

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_state() -> Any:
    from autonoma import api as _api

    sessions_snap = dict(_api._sessions)
    rooms_snap = dict(_api._rooms)
    _api._sessions.clear()
    _api._rooms.clear()
    yield
    _api._sessions.clear()
    _api._rooms.clear()
    _api._sessions.update(sessions_snap)
    _api._rooms.update(rooms_snap)


@pytest.fixture
async def app_client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _create_and_login(c: AsyncClient, username: str, password: str) -> str:
    user = await create_user(
        username=username,
        password_hash=hash_password(password),
        role="user",
        status="active",
    )
    r = await c.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    return user.id


# ── CRUD round-trip ───────────────────────────────────────────────────


async def test_crud_roundtrip(app_client: AsyncClient) -> None:
    await _create_and_login(app_client, "alice", "secret123")

    # Empty to start.
    g0 = await app_client.get("/api/quest-templates")
    assert g0.status_code == 200
    assert g0.json()["count"] == 0

    # Create one.
    r = await app_client.post(
        "/api/quest-templates",
        json={"text": "rescue the kitten"},
    )
    assert r.status_code == 201, r.text
    template_id = r.json()["template"]["id"]

    # Visible in list.
    g1 = await app_client.get("/api/quest-templates")
    assert g1.json()["count"] == 1
    assert g1.json()["templates"][0]["text"] == "rescue the kitten"
    assert g1.json()["templates"][0]["id"] == template_id

    # Delete.
    d = await app_client.delete(f"/api/quest-templates/{template_id}")
    assert d.status_code == 200, d.text

    # Empty again.
    g2 = await app_client.get("/api/quest-templates")
    assert g2.json()["count"] == 0


# ── Text length cap ───────────────────────────────────────────────────


async def test_create_rejects_overlong_text(app_client: AsyncClient) -> None:
    await _create_and_login(app_client, "bob", "secret123")

    overlong = "x" * (MAX_TEXT_LEN + 1)
    r = await app_client.post("/api/quest-templates", json={"text": overlong})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "template_text_too_long"


async def test_create_rejects_empty_text(app_client: AsyncClient) -> None:
    await _create_and_login(app_client, "carol", "secret123")
    r = await app_client.post("/api/quest-templates", json={"text": "   "})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "template_text_empty"


# ── Owner scoping ─────────────────────────────────────────────────────


async def test_owner_scoping_isolates_users(app_client: AsyncClient) -> None:
    # User A creates a template.
    await _create_and_login(app_client, "alice", "secret123")
    r = await app_client.post(
        "/api/quest-templates",
        json={"text": "alice template"},
    )
    template_id = r.json()["template"]["id"]

    # Switch to user B.
    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()
    await _create_and_login(app_client, "bobby", "secret123")

    # B sees an empty list.
    g = await app_client.get("/api/quest-templates")
    assert g.json()["count"] == 0

    # B trying to delete A's template — 404 ``template_not_found``.
    d = await app_client.delete(f"/api/quest-templates/{template_id}")
    assert d.status_code == 404
    assert d.json()["detail"]["code"] == "template_not_found"


# ── Unauthenticated access ────────────────────────────────────────────


async def test_unauthenticated_post_returns_401(app_client: AsyncClient) -> None:
    r = await app_client.post("/api/quest-templates", json={"text": "anon attempt"})
    assert r.status_code == 401


async def test_unauthenticated_delete_returns_401(app_client: AsyncClient) -> None:
    r = await app_client.delete("/api/quest-templates/123")
    assert r.status_code == 401


async def test_unauthenticated_get_returns_401(app_client: AsyncClient) -> None:
    """The GET behaviour also requires an active user (require_active_user
    gates the whole router). Pin it so a future relaxation is explicit."""
    r = await app_client.get("/api/quest-templates")
    assert r.status_code == 401
