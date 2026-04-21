"""Loop stall strategies — decide what to do when the swarm has made
no progress for ``stall_threshold`` rounds (currently 3).

Each strategy returns an action plan the Director executes. Keeping the
strategy a pure planner (no side effects) lets the Director hold the
locks and I/O, while the policy stays trivially testable.

Plan shapes:

- ``{"action": "none"}`` — do nothing, let the counter keep climbing.
- ``{"action": "approve_reviews", "tasks": list[Task]}`` — flip the given
  REVIEW tasks to DONE.
- ``{"action": "clear_deps", "task": Task, "cleared": list[str]}`` —
  strip dependencies from the given OPEN task so agents can pick it up.
- ``{"action": "escalate", "message": str}`` — no mutation, just a
  narration hook; the Director announces the stall and keeps going.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register
from autonoma.models import Task


StallPlan = dict[str, Any]


@register("loop.stall_policy", "auto_unblock")
def _auto_unblock(
    review_tasks: list[Task],
    open_tasks: list[Task],
    available_agents_count: int,
) -> StallPlan:
    """Default, matches pre-harness behavior. First try to rescue
    REVIEW tasks (no reviewer loop exists so they're a terminal trap);
    fall back to stripping an OPEN task's ``depends_on`` list so
    something becomes pickable."""
    if review_tasks:
        return {"action": "approve_reviews", "tasks": list(review_tasks)}

    if open_tasks and available_agents_count > 0:
        target = open_tasks[0]
        return {
            "action": "clear_deps",
            "task": target,
            "cleared": list(target.depends_on),
        }

    return {
        "action": "escalate",
        "message": (
            "stalled but no unblock path: no REVIEW tasks, "
            "no OPEN tasks, or no available agents"
        ),
    }


@register("loop.stall_policy", "wait")
def _wait(
    review_tasks: list[Task],
    open_tasks: list[Task],
    available_agents_count: int,
) -> StallPlan:
    """Passive — the Director notes the stall but takes no action.
    Useful when the operator wants to observe whether agents recover
    on their own without intervention."""
    return {"action": "none"}


@register("loop.stall_policy", "escalate_to_director")
def _escalate(
    review_tasks: list[Task],
    open_tasks: list[Task],
    available_agents_count: int,
) -> StallPlan:
    """Narrate the stall but don't mutate task state. The Director
    will announce the stall in its speech; a human operator can then
    intervene via chat or task edits."""
    reasons = []
    if review_tasks:
        reasons.append(f"{len(review_tasks)} REVIEW tasks waiting")
    if open_tasks:
        reasons.append(f"{len(open_tasks)} OPEN tasks blocked")
    if available_agents_count == 0:
        reasons.append("no available agents")
    detail = "; ".join(reasons) or "unknown cause"
    return {
        "action": "escalate",
        "message": f"stall persisted 3 rounds ({detail})",
    }
