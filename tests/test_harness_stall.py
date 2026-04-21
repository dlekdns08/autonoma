"""Tests for ``loop.stall_policy`` strategies.

The strategies are pure planners — no side effects — so the tests call
them with crafted task fixtures and inspect the returned plan. The
Director's side-effect dispatcher is covered by the swarm integration
tests.
"""

from __future__ import annotations

from autonoma.harness import stall_strategies  # noqa: F401 — ensures registration
from autonoma.harness.strategies import is_stub, lookup
from autonoma.models import Task, TaskStatus


def _task(title: str, status: TaskStatus, depends_on: list[str] | None = None) -> Task:
    t = Task(title=title, description=title)
    t.status = status
    if depends_on is not None:
        t.depends_on = list(depends_on)
    return t


def test_stall_strategies_registered() -> None:
    for value in ("auto_unblock", "wait", "escalate_to_director"):
        assert is_stub("loop.stall_policy", value) is False, value


# ── auto_unblock ──────────────────────────────────────────────────────


def test_auto_unblock_prefers_review_tasks() -> None:
    fn = lookup("loop.stall_policy", "auto_unblock")
    reviews = [_task("r1", TaskStatus.REVIEW), _task("r2", TaskStatus.REVIEW)]
    opens = [_task("o1", TaskStatus.OPEN, depends_on=["x"])]
    plan = fn(reviews, opens, 2)
    assert plan["action"] == "approve_reviews"
    assert plan["tasks"] == reviews


def test_auto_unblock_falls_back_to_clearing_deps() -> None:
    fn = lookup("loop.stall_policy", "auto_unblock")
    opens = [_task("o1", TaskStatus.OPEN, depends_on=["x", "y"])]
    plan = fn([], opens, 1)
    assert plan["action"] == "clear_deps"
    assert plan["task"] is opens[0]
    assert plan["cleared"] == ["x", "y"]


def test_auto_unblock_escalates_when_nothing_to_do() -> None:
    fn = lookup("loop.stall_policy", "auto_unblock")
    # no reviews, no opens → nothing mechanical to unblock
    plan = fn([], [], 0)
    assert plan["action"] == "escalate"
    assert "no unblock path" in plan["message"]


def test_auto_unblock_escalates_when_no_agents_available() -> None:
    fn = lookup("loop.stall_policy", "auto_unblock")
    opens = [_task("o1", TaskStatus.OPEN, depends_on=["x"])]
    plan = fn([], opens, 0)
    assert plan["action"] == "escalate"


# ── wait ──────────────────────────────────────────────────────────────


def test_wait_never_mutates() -> None:
    fn = lookup("loop.stall_policy", "wait")
    reviews = [_task("r", TaskStatus.REVIEW)]
    opens = [_task("o", TaskStatus.OPEN)]
    plan = fn(reviews, opens, 3)
    assert plan == {"action": "none"}


# ── escalate_to_director ──────────────────────────────────────────────


def test_escalate_reports_review_count() -> None:
    fn = lookup("loop.stall_policy", "escalate_to_director")
    reviews = [_task("r", TaskStatus.REVIEW)]
    plan = fn(reviews, [], 1)
    assert plan["action"] == "escalate"
    assert "1 REVIEW" in plan["message"]


def test_escalate_reports_no_agents() -> None:
    fn = lookup("loop.stall_policy", "escalate_to_director")
    plan = fn([], [], 0)
    assert plan["action"] == "escalate"
    assert "no available agents" in plan["message"]
