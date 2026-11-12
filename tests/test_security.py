"""Hardening: SSRF guard, auth rate limits, docs/metrics gating, headers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from autonoma import security
from autonoma.config import settings


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "session_secret", "x" * 32)
    monkeypatch.setattr(settings, "allow_private_llm_urls", None)


def _resolver(mapping: dict[str, list[str]]):
    async def fake_getaddrinfo(self: Any, host: str, port: int, **kw: Any):
        if host not in mapping:
            import socket

            raise socket.gaierror("no such host")
        return [(2, 1, 6, "", (ip, port)) for ip in mapping[host]]

    return fake_getaddrinfo


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:3479",
        "http://localhost:8000/v1",
        "http://10.0.0.5",
        "http://192.168.1.10:11434",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:8000",
        "http://[::ffff:127.0.0.1]",
        "http://internal.example",  # resolves to a private address
        "http://mixed.example",  # one public + one private record
        "http://nowhere.example",
        "ftp://example.com",
        "file:///etc/passwd",
        "http://user:pw@example.com",
        "not a url",
    ],
)
async def test_outbound_url_guard_refuses_internal_targets(
    url: str, prod: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setattr(
        asyncio.BaseEventLoop,
        "getaddrinfo",
        _resolver(
            {
                "127.0.0.1": ["127.0.0.1"],
                "localhost": ["127.0.0.1"],
                "10.0.0.5": ["10.0.0.5"],
                "192.168.1.10": ["192.168.1.10"],
                "169.254.169.254": ["169.254.169.254"],
                "::1": ["::1"],
                "::ffff:127.0.0.1": ["::ffff:127.0.0.1"],
                "internal.example": ["10.1.2.3"],
                "mixed.example": ["93.184.216.34", "10.1.2.3"],
                "example.com": ["93.184.216.34"],
            }
        ),
    )
    assert await security.check_outbound_url(url) is not None


async def test_outbound_url_guard_allows_public_and_dev_private(
    prod: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setattr(
        asyncio.BaseEventLoop, "getaddrinfo", _resolver({"llm.example.com": ["93.184.216.34"]})
    )
    assert await security.check_outbound_url("https://llm.example.com/v1") is None

    # Dev mode (no session secret) keeps local vLLM servers usable.
    monkeypatch.setattr(settings, "session_secret", "")
    assert await security.check_outbound_url("http://localhost:8000") is None
    # …and an explicit flag wins either way.
    monkeypatch.setattr(settings, "allow_private_llm_urls", False)
    assert await security.check_outbound_url("http://127.0.0.1:8000") is not None


def test_rate_limiter_window_and_bounds() -> None:
    rl = security.RateLimiter(3, window_s=60)
    assert [rl.hit("a", now=t) for t in (0, 1, 2)] == [0.0, 0.0, 0.0]
    assert rl.hit("a", now=3) == pytest.approx(57)
    assert rl.hit("b", now=3) == 0.0  # other keys unaffected
    assert rl.hit("a", now=61) == 0.0  # oldest hit aged out
    assert security.RateLimiter(0).hit("x") == 0.0  # disabled

    small = security.RateLimiter(1, window_s=60, max_keys=4)
    for i in range(50):
        small.hit(f"k{i}", now=float(i) / 100)
    assert len(small._hits) <= 4


def test_client_ip_prefers_forwarded_headers() -> None:
    from types import SimpleNamespace

    req = SimpleNamespace(
        headers={"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert security.client_ip(req) == "203.0.113.9"
    req.headers = {"x-forwarded-for": "198.51.100.1, 10.0.0.1"}
    assert security.client_ip(req) == "198.51.100.1"
    req.headers = {}
    assert security.client_ip(req) == "127.0.0.1"


@pytest.fixture
async def auth_client(fresh_db, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from autonoma.db.engine import init_db
    from autonoma.routers import auth as auth_router

    await init_db()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3)
    auth_router._ip_limiter.reset()
    auth_router._user_limiter.reset()
    app = FastAPI()
    app.include_router(auth_router.router)
    app.add_middleware(security.SecurityHeadersMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    auth_router._ip_limiter.reset()
    auth_router._user_limiter.reset()


async def test_login_is_rate_limited_per_ip_and_per_username(auth_client: AsyncClient) -> None:
    body = {"username": "admin", "password": "wrong-guess"}
    ip_a = {"cf-connecting-ip": "203.0.113.1"}
    for _ in range(3):
        assert (
            await auth_client.post("/api/auth/login", json=body, headers=ip_a)
        ).status_code == 401
    r = await auth_client.post("/api/auth/login", json=body, headers=ip_a)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    # A different IP guessing the same account is throttled by username.
    r = await auth_client.post(
        "/api/auth/login", json=body, headers={"cf-connecting-ip": "203.0.113.2"}
    )
    assert r.status_code == 429
    # Another account from a fresh IP is unaffected.
    r = await auth_client.post(
        "/api/auth/login",
        json={"username": "someone", "password": "wrong-guess"},
        headers={"cf-connecting-ip": "203.0.113.3"},
    )
    assert r.status_code == 401


async def test_signup_is_rate_limited_and_requires_8_chars(auth_client: AsyncClient) -> None:
    h = {"cf-connecting-ip": "203.0.113.7"}
    r = await auth_client.post(
        "/api/auth/signup", json={"username": "shorty", "password": "1234567"}, headers=h
    )
    assert r.status_code == 400 and r.json()["detail"] == "invalid_password"
    for i in range(2):
        r = await auth_client.post(
            "/api/auth/signup", json={"username": f"user{i}", "password": "longenough1"}, headers=h
        )
        assert r.status_code == 201
    r = await auth_client.post(
        "/api/auth/signup", json={"username": "user9", "password": "longenough1"}, headers=h
    )
    assert r.status_code == 429


async def test_guest_refuses_internal_vllm_url_without_fetching(
    auth_client: AsyncClient, prod: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.routers import auth as auth_router

    called = False

    async def never(**kw: Any):
        nonlocal called
        called = True
        return True, None

    monkeypatch.setattr(auth_router, "_validate_guest_credentials", never)
    r = await auth_client.post(
        "/api/auth/guest",
        json={"provider": "vllm", "model": "m", "base_url": "http://127.0.0.1:3479"},
    )
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "invalid_input"
    assert called is False

    monkeypatch.setattr(settings, "guest_mode", "off")
    r = await auth_client.post(
        "/api/auth/guest", json={"provider": "anthropic", "model": "m", "api_key": "k"}
    )
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "guest_disabled"


async def test_security_headers_present(auth_client: AsyncClient, prod: None) -> None:
    r = await auth_client.get("/api/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "max-age" in r.headers["strict-transport-security"]


async def test_metrics_needs_bearer_token_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.routers import metrics as metrics_router

    app = FastAPI()
    app.include_router(metrics_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        monkeypatch.setattr(settings, "session_secret", "")
        assert (await c.get("/metrics")).status_code == 200  # dev: open

        monkeypatch.setattr(settings, "session_secret", "x" * 32)
        monkeypatch.setattr(settings, "metrics_token", "")
        assert (await c.get("/metrics")).status_code == 404  # no token configured
        monkeypatch.setattr(settings, "metrics_token", "s3cret")
        assert (await c.get("/metrics")).status_code == 404
        bad = {"authorization": "Bearer nope"}
        assert (await c.get("/metrics", headers=bad)).status_code == 404
        ok = {"authorization": "Bearer s3cret"}
        assert (await c.get("/metrics", headers=ok)).status_code == 200


def test_docs_are_off_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma import api

    monkeypatch.setattr(settings, "api_docs_enabled", None)
    monkeypatch.setattr(settings, "session_secret", "")
    assert api._docs_enabled() is True
    monkeypatch.setattr(settings, "session_secret", "x" * 32)
    assert api._docs_enabled() is False
    monkeypatch.setattr(settings, "api_docs_enabled", True)
    assert api._docs_enabled() is True
