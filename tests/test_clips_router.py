"""Tests for the highlight-clip router (`/api/clips`).

Covers:

* Multipart upload happy path → 201 + ``{id, url}``; file exists on disk
  under ``data_dir/clips/{id}.{ext}``.
* 64 MB size cap returns 413 with the structured ``clip_too_large`` code.
* Mime whitelist: non-video mimes get a defensive fallback extension —
  upload still succeeds (the router doesn't reject non-video mimes
  outright; playback fails in the browser instead). We pin this
  behaviour so a future change is intentional.
* DB row vs. disk consistency: when the DB insert raises, the disk file
  is cleaned up (no orphan blobs).
* ``GET /api/clips/{id}`` returns the file with the right content-type
  and 404s on unknown ids.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from autonoma.auth import hash_password
from autonoma.config import settings
from autonoma.db.users import create_user

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
async def authed_client(fresh_db) -> AsyncIterator[tuple[AsyncClient, str]]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            user = await create_user(
                username="clipper",
                password_hash=hash_password("secret123"),
                role="user",
                status="active",
            )
            r = await c.post(
                "/api/auth/login",
                json={"username": "clipper", "password": "secret123"},
            )
            assert r.status_code == 200
            yield c, user.id


def _install_fake_session(session_id: int, owner_user_id: str) -> None:
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


# ── Upload happy path ─────────────────────────────────────────────────


async def test_upload_happy_path(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(1, user_id)

    payload = b"webm bytes" * 64
    r = await c.post(
        "/api/clips",
        files={"file": ("clip.webm", payload, "video/webm")},
        data={"session_id": "1", "duration_ms": "30000", "title": "nice"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body and "url" in body
    assert body["url"] == f"/api/clips/{body['id']}"

    # File should exist on disk under data_dir/clips/{id}.webm
    on_disk = settings.data_dir / "clips" / f"{body['id']}.webm"
    assert on_disk.exists()
    assert on_disk.read_bytes() == payload


# ── Size cap ───────────────────────────────────────────────────────────


async def test_upload_size_cap_returns_413(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(2, user_id)

    # 64 MB + 1 byte. Use a sparse-ish payload (we still allocate it).
    oversized = b"x" * (64 * 1024 * 1024 + 1)
    r = await c.post(
        "/api/clips",
        files={"file": ("big.webm", oversized, "video/webm")},
        data={"session_id": "2", "duration_ms": "30000"},
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "clip_too_large"


# ── Mime handling ──────────────────────────────────────────────────────


async def test_non_video_mime_falls_back_to_webm(
    authed_client: tuple[AsyncClient, str],
) -> None:
    """The router doesn't reject non-video mimes outright — it falls
    back to a default extension. Pin that contract so any tightening is
    intentional."""
    c, user_id = authed_client
    _install_fake_session(3, user_id)

    r = await c.post(
        "/api/clips",
        files={"file": ("oops.png", b"png data", "image/png")},
        data={"session_id": "3", "duration_ms": "1000"},
    )
    # Current behaviour: accepted, default extension.
    assert r.status_code == 201, r.text
    clip_id = r.json()["id"]
    assert (settings.data_dir / "clips" / f"{clip_id}.webm").exists()


# ── DB failure rolls back the file write ───────────────────────────────


async def test_db_failure_cleans_up_orphan_file(
    authed_client: tuple[AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the DB insert raises, the on-disk blob must not survive."""
    c, user_id = authed_client
    _install_fake_session(4, user_id)

    # List existing clip files first so we can compare after.
    clips_dir = settings.data_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    before = set(p.name for p in clips_dir.iterdir())

    # Force the engine to raise on .begin() — easiest way to break the
    # DB insert from inside the router without touching SQLAlchemy
    # internals. We patch only for this test.
    from autonoma.routers import clips as clips_router

    real_get_engine = clips_router.get_engine

    class _BoomEngine:
        def begin(self) -> Any:
            raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(clips_router, "get_engine", lambda: _BoomEngine())

    r = await c.post(
        "/api/clips",
        files={"file": ("clip.webm", b"webm bytes", "video/webm")},
        data={"session_id": "4", "duration_ms": "5000"},
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "db_failed"

    # Restore so any later assertions can read the DB.
    monkeypatch.setattr(clips_router, "get_engine", real_get_engine)

    # No new files should be in the clips dir.
    after = set(p.name for p in clips_dir.iterdir())
    assert after == before, f"orphan file(s) leaked after DB failure: {after - before}"


# ── GET endpoint ───────────────────────────────────────────────────────


async def test_get_clip_returns_file(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, user_id = authed_client
    _install_fake_session(5, user_id)

    payload = b"webm-content"
    upload = await c.post(
        "/api/clips",
        files={"file": ("clip.webm", payload, "video/webm")},
        data={"session_id": "5", "duration_ms": "12345"},
    )
    clip_id = upload.json()["id"]

    r = await c.get(f"/api/clips/{clip_id}")
    assert r.status_code == 200, r.text
    assert r.content == payload
    # Mime: declared was video/webm; FileResponse preserves it.
    assert r.headers["content-type"].startswith("video/webm")


async def test_get_clip_unknown_id_returns_404(
    authed_client: tuple[AsyncClient, str],
) -> None:
    c, _user_id = authed_client
    r = await c.get("/api/clips/no-such-id")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "clip_not_found"
