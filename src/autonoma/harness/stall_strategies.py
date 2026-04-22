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
from autonoma.models import Task, TaskStatus


StallPlan = dict[str, Any]


def _dep_is_stale(
    dep_id: str, task_status_map: dict[str, TaskStatus] | None
) -> bool:
    """A dep is 'stale' only when we're sure it's not blocking real work:
    either the upstream is ``DONE`` or the ID is orphaned (task deleted,
    never existed). Any live status (``OPEN``/``ASSIGNED``/
    ``IN_PROGRESS``/``REVIEW``/``BLOCKED``) is preserved because
    clearing it would bypass a genuine critical-path stage — e.g.
    starting review before implementation finishes.

    When ``task_status_map`` is ``None`` the caller didn't supply
    context, so we conservatively treat every dep as stale. This
    preserves behavior for older callers/tests that pre-date the
    critical-path guard.
    """
    if task_status_map is None:
        return True
    status = task_status_map.get(dep_id)
    return status is None or status == TaskStatus.DONE


@register("loop.stall_policy", "auto_unblock")
def _auto_unblock(
    review_tasks: list[Task],
    open_tasks: list[Task],
    available_agents_count: int,
    task_status_map: dict[str, TaskStatus] | None = None,
) -> StallPlan:
    """Default, matches pre-harness behavior. First try to rescue
    REVIEW tasks (no reviewer loop exists so they're a terminal trap);
    fall back to stripping stale entries from an OPEN task's
    ``depends_on`` so something becomes pickable.

    Critical-path guard: only deps whose upstream is ``DONE`` or
    orphaned get cleared. If every OPEN task's remaining deps point to
    live upstreams (IN_PROGRESS/ASSIGNED/etc.), we escalate rather than
    short-circuit a real stage.
    """
    if review_tasks:
        return {"action": "approve_reviews", "tasks": list(review_tasks)}

    if open_tasks and available_agents_count > 0:
        for target in open_tasks:
            if not target.depends_on:
                # Nothing to clear — but the Director will still kick
                # scheduling by treating the pass as a no-op unblock.
                return {
                    "action": "clear_deps",
                    "task": target,
                    "cleared": [],
                }
            stale = [
                d for d in target.depends_on
                if _dep_is_stale(d, task_status_map)
            ]
            if stale:
                # Clear only the stale subset; any live deps stay in
                # place so the pipeline ordering is respected.
                return {
                    "action": "clear_deps",
                    "task": target,
                    "cleared": stale,
                }

        return {
            "action": "escalate",
            "message": (
                "stalled: every OPEN task depends on a live upstream "
                "(IN_PROGRESS/ASSIGNED/REVIEW) — refusing to bypass "
                "critical path, needs operator intervention"
            ),
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
    task_status_map: dict[str, TaskStatus] | None = None,
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
    task_status_map: dict[str, TaskStatus] | None = None,
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
