"""Strategies for ``mood.transition_strategy``.

Applied once per ``think_and_act`` turn, AFTER the action executes. The
strategy inspects the action result and optionally shifts the agent's
mood. Direct mood assignments sprinkled through the codebase (level-up
celebrations, social events, task failures) still fire — this strategy
is an additional pass that runs last.

- ``sticky`` (default, pre-harness): keep the current mood. No-op.
  Preserves the current codebase's behavior where mood only changes at
  explicit event sites.
- ``reactive``: flip mood based on this turn's outcome — errors push
  toward FRUSTRATED, completions toward PROUD, idle decays toward
  FOCUSED. Useful for demos where visible emotional reactivity reads
  better than the mostly-flat default.
- ``random_walk``: every turn, small chance to jump to a random
  neighboring mood. Adds personality variance for long runs without
  needing richer event plumbing.

Strategy shape: ``(current_mood, action_result, rng) -> new_mood``. The
rng is passed in (rather than module-global) so tests can seed
deterministically.
"""

from __future__ import annotations

import random
from typing import Any

from autonoma.harness.strategies import register
from autonoma.world import Mood


@register("mood.transition_strategy", "sticky")
def _sticky(
    current: Mood, action_result: dict[str, Any], rng: random.Random
) -> Mood:
    return current


_REACTIVE_MAP: dict[str, Mood] = {
    "error": Mood.FRUSTRATED,
    "blocked": Mood.WORRIED,
    "complete_task": Mood.PROUD,
    "celebrate": Mood.EXCITED,
    "create_file": Mood.FOCUSED,
    "work_on_task": Mood.DETERMINED,
    "idle": Mood.RELAXED,
}


@register("mood.transition_strategy", "reactive")
def _reactive(
    current: Mood, action_result: dict[str, Any], rng: random.Random
) -> Mood:
    if action_result.get("error"):
        return Mood.FRUSTRATED
    action = action_result.get("action", "")
    return _REACTIVE_MAP.get(action, current)


_ADJACENT: tuple[Mood, ...] = (
    Mood.CURIOUS,
    Mood.FOCUSED,
    Mood.DETERMINED,
    Mood.RELAXED,
    Mood.INSPIRED,
    Mood.MISCHIEVOUS,
)


@register("mood.transition_strategy", "random_walk")
def _random_walk(
    current: Mood, action_result: dict[str, Any], rng: random.Random
) -> Mood:
    if rng.random() < 0.1:
        return rng.choice(_ADJACENT)
    return current
