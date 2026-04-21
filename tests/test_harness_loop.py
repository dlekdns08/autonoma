"""Tests for ``loop.exit_condition`` strategies.

Each strategy is a pure function — we exercise the predicate directly
instead of running a whole swarm loop. End-to-end verification that
AgentSwarm actually honors the strategy lives in the swarm integration
tests.
"""

from __future__ import annotations

from autonoma.harness import loop_strategies  # noqa: F401 — ensures registration
from autonoma.harness.strategies import is_stub, lookup
from autonoma.models import ProjectState, Task, TaskStatus


def _project(tasks: list[Task] | None = None, completed: bool = False) -> ProjectState:
    p = ProjectState(name="demo", description="demo")
    p.tasks = tasks or []
    p.completed = completed
    return p


def _task(status: TaskStatus) -> Task:
    return Task(title="t", description="t", status=status)


# ── registration ──────────────────────────────────────────────────────


def test_exit_strategies_are_registered() -> None:
    for value in ("all_tasks_done", "director_decides", "max_rounds_only"):
        assert is_stub("loop.exit_condition", value) is False, value


# ── all_tasks_done (default) ──────────────────────────────────────────


def test_all_tasks_done_exits_on_director_signal() -> None:
    fn = lookup("loop.exit_condition", "all_tasks_done")
    got = fn(_project(), "project_complete")
    assert got == (True, "project_complete")


def test_all_tasks_done_exits_on_completed_flag() -> None:
    fn = lookup("loop.exit_condition", "all_tasks_done")
    got = fn(_project(completed=True), None)
    assert got == (True, "completed_flag")


def test_all_tasks_done_exits_when_every_task_done() -> None:
    fn = lookup("loop.exit_condition", "all_tasks_done")
    tasks = [_task(TaskStatus.DONE), _task(TaskStatus.DONE)]
    got = fn(_project(tasks=tasks), None)
    assert got == (True, "all_tasks_done")


def test_all_tasks_done_stays_when_work_remains() -> None:
    fn = lookup("loop.exit_condition", "all_tasks_done")
    tasks = [_task(TaskStatus.DONE), _task(TaskStatus.IN_PROGRESS)]
    got = fn(_project(tasks=tasks), None)
    assert got == (False, "")


def test_all_tasks_done_empty_project_does_not_trip() -> None:
    """No tasks yet shouldn't be read as 'everything is done' — that
    would make an empty goal exit immediately on round 0."""
    fn = lookup("loop.exit_condition", "all_tasks_done")
    got = fn(_project(tasks=[]), None)
    assert got == (False, "")


# ── director_decides ──────────────────────────────────────────────────


def test_director_decides_ignores_completed_flag() -> None:
    fn = lookup("loop.exit_condition", "director_decides")
    # Even with completed flag + all tasks done, no director signal → stay.
    tasks = [_task(TaskStatus.DONE)]
    got = fn(_project(tasks=tasks, completed=True), None)
    assert got == (False, "")


def test_director_decides_exits_only_on_director_signal() -> None:
    fn = lookup("loop.exit_condition", "director_decides")
    assert fn(_project(), "project_complete") == (True, "project_complete")
    assert fn(_project(), "think") == (False, "")


# ── max_rounds_only ───────────────────────────────────────────────────


def test_max_rounds_only_never_exits_early() -> None:
    fn = lookup("loop.exit_condition", "max_rounds_only")
    # Nothing short-circuits — caller is expected to rely on the
    # outer round counter.
    assert fn(_project(completed=True), "project_complete") == (False, "")
    tasks = [_task(TaskStatus.DONE)]
    assert fn(_project(tasks=tasks), None) == (False, "")
