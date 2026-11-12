"""Small security primitives shared by the HTTP and WebSocket layers.

- :func:`check_outbound_url` — SSRF guard for user-supplied URLs (the
  vLLM ``base_url`` a guest hands us is fetched from the server).
- :class:`RateLimiter` / :func:`client_ip` — sliding-window limits for
  the unauthenticated auth endpoints.
- :class:`SecurityHeadersMiddleware` — baseline response headers.

"Production" throughout means ``settings.session_secret`` is set — the
same signal the cookie flags and ``/api/debug/auth`` already use.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

from autonoma.config import settings


def is_production() -> bool:
    return bool(settings.session_secret)


# ── SSRF guard ────────────────────────────────────────────────────────


def _private_urls_allowed() -> bool:
    flag = settings.allow_private_llm_urls
    # Unset → allowed in dev (a local vLLM on localhost is the normal
    # case there), refused in production.
    return (not is_production()) if flag is None else bool(flag)


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def check_outbound_url(url: str) -> str | None:
    """Return ``None`` when the server may fetch ``url``, else a reason.

    Refuses non-http(s) schemes, embedded credentials, and — unless
    private URLs are allowed — any host that is, or resolves to, a
    loopback / private / link-local / reserved address. Every resolved
    address must be public, so a name with one public and one internal
    record is refused too. (A name that re-resolves differently between
    this check and the later request — DNS rebinding — is not covered;
    keep internal services authenticated.)
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "invalid URL"
    if parts.scheme not in ("http", "https"):
        return "URL must be http(s)"
    if parts.username or parts.password:
        return "URL must not contain credentials"
    host = parts.hostname
    if not host:
        return "URL has no host"
    if _private_urls_allowed():
        return None
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "invalid port"
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return "host does not resolve"
    addresses = {str(info[4][0]) for info in infos}
    if not addresses or not all(_is_public_ip(a) for a in addresses):
        return "URL points at a private or local address"
    return None


# ── Rate limiting ─────────────────────────────────────────────────────


def client_ip(request: Any) -> str:
    """Best-effort caller address.

    The API listens on loopback behind a tunnel, so the socket peer is
    always the proxy; the forwarded headers are the only signal. They
    are attacker-controlled when the API is reachable directly, which
    is why they are used for throttling only, never for authorization.
    """
    headers = request.headers
    forwarded = headers.get("cf-connecting-ip") or headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip()
    if ip:
        return ip[:64]
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or "unknown"


class RateLimiter:
    """In-memory sliding window: at most ``limit`` hits per ``window_s``
    for each key. Process-local, which matches the single-process
    deployment; it resets on restart."""

    def __init__(self, limit: int, window_s: float = 60.0, *, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window_s = window_s
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str, *, now: float | None = None) -> float:
        """Record one attempt. Returns ``0.0`` when allowed, else the
        seconds until the caller may try again."""
        if self.limit <= 0:
            return 0.0
        t = time.monotonic() if now is None else now
        q = self._hits.get(key)
        if q is None:
            if len(self._hits) >= self._max_keys:
                self._sweep(t)
            q = self._hits[key] = deque()
        cutoff = t - self.window_s
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.limit:
            return max(0.1, q[0] + self.window_s - t)
        q.append(t)
        return 0.0

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window_s
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[key]
        if len(self._hits) >= self._max_keys:
            # Still full of live keys (a flood of distinct sources):
            # drop the oldest half rather than grow without bound.
            for key in list(self._hits)[: self._max_keys // 2]:
                del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


# ── Response headers ──────────────────────────────────────────────────

_BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"cross-origin-resource-policy", b"cross-origin"),
)
_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Adds baseline security headers to every HTTP response (pure ASGI,
    so streaming responses and WebSockets are untouched). HSTS is sent
    only in production, where the API is always behind TLS."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = list(_BASE_HEADERS)
        if is_production():
            extra.append(_HSTS)

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in extra if h[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
