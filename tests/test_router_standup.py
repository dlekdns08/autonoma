"""Happy-path coverage for ``autonoma.routers.standup``.

The standup endpoint shells out to TTS when ``tts_provider == omnivoice``.
Here we keep ``tts_provider == "none"`` (the default) so the router falls
through to the silence-WAV path — that lets us assert the wiring without
depending on torch / omnivoice.
"""

from __future__ import annotations

import wave
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


async def _login(client: AsyncClient, username: str = "standuprouter") -> None:
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


async def test_disabled_returns_503(client: AsyncClient) -> None:
    """With ``standup_enabled=False`` the endpoint should refuse to render."""
    await _login(client)
    r = await client.post(
        "/api/standup/generate",
        json={"lines": [{"agent": "Alice", "text": "hi"}]},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "standup_disabled"


async def test_enabled_happy_path_writes_files(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autonoma.config import settings

    monkeypatch.setattr(settings, "standup_enabled", True)
    monkeypatch.setattr(settings, "standup_output_dir", tmp_path / "out")
    await _login(client)

    r = await client.post(
        "/api/standup/generate",
        json={
            "title": "router-smoke",
            "lines": [
                {"agent": "Alice", "text": "good morning"},
                {"agent": "Bear", "text": "got the build green"},
                {"agent": "Cat", "text": ""},  # empty lines should be skipped
            ],
        },
    )
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["title"] == "router-smoke"
    # Cat's empty line is dropped, so we get 2 audio segments only.
    assert info["lines"] == 2

    audio_path = Path(info["audio_path"])
    transcript_path = Path(info["transcript_path"])
    assert audio_path.exists()
    assert transcript_path.exists()

    # Resulting WAV is well-formed PCM mono.
    with wave.open(str(audio_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2  # PCM16
        assert wf.getnframes() > 0

    transcript = transcript_path.read_text(encoding="utf-8")
    assert "router-smoke" in transcript
    assert "Alice" in transcript and "Bear" in transcript


async def test_empty_lines_array_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings

    monkeypatch.setattr(settings, "standup_enabled", True)
    await _login(client)

    r = await client.post("/api/standup/generate", json={"lines": []})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "empty_script"
