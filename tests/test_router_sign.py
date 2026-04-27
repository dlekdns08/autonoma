"""Happy-path coverage for ``autonoma.routers.sign``.

The companion ``test_sign_translator.py`` already covers translate /
upload edge cases; this file pins the simpler list / play endpoints
plus a smoke round-trip so a future refactor of the router stays honest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(
    fresh_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _login(client: AsyncClient, username: str = "signrouter") -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"username": username, "password": "password123"},
    )
    assert r.status_code == 201, r.text
    from autonoma.db.users import get_user_by_username, update_user_status
    user = await get_user_by_username(username)
    assert user is not None
    await update_user_status(user.id, "active")
    r = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert r.status_code == 200


async def _upload(client: AsyncClient, name: str, tokens: list[str]) -> None:
    r = await client.post(
        "/api/sign/clips/upload",
        json={
            "name": name,
            "tokens": tokens,
            "duration_ms": 600,
            "frames": [{"t_ms": 0, "bones": []}, {"t_ms": 300, "bones": []}],
        },
    )
    assert r.status_code == 201, r.text


async def test_list_clips_starts_empty(client: AsyncClient) -> None:
    await _login(client)
    r = await client.get("/api/sign/clips")
    assert r.status_code == 200
    assert r.json() == {"clips": []}


async def test_list_clips_after_upload(client: AsyncClient) -> None:
    await _login(client)
    await _upload(client, "wave", ["손 흔들기"])

    r = await client.get("/api/sign/clips")
    assert r.status_code == 200
    body = r.json()
    assert len(body["clips"]) == 1
    clip = body["clips"][0]
    assert clip["name"] == "wave"
    assert clip["frames"] == 2
    assert clip["duration_ms"] == 600
    assert clip["language"] == "ksl"


async def test_play_unknown_clip_returns_404(client: AsyncClient) -> None:
    await _login(client)
    r = await client.post("/api/sign/play", json={"clip": "ghost"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "clip_not_found"


async def test_play_emits_sign_clip_event(client: AsyncClient) -> None:
    await _login(client)
    await _upload(client, "hi", ["hi"])

    from autonoma.event_bus import bus
    received: list[dict] = []

    async def _on(**kwargs):
        received.append(kwargs)

    bus.on("sign.clip", _on)
    try:
        r = await client.post(
            "/api/sign/play",
            json={"clip": "hi", "vrm_file": "midori.vrm"},
        )
    finally:
        bus.off("sign.clip", _on)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert len(received) == 1
    assert received[0]["name"] == "hi"
    assert received[0]["vrm_file"] == "midori.vrm"


async def test_translate_empty_text_rejected(client: AsyncClient) -> None:
    await _login(client)
    r = await client.post("/api/sign/translate", json={"text": "   "})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "empty_text"


async def test_translate_round_trip(client: AsyncClient) -> None:
    await _login(client)
    await _upload(client, "thanks", ["감사합니다"])

    r = await client.post("/api/sign/translate", json={"text": "감사합니다"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokens"] == ["감사합니다"]
    assert body["plan"] and body["plan"][0]["clip"] == "thanks"
    assert body["coverage"] == 1.0
    assert body["missing"] == []
