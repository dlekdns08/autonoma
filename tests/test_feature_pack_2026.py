"""Smoke tests for the 2026 feature pack.

One test per new router: we verify the feature flag gate, the auth
gate where applicable, and one happy-path round-trip. Deep behavior
tests (e.g. actual LLM vision call, Slack event parsing end-to-end)
belong in per-feature modules.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _authed(client: AsyncClient, username: str = "pack") -> None:
    r = await client.post("/api/auth/signup", json={"username": username, "password": "password123"})
    assert r.status_code == 201, r.text
    from autonoma.db.users import get_user_by_username, update_user_status
    user = await get_user_by_username(username)
    assert user is not None
    await update_user_status(user.id, "active")
    r = await client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert r.status_code == 200


# ── #1/#2 Live ────────────────────────────────────────────────────────


async def test_live_requires_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "live_webhook_secret", "")
    r = await client.post("/api/live/chat", json={"text": "hello"})
    assert r.status_code == 503  # disabled when secret is empty


async def test_live_chat_happy_path(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "live_webhook_secret", "topsecret")
    r = await client.post(
        "/api/live/chat",
        headers={"X-Autonoma-Signature": "topsecret"},
        json={"source": "twitch", "username": "v", "text": "alice fix this"},
    )
    assert r.status_code == 200, r.text


async def test_live_donation_bad_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "live_webhook_secret", "s")
    r = await client.post(
        "/api/live/donation",
        headers={"X-Autonoma-Signature": "wrong"},
        json={"amount_cents": 1000},
    )
    assert r.status_code == 401


# ── #3 Vision ─────────────────────────────────────────────────────────


async def test_vision_disabled_by_default(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.post(
        "/api/vision/observe",
        files={"frame": ("x.jpg", b"\xff\xd8\xff\xe0fake", "image/jpeg")},
        data={"hint": "debugging"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "vision_disabled"


# ── #5 Agent profile ──────────────────────────────────────────────────


async def test_agent_profile_404_for_unknown(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.get("/api/agents/doesnotexist/profile")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "agent_not_found"


# ── #6 Personas ───────────────────────────────────────────────────────


async def test_persona_crud_roundtrip(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.get("/api/personas")
    assert r.status_code == 200
    assert r.json()["personas"] == []

    r = await client.post("/api/personas", json={
        "name": "Midori",
        "seed_string": "coder:midori:v1",
        "role": "coder",
        "tags": ["fox", "calm"],
    })
    assert r.status_code == 201, r.text
    persona = r.json()["persona"]
    assert persona["name"] == "Midori"
    assert persona["is_public"] is False

    # Publish, then import creates a SECOND row under same owner.
    pid = persona["id"]
    r = await client.post(f"/api/personas/{pid}/publish", json={"is_public": True})
    assert r.status_code == 200

    r = await client.get("/api/personas/public")
    assert r.status_code == 200
    public = r.json()["personas"]
    assert any(p["id"] == pid for p in public)

    bundle = next(p for p in public if p["id"] == pid)
    r = await client.post("/api/personas/import", json=bundle)
    assert r.status_code == 201
    imported = r.json()["persona"]
    assert imported["id"] != pid
    assert imported["is_public"] is False  # imports start private


# ── #7 Battle ─────────────────────────────────────────────────────────


async def test_battle_invite_and_self_accept_rejected(client: AsyncClient) -> None:
    await _authed(client, "alice")
    r = await client.post("/api/battle/invite", json={"task_goal": "write fizzbuzz"})
    assert r.status_code == 201
    invite_id = r.json()["invite"]["id"]

    r = await client.post("/api/battle/accept", json={"invite_id": invite_id})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "cannot_self_accept"


# ── #8 Bridges ────────────────────────────────────────────────────────


async def test_slack_bridge_disabled_without_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "slack_signing_secret", "")
    r = await client.post("/api/bridges/slack/events", json={})
    assert r.status_code == 503


async def test_slack_bridge_verifies_signature(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "slack_signing_secret", "shh")

    body = b'{"event":{"type":"app_mention","text":"@alice fix this"},"type":"event_callback"}'
    ts = str(int(time.time()))
    basestring = b"v0:" + ts.encode() + b":" + body
    sig = "v0=" + hmac.new(b"shh", basestring, hashlib.sha256).hexdigest()

    r = await client.post(
        "/api/bridges/slack/events",
        content=body,
        headers={
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200, r.text


async def test_discord_bridge_shared_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "discord_webhook_secret", "disc")
    r = await client.post(
        "/api/bridges/discord/webhook",
        headers={"X-Autonoma-Signature": "disc"},
        json={"channel_id": "c", "user": "u", "text": "@bear please test"},
    )
    assert r.status_code == 200


# ── #10 Standup ───────────────────────────────────────────────────────


async def test_standup_disabled_by_default(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.post("/api/standup/generate", json={"lines": [{"agent": "a", "text": "hi"}]})
    assert r.status_code == 503


async def test_standup_produces_wav(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "standup_enabled", True)
    monkeypatch.setattr(settings, "standup_output_dir", tmp_path / "standups")
    await _authed(client)

    r = await client.post("/api/standup/generate", json={
        "title": "test-standup",
        "lines": [
            {"agent": "Alice", "text": "good morning"},
            {"agent": "Bear", "text": "ready for standup"},
        ],
    })
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["lines"] == 2
    assert Path(info["audio_path"]).exists()
    assert Path(info["transcript_path"]).exists()


# ── #11 Playback ──────────────────────────────────────────────────────


async def test_playback_empty_session(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.get("/api/playback/99999/frames")
    assert r.status_code == 200
    assert r.json()["frames"] == []


# ── #12 Sign clips ────────────────────────────────────────────────────


async def test_sign_clip_missing(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.post("/api/sign/play", json={"clip": "nope"})
    assert r.status_code == 404
