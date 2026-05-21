"""In-process per-key sliding-window rate limiter.

Lightweight, dependency-free helper so public endpoints (e.g. the
anonymous ``/api/translate`` used by ``/watch/[code]``) can refuse
abuse without dragging in slowapi / fastapi-limiter / Redis.

Semantics
---------
Sliding window: each call records a monotonic timestamp in a per-key
``deque``; ``check_and_consume`` returns ``True`` iff fewer than
``limit`` timestamps remain in the trailing ``window_seconds`` interval
(and only then appends a new one — non-consuming peeks use
``would_allow``).

This is "good enough" for MVP single-process FastAPI deployments. If
we ever go multi-process we'll swap to Redis behind the same surface.

IP resolution
-------------
``client_ip(request)`` returns ``X-Forwarded-For``'s first hop when a
reverse-proxy header is present (Render/Fly/Cloudflare all set it),
falling back to ``request.client.host`` and ultimately ``"unknown"``.
We keep the helper here so multiple routers share the same ip
canonicalisation — duplicating ``request.client.host`` everywhere
silently disagrees on proxy setups.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque

from fastapi import Request


def client_ip(request: Request) -> str:
    """Best-effort client IP, honouring ``X-Forwarded-For`` if present.

    Only the *first* hop is trusted — XFF is a comma-separated chain
    appended by each proxy, and the leftmost entry is the original
    client (subject to proxy spoof-protection upstream). If neither
    XFF nor ``request.client`` is available we return ``"unknown"`` so
    the limiter still gets a consistent string key.
    """
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


class SlidingWindowLimiter:
    """Per-key sliding-window limiter.

    Backed by a ``dict[key, deque[monotonic_timestamps]]`` guarded by a
    ``threading.Lock`` (so it's safe from both async and sync code
    paths — FastAPI routes can be either). Old timestamps are trimmed
    lazily on every touch so the memory footprint stays bounded at
    O(num_active_keys × limit).
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.limit = int(limit)
        self.window = float(window_seconds)
        self._hits: dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, dq: Deque[float], now: float) -> None:
        cutoff = now - self.window
        while dq and dq[0] < cutoff:
            dq.popleft()

    def check_and_consume(self, key: str) -> bool:
        """Atomically check budget and, if available, record a hit.

        Returns ``True`` when the caller is under budget (and the hit
        has been recorded), ``False`` when the budget is exhausted.
        Callers that want to serve from cache *without* spending
        budget should call this only after the cache miss.
        """
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            self._trim(dq, now)
            if len(dq) >= self.limit:
                return False
            dq.append(now)
            return True

    def would_allow(self, key: str) -> bool:
        """Non-consuming peek — useful for diagnostics / tests."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                return True
            self._trim(dq, now)
            return len(dq) < self.limit

    def reset(self, key: str | None = None) -> None:
        """Drop tracked state for ``key`` (or everything when ``None``).

        Primarily a test hook — production code should never need to
        manually clear a window mid-flight.
        """
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
