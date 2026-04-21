"""Routing strategies — pick recipients for an ``AgentMessage``.

Each strategy is registered under ``routing.strategy`` and shares the same
shape:

    (msg, agent_names, state) -> list[str]

- ``msg``: the message being dispatched
- ``agent_names``: all live agent names (including the Director)
- ``state``: a mutable dict owned by the swarm; strategies may stash
  per-session state here (e.g. a round-robin cursor). The swarm never
  inspects its contents.

Strategies return agent *names* rather than agent objects so the swarm
retains full control over delivery (inbox truncation, etc.).

Import-time side effect: importing this module registers all three
implementations. ``autonoma.harness`` re-imports it so any caller that
depends on ``autonoma.harness.*`` gets the registry pre-populated.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register
from autonoma.models import AgentMessage


def _recipients_except_sender(
    msg: AgentMessage, agent_names: list[str]
) -> list[str]:
    return [n for n in agent_names if n != msg.sender]


@register("routing.strategy", "priority")
def _priority(
    msg: AgentMessage,
    agent_names: list[str],
    state: dict[str, Any],
) -> list[str]:
    """Current default: targeted delivery, with ``"all"`` fanning out to
    every agent except the sender. Unknown recipients drop silently — a
    message to a just-despawned agent shouldn't halt the loop."""
    if msg.recipient == "all":
        return _recipients_except_sender(msg, agent_names)
    if msg.recipient in agent_names:
        return [msg.recipient]
    return []


@register("routing.strategy", "round_robin")
def _round_robin(
    msg: AgentMessage,
    agent_names: list[str],
    state: dict[str, Any],
) -> list[str]:
    """Broadcasts get routed to a single agent, rotating each call so
    work spreads evenly. Directed messages bypass the rotation."""
    if msg.recipient != "all":
        return [msg.recipient] if msg.recipient in agent_names else []

    candidates = _recipients_except_sender(msg, agent_names)
    if not candidates:
        return []

    cursor = int(state.get("routing_rr_cursor", 0))
    pick = candidates[cursor % len(candidates)]
    state["routing_rr_cursor"] = cursor + 1
    return [pick]


@register("routing.strategy", "broadcast")
def _broadcast(
    msg: AgentMessage,
    agent_names: list[str],
    state: dict[str, Any],
) -> list[str]:
    """Every message reaches every agent (except the sender). Useful
    when debugging coordination issues — nobody can claim they missed it."""
    return _recipients_except_sender(msg, agent_names)
