"""Loop strategies — drive the outer swarm loop's exit decision and stall
handling.

Each strategy is a pure predicate/effect function registered in the
central strategy registry. The swarm invokes them at specific call sites
and acts on the returned directive.

Keeping loop control pluggable here means presets can swap behaviors
(e.g. a "max_rounds_only" run for benchmarking, or "director_decides"
for a director-led demo) without touching ``AgentSwarm``.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register
from autonoma.models import ProjectState, TaskStatus


# ── loop.exit_condition ───────────────────────────────────────────────
#
# Signature: (project, director_action) -> (should_exit, reason)
#
# ``director_action`` is the action string the Director returned this
# round, or ``None`` when called from the end-of-round check point.


@register("loop.exit_condition", "all_tasks_done")
def _exit_all_tasks_done(
    project: ProjectState,
    director_action: str | None,
) -> tuple[bool, str]:
    """Default. Exits when the Director signals completion, when the
    ``project.completed`` flag flips, or when every task is DONE.

    The per-task scan is the authoritative signal; the other two are
    fast paths for the common "director finished up cleanly" case."""
    if director_action == "project_complete":
        return True, "project_complete"
    if project.completed:
        return True, "completed_flag"
    if project.tasks and all(t.status == TaskStatus.DONE for t in project.tasks):
        return True, "all_tasks_done"
    return False, ""


@register("loop.exit_condition", "director_decides")
def _exit_director_decides(
    project: ProjectState,
    director_action: str | None,
) -> tuple[bool, str]:
    """Only the Director's explicit ``project_complete`` action ends the
    run. Useful when you want the Director to narrate a finale rather
    than having the loop quietly bail the moment every task flips to
    DONE."""
    if director_action == "project_complete":
        return True, "project_complete"
    return False, ""


@register("loop.exit_condition", "max_rounds_only")
def _exit_max_rounds_only(
    project: ProjectState,
    director_action: str | None,
) -> tuple[bool, str]:
    """Never exits early. The outer ``while round < max_rounds`` guard
    is the sole termination. Good for benchmarking or stress-testing
    ``max_rounds`` budgets — every run uses the full budget."""
    return False, ""
