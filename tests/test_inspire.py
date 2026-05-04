"""Tests for the goal recommender — Feature #11.

These tests pin three behaviours of ``POST /api/inspire``:

1. The endpoint returns 503 when ``settings.inspire_enabled`` is False.
2. Happy path with a canned LLM bullet list parses into 5 suggestions
   each shaped ``{"text": ..., "effort": ...}``.
3. Calling without ``repo_url`` or ``file_tree`` returns 422.

The router is mounted on a throw-away ``FastAPI`` app so the suite
doesn't depend on the full DB-backed application boot. The auth
dependency is overridden to a stub user; we are not testing auth here.
The LLM client is monkeypatched so no network calls are made.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


# ── Test doubles ──────────────────────────────────────────────────────


class _StubUser:
    """Quacks like ``autonoma.auth.User`` for the purposes of the dep."""

    id = "test-user-id"
    username = "tester"
    status = "active"
    role = "user"


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    """Tiny app with just the inspire router mounted."""
    from autonoma.auth import require_active_user
    from autonoma.routers import inspire as inspire_mod

    a = FastAPI()
    a.include_router(inspire_mod.router)
    a.dependency_overrides[require_active_user] = lambda: _StubUser()
    return a


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_inspire_cache() -> None:
    from autonoma.routers import inspire as inspire_mod
    inspire_mod._cache.clear()


# ── Tests ─────────────────────────────────────────────────────────────


async def test_inspire_503_when_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "inspire_enabled", False)

    r = await client.post(
        "/api/inspire",
        json={"file_tree": "src/foo.py\nsrc/bar.py"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "inspire_disabled"


async def test_inspire_happy_path_returns_five_suggestions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    from autonoma.routers import inspire as inspire_mod

    monkeypatch.setattr(settings, "inspire_enabled", True)

    canned = (
        "- Add structured logging to the request middleware (small)\n"
        "- Wire up Prometheus metrics for the inspire cache (medium)\n"
        "- Refactor the persona registry into its own module (medium)\n"
        "- Add an end-to-end test for the cutscene loop (large)\n"
        "- Document the swarm round protocol in docs/swarm.md (small)\n"
    )

    async def _fake_call_llm(context: str, focus: str | None) -> str:
        # Sanity-check the prompt assembly: the file_tree we sent is
        # passed through to the LLM via ``context``, and the focus tag
        # is forwarded.
        assert "src/autonoma/api.py" in context
        assert focus == "feature"
        return canned

    monkeypatch.setattr(inspire_mod, "_call_llm", _fake_call_llm)

    r = await client.post(
        "/api/inspire",
        json={
            "file_tree": "src/autonoma/api.py\nsrc/autonoma/llm.py",
            "focus": "feature",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "suggestions" in body
    sugg = body["suggestions"]
    assert len(sugg) == 5
    for row in sugg:
        assert set(row.keys()) == {"text", "effort"}
        assert row["effort"] in ("small", "medium", "large")
        assert row["text"]
        # Effort tag should have been stripped from the displayed text.
        assert row["effort"] not in row["text"].lower().split()

    # Confirm the parsed efforts match the canned input order.
    assert [r["effort"] for r in sugg] == [
        "small", "medium", "medium", "large", "small",
    ]
    # Spot-check a parsed text.
    assert "structured logging" in sugg[0]["text"].lower()



async def test_inspire_handles_github_fetch_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the GitHub fetch raises (e.g. timeout), the route must:

    1. Return 502 (per ``_fetch_github_context``'s ``HTTPException``).
    2. Surface a documented error code (``github_fetch_failed`` /
       ``github_rate_limited``) in ``detail.code``.
    3. NOT leak the raw exception text into the response body. (If this
       assertion fails, that's a real bug — leave the test failing as a
       finding rather than weakening the assertion.)
    """
    import httpx as _httpx

    from autonoma.config import settings
    monkeypatch.setattr(settings, "inspire_enabled", True)

    async def _boom(self, url, *args, **kwargs):
        raise _httpx.TimeoutException("upstream slow")

    # Patch the AsyncClient.get used by ``_fetch_github_context``.
    monkeypatch.setattr(_httpx.AsyncClient, "get", _boom)

    r = await client.post(
        "/api/inspire",
        json={"repo_url": "https://github.com/example/repo"},
    )

    assert r.status_code == 502, r.text
    body = r.json()
    code = body["detail"]["code"]
    # Either of these is "documented" per the route's contract.
    assert code in {"github_fetch_failed", "github_rate_limited"}, (
        f"unexpected error code {code!r}"
    )

    # Raw exception text MUST NOT leak. If this fails, the bug is in
    # ``routers/inspire.py::_fetch_github_context`` — it currently
    # f-string-formats the exception into the user-facing message.
    # TODO(human-triage): leak of raw exception text into response.
    assert "upstream slow" not in r.text, (
        "raw exception text leaked into response body — see "
        "routers/inspire.py::_fetch_github_context"
    )


async def test_inspire_422_without_context(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "inspire_enabled", True)

    r = await client.post("/api/inspire", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_context"
