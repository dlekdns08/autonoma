"""Strategies for ``action.llm_error_handling``.

Fires when the LLM client raises — either a connection error (network
blip, DNS failure, timeout) or a rate-limit refusal. The three variants
trade throughput for cost/strictness:

- ``backoff`` (default, pre-harness): sleep 2s on rate-limit, skip sleep
  on connection error, and return an idle fallback so the run
  continues. Matches the current hardcoded handler byte-for-byte.
- ``rate_limit_sleep``: same fallback shape but with a heavier rate-
  limit sleep (10s) for hosted/paid usage where burning through quota
  hurts. Connection errors still surface quickly.
- ``abort``: re-raise the exception. Strict mode — caller (swarm loop)
  gets to decide whether to kill the agent, retry, or escalate.

Strategy shape: ``async (exc, agent_name) -> dict[str, Any]``. Async
because the sleep has to ``await asyncio.sleep``; the caller already
has an event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from autonoma.harness.strategies import register
from autonoma.llm import LLMConnectionError, LLMRateLimitError


@register("action.llm_error_handling", "backoff")
async def _backoff(exc: Exception, agent_name: str) -> dict[str, Any]:
    if isinstance(exc, LLMRateLimitError):
        await asyncio.sleep(2)
        return {
            "action": "idle",
            "speech": "Rate limited, waiting...",
            "thinking": "rate_limited",
        }
    return {
        "action": "idle",
        "speech": "Can't reach the API...",
        "thinking": "connection_error",
    }


@register("action.llm_error_handling", "rate_limit_sleep")
async def _rate_limit_sleep(exc: Exception, agent_name: str) -> dict[str, Any]:
    if isinstance(exc, LLMRateLimitError):
        await asyncio.sleep(10)
        return {
            "action": "idle",
            "speech": "Backing off...",
            "thinking": "rate_limited_long",
        }
    return {
        "action": "idle",
        "speech": "Can't reach the API...",
        "thinking": "connection_error",
    }


@register("action.llm_error_handling", "abort")
async def _abort(exc: Exception, agent_name: str) -> dict[str, Any]:
    raise exc


