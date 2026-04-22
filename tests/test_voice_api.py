"""HTTP-level tests for the /api/voice-profiles + /api/voice-bindings routes.

Exercises the FastAPI app via httpx ASGITransport like ``test_auth.py`` so
cookie/session semantics are real. The goals:

- Upload validation (size, duration, MIME sniff, declared-vs-actual mismatch)
- CRUD round-trip on profiles
- Binding CRUD + unique-per-VRM semantics
- Delete blocked while a binding still references the profile

Synthesis-path (/test) is stubbed: we monkeypatch ``get_shared_client`` so
the test doesn't load 2 GB of model weights.
"""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _signup_and_approve(client: AsyncClient, username: str, password: str) -> None:
    """Create a user and flip them to active via a direct DB write.

    The approval flow requires an admin cookie, which complicates every
    voice test. The voice surface only cares about "is the caller
    active" — we short-circuit the state machine here.
    """
    r = await client.post("/api/auth/signup", json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    from autonoma.db.users import get_user_by_username, update_user_status
    user = await get_user_by_username(username)
    assert user is not None
    await update_user_status(user.id, "active")


async def _login(client: AsyncClient, username: str, password: str) -> None:
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


def _make_wav(duration_s: float = 3.0, sample_rate: int = 24000) -> bytes:
    """Produce a silent PCM16 mono WAV of ``duration_s`` seconds."""
    frames = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


async def _authed(client: AsyncClient, username: str = "voicer") -> None:
    await _signup_and_approve(client, username, "password123")
    await _login(client, username, "password123")


# ── Upload validation ─────────────────────────────────────────────────


async def test_upload_rejects_non_audio_bytes(client: AsyncClient) -> None:
    await _authed(client)
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "alice", "ref_text": "hello"},
        files={"ref_audio": ("x.txt", b"not audio at all", "audio/wav")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unrecognized_audio"


async def test_upload_rejects_too_short_wav(client: AsyncClient) -> None:
    await _authed(client)
    wav = _make_wav(duration_s=0.3)
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "alice", "ref_text": "hi"},
        files={"ref_audio": ("x.wav", wav, "audio/wav")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "audio_too_short"


async def test_upload_rejects_too_long_wav(client: AsyncClient) -> None:
    await _authed(client)
    wav = _make_wav(duration_s=40.0)
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "alice", "ref_text": "hi"},
        files={"ref_audio": ("x.wav", wav, "audio/wav")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "audio_too_long"


async def test_upload_sniffs_mime_over_declared(client: AsyncClient) -> None:
    """Client lies about content-type as ``audio/wav`` but the bytes are
    actually an OGG — sniff wins and the stored MIME is ``audio/ogg``."""
    await _authed(client)
    ogg = b"OggS" + b"\x00" * 32  # minimal OggS header
    # We lie about the content type:
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "bob", "ref_text": "hello"},
        files={"ref_audio": ("x.wav", ogg, "audio/wav")},
    )
    assert r.status_code == 201, r.text
    profile = r.json()["profile"]
    assert profile["ref_audio_mime"] == "audio/ogg"


# ── CRUD round-trip ──────────────────────────────────────────────────


async def test_profile_list_create_get_delete(client: AsyncClient) -> None:
    await _authed(client)
    # empty list
    r = await client.get("/api/voice-profiles")
    assert r.status_code == 200
    assert r.json() == {"profiles": []}

    wav = _make_wav(duration_s=3.0)
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "alice", "ref_text": "안녕하세요"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["profile"]["id"]

    r = await client.get(f"/api/voice-profiles/{pid}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")
    assert r.content == wav

    r = await client.delete(f"/api/voice-profiles/{pid}")
    assert r.status_code == 204
    r = await client.get(f"/api/voice-profiles/{pid}/audio")
    assert r.status_code == 404


# ── Binding semantics ────────────────────────────────────────────────


async def test_binding_upsert_overwrites_same_vrm(client: AsyncClient) -> None:
    await _authed(client)
    wav = _make_wav(duration_s=2.0)

    async def make_profile(name: str) -> str:
        r = await client.post(
            "/api/voice-profiles",
            data={"name": name, "ref_text": "hi"},
            files={"ref_audio": ("a.wav", wav, "audio/wav")},
        )
        assert r.status_code == 201, r.text
        return r.json()["profile"]["id"]

    p1 = await make_profile("v1")
    p2 = await make_profile("v2")

    # First bind v1 to midori.vrm
    r = await client.put(
        "/api/voice-bindings",
        json={"vrm_file": "midori.vrm", "profile_id": p1},
    )
    assert r.status_code == 200
    # Now overwrite with v2 — same key should yield a single binding row
    r = await client.put(
        "/api/voice-bindings",
        json={"vrm_file": "midori.vrm", "profile_id": p2},
    )
    assert r.status_code == 200

    r = await client.get("/api/voice-bindings")
    bindings = r.json()["bindings"]
    test_vrm_rows = [b for b in bindings if b["vrm_file"] == "midori.vrm"]
    assert len(test_vrm_rows) == 1
    assert test_vrm_rows[0]["profile_id"] == p2


async def test_cannot_delete_profile_while_bound(client: AsyncClient) -> None:
    await _authed(client)
    wav = _make_wav(duration_s=2.0)
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "v", "ref_text": "hi"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )
    pid = r.json()["profile"]["id"]
    await client.put(
        "/api/voice-bindings",
        json={"vrm_file": "midori.vrm", "profile_id": pid},
    )
    r = await client.delete(f"/api/voice-profiles/{pid}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "profile_in_use"

    # Unbind, then delete succeeds
    r = await client.request(
        "DELETE", "/api/voice-bindings", params={"vrm_file": "midori.vrm"}
    )
    assert r.status_code == 204
    r = await client.delete(f"/api/voice-profiles/{pid}")
    assert r.status_code == 204


# ── /test endpoint with stubbed client ────────────────────────────────


async def test_synthesize_uses_structured_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the synth client raises TTSError(missing reference) the /test
    endpoint surfaces a structured 503 the UI can branch on."""
    # Skip if the optional tts extra isn't installed in this env.
    pytest.importorskip("numpy")

    from autonoma import tts_omnivoice
    from autonoma.tts_base import BaseTTSClient, TTSError

    class _Boom(BaseTTSClient):
        async def synthesize(self, **kwargs):
            raise TTSError("omnivoice: missing ref_audio or ref_text for voice")
            yield b""  # pragma: no cover

    monkeypatch.setattr(tts_omnivoice, "get_shared_client", lambda: _Boom())

    await _authed(client)
    wav = _make_wav(duration_s=2.0)
    r = await client.post(
        "/api/voice-profiles",
        data={"name": "v", "ref_text": "hi"},
        files={"ref_audio": ("a.wav", wav, "audio/wav")},
    )
    pid = r.json()["profile"]["id"]

    r = await client.post(
        f"/api/voice-profiles/{pid}/test", json={"text": "hello"}
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "missing_reference"
    assert "message" in detail
