"""Tests for ``action.llm_error_handling`` strategies.

Monkey-patches ``asyncio.sleep`` so we can assert on the sleep duration
without actually waiting — the strategies' sleeps are pure side-effects
we need to probe, not behavior to wait through.
"""

from __future__ import annotations

import pytest

from autonoma.harness import llm_error_strategies  # noqa: F401 — registers
from autonoma.harness.strategies import is_stub, lookup
from autonoma.llm import LLMConnectionError, LLMRateLimitError


@pytest.fixture
def capture_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def _fake_sleep(duration: float) -> None:
        recorded.append(duration)

    monkeypatch.setattr(
        "autonoma.harness.llm_error_strategies.asyncio.sleep", _fake_sleep
    )
    return recorded


def test_strategies_registered() -> None:
    for v in ("backoff", "rate_limit_sleep", "abort"):
        assert is_stub("action.llm_error_handling", v) is False, v


# ── backoff ────────────────────────────────────────────────────────────


async def test_backoff_rate_limit_sleeps_2s(capture_sleep: list[float]) -> None:
    fn = lookup("action.llm_error_handling", "backoff")
    result = await fn(LLMRateLimitError("429"), "A")
    assert result["action"] == "idle"
    assert result["thinking"] == "rate_limited"
    assert capture_sleep == [2]


async def test_backoff_connection_error_skips_sleep(capture_sleep: list[float]) -> None:
    fn = lookup("action.llm_error_handling", "backoff")
    result = await fn(LLMConnectionError("no route"), "A")
    assert result["action"] == "idle"
    assert result["thinking"] == "connection_error"
    assert capture_sleep == []


# ── rate_limit_sleep ──────────────────────────────────────────────────


async def test_rate_limit_sleep_sleeps_longer(capture_sleep: list[float]) -> None:
    fn = lookup("action.llm_error_handling", "rate_limit_sleep")
    result = await fn(LLMRateLimitError("429"), "A")
    assert result["action"] == "idle"
    assert result["thinking"] == "rate_limited_long"
    assert capture_sleep == [10]


async def test_rate_limit_sleep_connection_fast_path(capture_sleep: list[float]) -> None:
    fn = lookup("action.llm_error_handling", "rate_limit_sleep")
    result = await fn(LLMConnectionError("no route"), "A")
    assert result["thinking"] == "connection_error"
    assert capture_sleep == []


# ── abort ──────────────────────────────────────────────────────────────


async def test_abort_reraises_rate_limit() -> None:
    fn = lookup("action.llm_error_handling", "abort")
    exc = LLMRateLimitError("429")
    with pytest.raises(LLMRateLimitError) as info:
        await fn(exc, "A")
    assert info.value is exc


async def test_abort_reraises_connection() -> None:
    fn = lookup("action.llm_error_handling", "abort")
    with pytest.raises(LLMConnectionError):
        await fn(LLMConnectionError("boom"), "A")
