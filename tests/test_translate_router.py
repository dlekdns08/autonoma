"""``/api/translate`` — anonymous public access + per-IP rate limit.

These tests pin the public-endpoint contract used by ``/watch/[code]``:

* Anonymous (no cookie) requests are accepted.
* Empty / whitespace-only text is rejected with 400 ``empty_text``.
* Identical (text, lang) hits the LRU cache and does not consume
  rate-limit budget — important because subtitled streams repeat the
  same line constantly.
* Over-budget *unique* texts return 429.

The LLM is swapped for a deterministic counter-based stub so each
unique text produces a unique translation; this lets us tell cache
hits apart from fresh calls without depending on a real provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@dataclass
class _FakeResp:
    text: str
    input_tokens: int = 1
    output_tokens: int = 1
    stop_reason: str = "end_turn"


class _CountingLLM:
    """Returns a fresh translation per call so we can detect cache hits."""

    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **_kwargs: Any) -> _FakeResp:
        self.calls += 1
        return _FakeResp(text=f"TRANSLATED #{self.calls}")


@pytest.fixture
async def client(
    fresh_db, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, _CountingLLM]]:
    """ASGI client + the LLM spy bound to the translate router."""
    from autonoma.routers import translate as translate_mod

    fake = _CountingLLM()
    monkeypatch.setattr(translate_mod, "create_llm_client", lambda _cfg: fake)
    # Each test starts with a clean limiter + cache to keep ordering
    # assumptions explicit.
    translate_mod._translate_limiter.reset()
    translate_mod._cache.clear()

    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c, fake


async def test_anonymous_translate_returns_200(
    client: tuple[AsyncClient, _CountingLLM],
) -> None:
    """No auth cookie — should still succeed (it's a viewer endpoint)."""
    c, _fake = client
    r = await c.post(
        "/api/translate",
        json={"text": "안녕하세요", "from_lang": "ko", "to_lang": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "TRANSLATED #1"
    assert body["cached"] is False


async def test_empty_text_rejected(
    client: tuple[AsyncClient, _CountingLLM],
) -> None:
    c, _fake = client
    r = await c.post("/api/translate", json={"text": "   ", "to_lang": "en"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "empty_text"


async def test_cache_hit_returns_200_even_when_rate_limited(
    client: tuple[AsyncClient, _CountingLLM],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated identical text bypasses the limiter via the LRU."""
    c, fake = client
    from autonoma.routers import translate as translate_mod

    # First call: warms the cache and spends one budget unit.
    r1 = await c.post(
        "/api/translate",
        json={"text": "thanks", "from_lang": "en", "to_lang": "ko"},
    )
    assert r1.status_code == 200
    assert r1.json()["cached"] is False
    assert fake.calls == 1

    # Drain the budget for this client IP by simulating it as fully
    # consumed — easier than firing 30 unique calls and just as
    # accurate, since we already test the over-budget path separately.
    limiter = translate_mod._translate_limiter
    for _ in range(limiter.limit):
        limiter.check_and_consume("127.0.0.1")
    assert limiter.would_allow("127.0.0.1") is False

    # Second identical call must still succeed (served from cache).
    r2 = await c.post(
        "/api/translate",
        json={"text": "thanks", "from_lang": "en", "to_lang": "ko"},
    )
    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    # And it must NOT have hit the LLM again.
    assert fake.calls == 1


async def test_unique_requests_get_rate_limited(
    client: tuple[AsyncClient, _CountingLLM],
) -> None:
    """50 unique texts from the same IP in <60s → some 429s."""
    c, _fake = client
    from autonoma.routers import translate as translate_mod

    limit = translate_mod._TRANSLATE_LIMIT
    overshoot = 20

    statuses: list[int] = []
    for i in range(limit + overshoot):
        r = await c.post(
            "/api/translate",
            json={"text": f"phrase number {i}", "from_lang": "en", "to_lang": "ko"},
        )
        statuses.append(r.status_code)

    ok = sum(1 for s in statuses if s == 200)
    too_many = sum(1 for s in statuses if s == 429)
    # The first ``limit`` should succeed, the rest should be rejected.
    # Allow slack only on the exact split — what matters is "some
    # succeed, the over-budget ones get 429, nothing else slips in".
    assert ok == limit, statuses
    assert too_many == overshoot, statuses
    assert ok + too_many == len(statuses)
