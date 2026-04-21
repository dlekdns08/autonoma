"""Strategies for ``safety.enforcement_level``.

Circuit breaker policy — what to do when an agent has accumulated a run
of consecutive errors (timeouts, parse failures, unhandled exceptions).
Pre-harness the field was inert; the ``strict`` default now gives
operators a real fail-safe.

- ``strict`` (default): once ``error_count >= threshold``, return a
  circuit-break decision so the caller can skip this turn instead of
  re-hammering a broken agent. Slightly tighter than pre-harness (which
  kept running), traded off for not burning token budget on an agent
  that's clearly stuck.
- ``permissive``: warn when the threshold is crossed but let the agent
  keep trying — useful when errors are expected (flaky upstream
  services) and the run shouldn't stall for them.
- ``off``: don't inspect the counter at all. For development/benchmark
  runs where circuit-breaker noise obscures the thing being measured.

Strategy shape: ``(error_count, threshold, agent_name) -> dict | None``.
``None`` means "proceed normally". A dict means "short-circuit — return
this decision from ``think_and_act`` without calling the LLM".
"""

from __future__ import annotations

import logging
from typing import Any

from autonoma.harness.strategies import register

logger = logging.getLogger(__name__)


@register("safety.enforcement_level", "strict")
def _strict(
    error_count: int, threshold: int, agent_name: str
) -> dict[str, Any] | None:
    if error_count >= threshold:
        logger.error(
            f"[{agent_name}] circuit breaker tripped "
            f"({error_count} consecutive errors >= {threshold})"
        )
        return {
            "agent": agent_name,
            "action": "idle",
            "error": "circuit_break",
            "speech": "Too many failures, cooling down...",
            "thinking": "circuit_break",
        }
    return None


@register("safety.enforcement_level", "permissive")
def _permissive(
    error_count: int, threshold: int, agent_name: str
) -> dict[str, Any] | None:
    if error_count >= threshold:
        logger.warning(
            f"[{agent_name}] (permissive) {error_count} consecutive errors "
            f"(threshold {threshold}) — continuing anyway"
        )
    return None


@register("safety.enforcement_level", "off")
def _off(
    error_count: int, threshold: int, agent_name: str
) -> dict[str, Any] | None:
    return None
