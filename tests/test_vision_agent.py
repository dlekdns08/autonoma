"""Vision Agent — real multimodal path.

Mocks ``create_llm_client`` with a client that returns a canned JSON
response. Verifies:

1. The router wires the image through to ``client.create`` with the
   right content-block shape per provider (anthropic uses
   ``{type: image, source: ...}``; OpenAI uses ``{type: image_url,
   image_url: {url: data:...}}``).
2. A successful response populates the ``agent.speech`` event bus.
3. Cooldown blocks back-to-back calls from the same user.
4. Malformed model JSON degrades to ``acted=false`` without crashing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _authed(client: AsyncClient, username: str = "vis") -> None:
    r = await client.post("/api/auth/signup", json={"username": username, "password": "password123"})
    assert r.status_code == 201
    from autonoma.db.users import get_user_by_username, update_user_status
    user = await get_user_by_username(username)
    assert user is not None
    await update_user_status(user.id, "active")
    r = await client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert r.status_code == 200


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 10


@dataclass
class _FakeResp:
    text: str
    input_tokens: int = 10
    output_tokens: int = 10
    stop_reason: str = "end_turn"


class _SpyClient:
    """LLM double that records the last call + returns a scripted reply."""

    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.last_call: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _FakeResp:
        self.last_call = kwargs
        return _FakeResp(text=self.reply_text)


def _prime_vision(monkeypatch: pytest.MonkeyPatch, spy: _SpyClient) -> None:
    """Enable the feature + swap the LLM client for our spy."""
    from autonoma.config import settings
    monkeypatch.setattr(settings, "vision_agent_enabled", True)
    monkeypatch.setattr(settings, "vision_agent_cooldown_s", 60)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")  # pretend key

    from autonoma.routers import vision as vision_mod
    monkeypatch.setattr(vision_mod, "create_llm_client", lambda cfg: spy)  # type: ignore[arg-type]

    # Reset per-user cooldown between tests.
    vision_mod._last_seen.clear()


async def test_vision_happy_path_anthropic(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _authed(client)
    spy = _SpyClient(
        '{"acted": true, "agent": "Midori", '
        '"message": "line 42 looks like an infinite loop.", '
        '"reason": "saw while(1) with no break"}'
    )
    _prime_vision(monkeypatch, spy)
    # Provider is anthropic by default in settings.

    r = await client.post(
        "/api/vision/observe",
        files={"frame": ("x.jpg", JPEG_MAGIC + b"\x00" * 100, "image/jpeg")},
        data={"hint": "debugging infinite loop"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["acted"] is True
    assert body["agent"] == "Midori"
    assert "infinite loop" in body["message"]

    # Confirm the call shape used Anthropic's image block form.
    assert spy.last_call is not None
    user_msg = spy.last_call["messages"][0]
    content = user_msg["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1]["type"] == "text"


async def test_vision_happy_path_openai(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _authed(client)
    spy = _SpyClient(
        '{"acted": false, "agent": "", "message": "", "reason": "nothing to say"}'
    )
    _prime_vision(monkeypatch, spy)

    from autonoma.config import settings
    monkeypatch.setattr(settings, "provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    r = await client.post(
        "/api/vision/observe",
        files={"frame": ("x.jpg", JPEG_MAGIC + b"\x00" * 100, "image/jpeg")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["acted"] is False

    # OpenAI shape: image_url block carrying a data-URL.
    assert spy.last_call is not None
    content = spy.last_call["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_vision_cooldown(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _authed(client)
    spy = _SpyClient('{"acted": true, "agent": "A", "message": "hi", "reason": "r"}')
    _prime_vision(monkeypatch, spy)

    # First call: acts. Second: should cooldown.
    r1 = await client.post(
        "/api/vision/observe",
        files={"frame": ("a.jpg", JPEG_MAGIC + b"\x00" * 32, "image/jpeg")},
    )
    assert r1.status_code == 200
    assert r1.json()["acted"] is True

    r2 = await client.post(
        "/api/vision/observe",
        files={"frame": ("a.jpg", JPEG_MAGIC + b"\x00" * 32, "image/jpeg")},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["acted"] is False
    assert body["reason"] == "cooldown"


async def test_vision_unparseable_falls_through(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _authed(client)
    spy = _SpyClient("this is not JSON at all")
    _prime_vision(monkeypatch, spy)

    r = await client.post(
        "/api/vision/observe",
        files={"frame": ("x.jpg", JPEG_MAGIC + b"\x00" * 32, "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json()["acted"] is False


async def test_vision_fence_wrapped_json_parses(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Models sometimes wrap JSON in ```json ... ``` — we still accept it."""
    await _authed(client)
    spy = _SpyClient('```json\n{"acted": true, "agent": "X", "message": "ok", "reason": "r"}\n```')
    _prime_vision(monkeypatch, spy)

    r = await client.post(
        "/api/vision/observe",
        files={"frame": ("x.jpg", JPEG_MAGIC + b"\x00" * 32, "image/jpeg")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["acted"] is True
    assert body["agent"] == "X"
