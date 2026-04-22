"""Strategies for ``action.harness_enforcement``.

Decides what to do when an agent tries to take an action that isn't in
its harness's capability list. Three variants balance safety against
friction:

- ``strict`` (default, pre-harness): block the action. Caller converts
  this into a ``"blocked"`` result so the UI and telemetry see the miss.
- ``permissive``: log a warning and let the action through. Useful for
  exploring new action types without blowing up the run.
- ``off``: no checking at all. Development/benchmarking shortcut;
  dangerous in production because it sidesteps every capability grant.

Strategy shape: ``(agent_name, action_type, harness) -> bool``. True
means the caller should proceed; False means the caller should emit the
blocked-action fallback. Keeping this predicate-shaped (rather than
returning a decision dict) lets the caller build the user-facing speech
in one place.
"""

from __future__ import annotations

import logging
from typing import Any

from autonoma.harness.strategies import register

logger = logging.getLogger(__name__)


def _classify(harness: Any, action_type: str) -> tuple[bool, str | None]:
    """Prefer :meth:`check_action` when the harness exposes it; fall back
    to :meth:`can_perform` for mocks/test doubles that only implement the
    boolean predicate.
    """
    check = getattr(harness, "check_action", None)
    if callable(check):
        return check(action_type)
    return bool(harness.can_perform(action_type)), None


@register("action.harness_enforcement", "strict")
def _strict(agent_name: str, action_type: str, harness: Any) -> bool:
    allowed, reason = _classify(harness, action_type)
    if not allowed:
        logger.warning(
            f"[{agent_name}] harness '{harness.name}' blocked action "
            f"'{action_type}'"
            + (f": {reason}" if reason else "")
        )
    return allowed


@register("action.harness_enforcement", "permissive")
def _permissive(agent_name: str, action_type: str, harness: Any) -> bool:
    allowed, reason = _classify(harness, action_type)
    if not allowed:
        logger.warning(
            f"[{agent_name}] (permissive) harness '{harness.name}' would "
            f"block action '{action_type}'"
            + (f" ({reason})" if reason else "")
            + " — allowing anyway"
        )
    return True


@register("action.harness_enforcement", "off")
def _off(agent_name: str, action_type: str, harness: Any) -> bool:
    logger.debug(
        f"[{agent_name}] harness_enforcement=off — skipping capability check "
        f"for action '{action_type}' (harness: {harness.name})"
    )
    return True
