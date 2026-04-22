"""Text → KSL phrase-book translator (feature #12)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(fresh_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    # Isolate ``data_dir`` so uploaded clips don't pollute the repo.
    from autonoma.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _auth(client: AsyncClient) -> None:
    r = await client.post("/api/auth/signup", json={"username": "signer", "password": "password123"})
    assert r.status_code == 201
    from autonoma.db.users import get_user_by_username, update_user_status
    user = await get_user_by_username("signer")
    assert user is not None
    await update_user_status(user.id, "active")
    r = await client.post("/api/auth/login", json={"username": "signer", "password": "password123"})
    assert r.status_code == 200


async def _upload_clip(client: AsyncClient, name: str, tokens: list[str]) -> None:
    r = await client.post("/api/sign/clips/upload", json={
        "name": name,
        "tokens": tokens,
        "duration_ms": 800,
        "frames": [{"t_ms": 0, "bones": []}],
    })
    assert r.status_code == 201, r.text


async def test_translate_hits_phrase_clip(client: AsyncClient) -> None:
    await _auth(client)
    await _upload_clip(client, "hello", ["안녕하세요", "hello"])
    r = await client.post("/api/sign/translate", json={"text": "안녕하세요"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokens"] == ["안녕하세요"]
    assert body["plan"][0]["kind"] == "word"
    assert body["plan"][0]["clip"] == "hello"
    assert body["missing"] == []
    assert body["coverage"] == 1.0


async def test_translate_prefers_longest_phrase(client: AsyncClient) -> None:
    await _auth(client)
    await _upload_clip(client, "greet", ["좋은 아침"])
    await _upload_clip(client, "morning", ["아침"])
    r = await client.post("/api/sign/translate", json={"text": "좋은 아침"})
    assert r.status_code == 200
    body = r.json()
    # Should pick the 2-word "좋은 아침" clip rather than fingerspelling
    # "좋은" + playing the "아침" single-word clip.
    assert body["plan"] == [
        {"kind": "phrase", "clip": "greet", "surface": "좋은 아침"},
    ]


async def test_translate_fingerspells_unknown(client: AsyncClient) -> None:
    await _auth(client)
    # Fingerspelling clips for just 2 letters of a 4-letter word
    await _upload_clip(client, "ksl_letter:x", ["ksl_letter:x"])
    await _upload_clip(client, "ksl_letter:y", ["ksl_letter:y"])
    r = await client.post("/api/sign/translate", json={"text": "xyzz"})
    assert r.status_code == 200
    body = r.json()
    # All 4 chars got fingerspell entries; two with clips, two empty.
    kinds = [s["kind"] for s in body["plan"]]
    assert kinds == ["fingerspell"] * 4
    clips = [s["clip"] for s in body["plan"]]
    assert clips[0] == "ksl_letter:x"
    assert clips[1] == "ksl_letter:y"
    assert clips[2] == "" and clips[3] == ""
    # Missing should list the surface chars without clips.
    assert body["missing"] == ["z", "z"]


async def test_translate_with_emit(client: AsyncClient) -> None:
    """``emit=true`` should publish a ``sign.sequence`` event."""
    await _auth(client)
    await _upload_clip(client, "hi", ["hi"])

    from autonoma.event_bus import bus
    received: list[dict] = []

    async def on_seq(**kwargs):
        received.append(kwargs)

    bus.on("sign.sequence", on_seq)
    try:
        r = await client.post("/api/sign/translate", json={
            "text": "hi",
            "vrm_file": "midori.vrm",
            "emit": True,
        })
    finally:
        bus.off("sign.sequence", on_seq)
    assert r.status_code == 200
    assert len(received) == 1
    assert received[0]["vrm_file"] == "midori.vrm"
    assert received[0]["plan"][0]["clip"] == "hi"


async def test_upload_rejects_bad_name(client: AsyncClient) -> None:
    await _auth(client)
    r = await client.post("/api/sign/clips/upload", json={
        "name": "../escape",
        "frames": [{"t_ms": 0}],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_clip_name"


async def test_upload_rejects_empty_frames(client: AsyncClient) -> None:
    await _auth(client)
    r = await client.post("/api/sign/clips/upload", json={
        "name": "ok_name",
        "frames": [],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_frames"
