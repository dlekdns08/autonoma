"""Retry / timeout behavior of ``autonoma.llm._call_with_retry``.

These tests exercise the retry helper directly with mock async callables
so we don't hit any provider SDK. Coverage:

  * succeeds on first attempt without retrying
  * retries once on a transient ``LLMConnectionError`` and returns the
    second attempt's value
  * exhausts retries and re-raises the last transient error with a clear
    type
  * surfaces an asyncio.TimeoutError as ``LLMConnectionError`` and retries
"""

from __future__ import annotations

import asyncio

import pytest

from autonoma.llm import (
    LLMConnectionError,
    LLMRateLimitError,
    _call_with_retry,
)


@pytest.fixture(autouse=True)
def _zero_backoff(monkeypatch: pytest.MonkeyPatch):
    """Zero out the exponential backoff base delay so retry tests run fast.

    We can't monkeypatch ``asyncio.sleep`` itself — ``llm._call_with_retry``
    imports the module-level ``asyncio`` reference, so patching it would
    also no-op the user's ``asyncio.sleep`` calls inside ``do_call`` and
    break the timeout test. Tweaking the base-delay constant + jitter
    leaves real ``asyncio.sleep`` intact.
    """
    monkeypatch.setattr("autonoma.llm.LLM_RETRY_BASE_DELAY", 0.0)
    monkeypatch.setattr("autonoma.llm.random.uniform", lambda _a, _b: 0.0)


async def test_success_on_first_try() -> None:
    calls = {"n": 0}

    async def do_call() -> str:
        calls["n"] += 1
        return "ok"

    result = await _call_with_retry(do_call, label="t.first", max_retries=2)
    assert result == "ok"
    assert calls["n"] == 1


async def test_success_after_one_retry() -> None:
    calls = {"n": 0}

    async def do_call() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMConnectionError("flaky network")
        return "second-time-lucky"

    result = await _call_with_retry(do_call, label="t.retry", max_retries=2)
    assert result == "second-time-lucky"
    assert calls["n"] == 2


async def test_exhausts_retries_and_reraises_connection_error() -> None:
    calls = {"n": 0}

    async def do_call() -> str:
        calls["n"] += 1
        raise LLMConnectionError("upstream is down")

    with pytest.raises(LLMConnectionError) as exc_info:
        await _call_with_retry(do_call, label="t.exhaust", max_retries=2)

    # 1 initial attempt + 2 retries = 3 total
    assert calls["n"] == 3
    assert "upstream is down" in str(exc_info.value)


async def test_timeout_is_normalized_to_connection_error() -> None:
    calls = {"n": 0}

    async def do_call() -> str:
        calls["n"] += 1
        # Sleep longer than the wait_for timeout so asyncio cancels us.
        await asyncio.sleep(5.0)
        return "never"

    with pytest.raises(LLMConnectionError) as exc_info:
        await _call_with_retry(
            do_call, label="t.timeout", timeout=0.05, max_retries=1
        )
    assert calls["n"] == 2  # 1 initial + 1 retry
    assert "timed out" in str(exc_info.value).lower()


async def test_rate_limit_error_is_retried() -> None:
    """Rate-limit errors are explicitly classed as transient by the helper."""
    calls = {"n": 0}

    async def do_call() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise LLMRateLimitError("slow down")
        return "done"

    result = await _call_with_retry(do_call, label="t.rl", max_retries=2)
    assert result == "done"
    assert calls["n"] == 2


async def test_unexpected_exception_propagates_without_retry() -> None:
    """Auth and unknown errors should NOT be retried — burns quota for nothing."""
    calls = {"n": 0}

    async def do_call() -> str:
        calls["n"] += 1
        raise ValueError("bad request shape")

    with pytest.raises(ValueError):
        await _call_with_retry(do_call, label="t.fatal", max_retries=3)
    assert calls["n"] == 1
