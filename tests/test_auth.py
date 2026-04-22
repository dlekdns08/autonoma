"""Tests for the cookie-session authentication system.

Each test runs against a fresh, isolated SQLite file (via monkeypatched
``settings.data_dir``) so state from one test can't bleed into another.
We exercise the real FastAPI app through ``httpx.AsyncClient + ASGITransport``,
which gives us cookie round-trip semantics without spinning up a server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


# ── Fixtures ──────────────────────────────────────────────────────────
# ``fresh_db`` lives in tests/conftest.py so every DB-touching test uses
# the same scratch-DB recipe. Locally-scoped fixtures here are only for
# things specific to this file (the HTTP client).


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    """Build an AsyncClient wired to the real FastAPI app, with lifespan
    triggered so the admin-bootstrap hook runs."""
    # Defer import until AFTER ``fresh_db`` has patched settings, otherwise
    # the lifespan hook would initialize the engine against the stale
    # default data_dir.
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as c:
        # Manually drive lifespan startup so the bootstrap hook runs.
        async with app.router.lifespan_context(app):
            yield c


# ── Tests ─────────────────────────────────────────────────────────────


async def test_signup_creates_pending_user(client: AsyncClient) -> None:
    """A fresh signup goes into the ``pending`` bucket — never active."""
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "alice", "password": "secret123"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"status": "pending"}

    # The row exists with status=pending.
    from autonoma.db.users import get_user_by_username

    user = await get_user_by_username("alice")
    assert user is not None
    assert user.status == "pending"
    assert user.role == "user"


async def test_signup_rejects_duplicate_username(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "bob", "password": "secret123"},
    )
    assert resp.status_code == 201

    resp2 = await client.post(
        "/api/auth/signup",
        json={"username": "bob", "password": "another1"},
    )
    assert resp2.status_code == 409
    assert resp2.json()["detail"] == "username_taken"


async def test_signup_rejects_invalid_username(client: AsyncClient) -> None:
    # Too short (< 3 chars).
    resp = await client.post(
        "/api/auth/signup",
        json={"username": "ab", "password": "secret123"},
    )
    assert resp.status_code == 400

    # Illegal character ("!") that isn't in [a-z0-9_-].
    resp2 = await client.post(
        "/api/auth/signup",
        json={"username": "bad!name", "password": "secret123"},
    )
    assert resp2.status_code == 400


async def test_login_rejects_pending_user(client: AsyncClient) -> None:
    """A user who signed up but hasn't been approved cannot log in."""
    await client.post(
        "/api/auth/signup",
        json={"username": "carol", "password": "secret123"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"username": "carol", "password": "secret123"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_active"


async def test_login_rejects_bad_credentials(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "nosuch", "password": "whatever"},
    )
    assert resp.status_code == 401


async def test_approve_flips_pending_to_active_and_allows_login(
    client: AsyncClient,
) -> None:
    """Admin bootstrap + approval workflow end-to-end.

    1. An admin user must already exist (seeded via the bootstrap env
       mechanism below — we use the CRUD helper for test simplicity).
    2. Admin logs in, approves the pending user.
    3. The approved user can then log in.
    4. ``GET /api/auth/me`` round-trips the session cookie back to the
       right user.
    """
    # Create an admin directly (faster than driving the lifespan path).
    from autonoma.auth import hash_password
    from autonoma.db.users import create_user

    await create_user(
        username="admin",
        password_hash=hash_password("adminpw1"),
        role="admin",
        status="active",
    )

    # Signup a pending user.
    await client.post(
        "/api/auth/signup",
        json={"username": "dave", "password": "secret123"},
    )

    # Before approval: login must fail with 403.
    pre = await client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "secret123"},
    )
    assert pre.status_code == 403

    # Admin logs in.
    admin_login = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "adminpw1"},
    )
    assert admin_login.status_code == 200
    # Cookie stored on the client session automatically.
    assert "autonoma_session" in client.cookies

    # Admin lists users, finds the pending one.
    listing = await client.get("/api/admin/users")
    assert listing.status_code == 200
    dave_row = next(
        u for u in listing.json()["users"] if u["username"] == "dave"
    )
    assert dave_row["status"] == "pending"

    # Admin approves.
    approve = await client.post(f"/api/admin/users/{dave_row['id']}/approve")
    assert approve.status_code == 204

    # Admin logs out; switch to dave.
    await client.post("/api/auth/logout")
    client.cookies.clear()

    dave_login = await client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "secret123"},
    )
    assert dave_login.status_code == 200
    body = dave_login.json()
    assert body["user"]["username"] == "dave"
    assert body["user"]["status"] == "active"
    assert "password_hash" not in body["user"]

    # Cookie auth round-trip: /me returns the same user.
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "dave"


