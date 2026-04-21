"""Checkpoint-shape strategies.

``checkpoint.include_full_state`` decides how fat a periodic
``session.checkpoint`` event is. ``"off"`` emits just the heartbeat
(session_id + round + timestamp); ``"on"`` adds the agent roster and
recent message tail so a watcher could rebuild the UI without a fresh
snapshot.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register


@register("checkpoint.include_full_state", "off")
def _off(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": context.get("session_id"),
        "round": context.get("round"),
        "tokens_used": context.get("tokens_used", 0),
    }


@register("checkpoint.include_full_state", "on")
def _on(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": context.get("session_id"),
        "round": context.get("round"),
        "tokens_used": context.get("tokens_used", 0),
        "agents": context.get("agents", []),
        "recent_messages": context.get("recent_messages", []),
    }
