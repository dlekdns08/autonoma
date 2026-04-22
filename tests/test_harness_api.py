"""Tests for the /api/harness/presets REST endpoints.

Mirrors the ``tests/test_auth.py`` pattern: a scratch SQLite per test,
the real FastAPI app driven through ``httpx.AsyncClient + ASGITransport``,
and a lifespan context so the default-preset bootstrap hook runs.

We skip WS tests deliberately — the rest of the suite does not use
``websocket_connect`` anywhere (no test imports it), so adding a single
one here would introduce a new pattern for little payoff. The policy
resolution helper is exercised via its ValidationError branch by
posting an obviously-bad preset body through the REST layer, which
walks the same validator.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


# ── Fixtures ──────────────────────────────────────────────────────────
# ``fresh_db`` is shared across the DB-touching test suite (see
# tests/conftest.py). Only the HTTP client fixture is local to this file.


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _register_and_login(
    c: AsyncClient, username: str, password: str = "secret123"
) -> None:
    """Create an already-active user and log them in on ``c``."""
    from autonoma.auth import hash_password
    from autonoma.db.users import create_user

    await create_user(
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


def _default_content() -> dict:
    from autonoma.harness.policy import default_policy_content

    return default_policy_content().model_dump(mode="json")


# ── Happy path ────────────────────────────────────────────────────────


async def test_create_list_get_update_delete_round_trip(
    client: AsyncClient,
) -> None:
    await _register_and_login(client, "alice")

    body = {"name": "faster", "content": _default_content()}
    body["content"]["loop"]["max_rounds"] = 75
    created = await client.post("/api/harness/presets", json=body)
    assert created.status_code == 201, created.text
    created_json = created.json()
    preset_id = created_json["id"]
    assert created_json["name"] == "faster"
    assert created_json["content"]["loop"]["max_rounds"] == 75
    assert created_json["is_default"] is False

    # List — default preset and the new one both show up.
    listing = await client.get("/api/harness/presets")
    assert listing.status_code == 200
    names = [p["name"] for p in listing.json()["presets"]]
    assert "faster" in names
    assert "default" in names

    # Get by id
    got = await client.get(f"/api/harness/presets/{preset_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "faster"

    # Update name
    up = await client.put(
        f"/api/harness/presets/{preset_id}",
        json={"name": "renamed"},
    )
    assert up.status_code == 200
    assert up.json()["name"] == "renamed"

    # Delete
    rm = await client.delete(f"/api/harness/presets/{preset_id}")
    assert rm.status_code == 204
    gone = await client.get(f"/api/harness/presets/{preset_id}")
    assert gone.status_code == 404


# ── Auth + ownership ──────────────────────────────────────────────────


async def test_list_without_cookie_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/harness/presets")
    assert r.status_code == 401


async def test_create_without_cookie_returns_401(client: AsyncClient) -> None:
    r = await client.post(
        "/api/harness/presets",
        json={"name": "x", "content": _default_content()},
    )
    assert r.status_code == 401


async def test_user_b_cannot_read_user_a_preset(client: AsyncClient) -> None:
    await _register_and_login(client, "alice")
    body = {"name": "alice-only", "content": _default_content()}
    r = await client.post("/api/harness/presets", json=body)
    assert r.status_code == 201
    preset_id = r.json()["id"]

    await client.post("/api/auth/logout")
    client.cookies.clear()

    await _register_and_login(client, "bob")
    r2 = await client.get(f"/api/harness/presets/{preset_id}")
    assert r2.status_code == 403


async def test_user_b_cannot_update_or_delete_user_a_preset(
    client: AsyncClient,
) -> None:
    await _register_and_login(client, "alice")
    r = await client.post(
        "/api/harness/presets",
        json={"name": "alice-only", "content": _default_content()},
    )
    preset_id = r.json()["id"]

    await client.post("/api/auth/logout")
    client.cookies.clear()

    await _register_and_login(client, "bob")
    up = await client.put(
        f"/api/harness/presets/{preset_id}", json={"name": "hijack"}
    )
    assert up.status_code == 403
    rm = await client.delete(f"/api/harness/presets/{preset_id}")
    assert rm.status_code == 403


async def test_default_preset_cannot_be_updated_or_deleted(
    client: AsyncClient,
) -> None:
    await _register_and_login(client, "alice")
    # Locate the default via the listing — it's always present.
    listing = (await client.get("/api/harness/presets")).json()["presets"]
    default = next(p for p in listing if p["is_default"])
    did = default["id"]

    up = await client.put(f"/api/harness/presets/{did}", json={"name": "x"})
    assert up.status_code == 403
    rm = await client.delete(f"/api/harness/presets/{did}")
    assert rm.status_code == 403


# ── Validation (422) ──────────────────────────────────────────────────


async def test_bad_enum_value_is_422(client: AsyncClient) -> None:
    await _register_and_login(client, "alice")
    content = _default_content()
    content["routing"]["strategy"] = "nope"
    r = await client.post(
        "/api/harness/presets",
        json={"name": "bad-enum", "content": content},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, list)
    # Native FastAPI shape: every entry carries loc/msg/type.
    assert any("routing" in item["loc"] for item in detail)


async def test_bound_violation_is_422(client: AsyncClient) -> None:
    await _register_and_login(client, "alice")
    content = _default_content()
    content["loop"]["max_rounds"] = 5  # below ge=10
    r = await client.post(
        "/api/harness/presets",
        json={"name": "bad-bound", "content": content},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("loop" in item["loc"] for item in detail)


# ── Default-preset visibility ─────────────────────────────────────────


async def test_default_appears_in_list_for_every_user(
    client: AsyncClient,
) -> None:
    await _register_and_login(client, "alice")
    a_list = (await client.get("/api/harness/presets")).json()["presets"]
    assert any(p["is_default"] for p in a_list)

    await client.post("/api/auth/logout")
    client.cookies.clear()

    await _register_and_login(client, "bob")
    b_list = (await client.get("/api/harness/presets")).json()["presets"]
    assert any(p["is_default"] for p in b_list)


async def test_any_authenticated_user_can_get_default(
    client: AsyncClient,
) -> None:
    await _register_and_login(client, "alice")
    listing = (await client.get("/api/harness/presets")).json()["presets"]
    default = next(p for p in listing if p["is_default"])
    did = default["id"]

    # Switch to a fresh user — they still get 200.
    await client.post("/api/auth/logout")
    client.cookies.clear()
    await _register_and_login(client, "bob")
    r = await client.get(f"/api/harness/presets/{did}")
    assert r.status_code == 200
    assert r.json()["is_default"] is True


# ── Policy resolver (unit, not WS) ────────────────────────────────────
# We don't exercise the WS path (no websocket_connect tests exist in
# this suite) — instead we directly call the resolver that WS ``start``
# uses, which is the behavior we care about.


async def test_resolve_start_policy_defaults_without_preset(
    fresh_db,
) -> None:
    from autonoma.api import _resolve_start_policy

    content, err = await _resolve_start_policy(
        user_id=None, preset_id=None, overrides=None
    )
    assert err is None
    assert content is not None
    assert content.loop.max_rounds == 40


async def test_resolve_start_policy_rejects_unknown_preset(
    fresh_db,
) -> None:
    from autonoma.api import _resolve_start_policy

    content, err = await _resolve_start_policy(
        user_id=None, preset_id="does-not-exist", overrides=None
    )
    assert content is None
    assert err == "preset not accessible"


async def test_resolve_start_policy_rejects_bad_overrides(
    fresh_db,
) -> None:
    from autonoma.api import _resolve_start_policy

    # Invalid enum in the routing override should fail ValidationError
    # and yield "invalid policy content".
    content, err = await _resolve_start_policy(
        user_id=None,
        preset_id=None,
        overrides={"routing": {"strategy": "lottery"}},
    )
    assert content is None
    assert err == "invalid policy content"


async def test_resolve_start_policy_applies_per_section_override(
    fresh_db,
) -> None:
    from autonoma.api import _resolve_start_policy

    content, err = await _resolve_start_policy(
        user_id=None,
        preset_id=None,
        overrides={"routing": {"strategy": "round_robin"}},
    )
    assert err is None
    assert content is not None
    assert content.routing.strategy == "round_robin"


async def test_resolve_start_policy_forbids_other_users_preset(
    fresh_db,
) -> None:
    from autonoma.api import _resolve_start_policy
    from autonoma.auth import hash_password
    from autonoma.db.harness_policies import create_policy
    from autonoma.db.users import create_user
    from autonoma.harness.policy import default_policy_content

    alice = await create_user(
        username="alice_r",
        password_hash=hash_password("secret123"),
        role="user",
        status="active",
    )
    bob = await create_user(
        username="bob_r",
        password_hash=hash_password("secret123"),
        role="user",
        status="active",
    )
    preset = await create_policy(
        owner_user_id=alice.id,
        name="alice-only",
        content=default_policy_content(),
    )
    content, err = await _resolve_start_policy(
        user_id=bob.id, preset_id=preset.id, overrides=None
    )
    assert content is None
    assert err == "preset not accessible"