async def test_me_without_cookie_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_admin_endpoint_requires_admin(client: AsyncClient) -> None:
    """A plain active user must not be able to hit /api/admin/*."""
    from autonoma.auth import hash_password
    from autonoma.db.users import create_user

    await create_user(
        username="eve",
        password_hash=hash_password("secret123"),
        role="user",
        status="active",
    )

    await client.post(
        "/api/auth/login",
        json={"username": "eve", "password": "secret123"},
    )
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_required"


async def test_deny_moves_pending_to_disabled(client: AsyncClient) -> None:
    from autonoma.auth import hash_password
    from autonoma.db.users import create_user, get_user_by_username

    await create_user(
        username="admin",
        password_hash=hash_password("adminpw1"),
        role="admin",
        status="active",
    )
    await client.post(
        "/api/auth/signup",
        json={"username": "frank", "password": "secret123"},
    )
    await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "adminpw1"},
    )
    frank = await get_user_by_username("frank")
    assert frank is not None
    resp = await client.post(f"/api/admin/users/{frank.id}/deny")
    assert resp.status_code == 204
    frank_after = await get_user_by_username("frank")
    assert frank_after is not None
    assert frank_after.status == "disabled"


async def test_admin_bootstrap_on_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When AUTONOMA_ADMIN_PASSWORD is set and no admin row exists, the
    app's lifespan hook creates one with role=admin, status=active."""
    from autonoma import config as config_module
    from autonoma.db import engine as engine_module

    # Fresh DB for this test.
    monkeypatch.setattr(config_module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config_module.settings, "db_filename", "bootstrap.db")
    monkeypatch.setattr(config_module.settings, "admin_password", "bootstrap-pw")
    engine_module._engine = None
    engine_module._initialized = False

    from autonoma.api import app

    try:
        # Drive lifespan: startup triggers the bootstrap.
        async with app.router.lifespan_context(app):
            from autonoma.db.users import get_user_by_username

            admin = await get_user_by_username("admin")
            assert admin is not None
            assert admin.role == "admin"
            assert admin.status == "active"

            # The password it stored must verify.
            from autonoma.auth import verify_password

            assert verify_password("bootstrap-pw", admin.password_hash)
    finally:
        engine_module._engine = None
        engine_module._initialized = False


async def test_disable_and_reactivate_flow(client: AsyncClient) -> None:
    from autonoma.auth import hash_password
    from autonoma.db.users import create_user, get_user_by_username

    await create_user(
        username="admin",
        password_hash=hash_password("adminpw1"),
        role="admin",
        status="active",
    )
    await create_user(
        username="grace",
        password_hash=hash_password("secret123"),
        role="user",
        status="active",
    )
    await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "adminpw1"},
    )
    grace = await get_user_by_username("grace")
    assert grace is not None

    # Disable
    r = await client.post(f"/api/admin/users/{grace.id}/disable")
    assert r.status_code == 204
    after_disable = await get_user_by_username("grace")
    assert after_disable is not None and after_disable.status == "disabled"

    # Re-disabling (already disabled) conflicts.
    r2 = await client.post(f"/api/admin/users/{grace.id}/disable")
    assert r2.status_code == 409

    # Reactivate.
    r3 = await client.post(f"/api/admin/users/{grace.id}/reactivate")
    assert r3.status_code == 204
    after_reactivate = await get_user_by_username("grace")
    assert after_reactivate is not None and after_reactivate.status == "active"
