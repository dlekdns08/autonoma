"""Tests for ``mood.transition_strategy`` end-of-turn mood updates."""

from __future__ import annotations

import random

from autonoma.harness import mood_strategies  # noqa: F401 — registers
from autonoma.harness.strategies import is_stub, lookup
from autonoma.world import Mood


def test_strategies_registered() -> None:
    for v in ("sticky", "reactive", "random_walk"):
        assert is_stub("mood.transition_strategy", v) is False, v


# ── sticky ─────────────────────────────────────────────────────────────


def test_sticky_returns_current_regardless_of_result() -> None:
    """Pre-harness default — direct assignments elsewhere still fire;
    this strategy adds nothing."""
    fn = lookup("mood.transition_strategy", "sticky")
    rng = random.Random(0)
    for result in ({"action": "idle"}, {"error": "boom"}, {"action": "celebrate"}):
        assert fn(Mood.CURIOUS, result, rng) is Mood.CURIOUS


# ── reactive ───────────────────────────────────────────────────────────


def test_reactive_error_result_pushes_frustrated() -> None:
    fn = lookup("mood.transition_strategy", "reactive")
    rng = random.Random(0)
    assert fn(Mood.HAPPY, {"action": "work_on_task", "error": "boom"}, rng) is Mood.FRUSTRATED


def test_reactive_maps_complete_task_to_proud() -> None:
    fn = lookup("mood.transition_strategy", "reactive")
    rng = random.Random(0)
    assert fn(Mood.CURIOUS, {"action": "complete_task"}, rng) is Mood.PROUD


def test_reactive_maps_celebrate_to_excited() -> None:
    fn = lookup("mood.transition_strategy", "reactive")
    rng = random.Random(0)
    assert fn(Mood.CURIOUS, {"action": "celebrate"}, rng) is Mood.EXCITED


def test_reactive_unknown_action_preserves_current() -> None:
    fn = lookup("mood.transition_strategy", "reactive")
    rng = random.Random(0)
    assert fn(Mood.NOSTALGIC, {"action": "something_weird"}, rng) is Mood.NOSTALGIC


# ── random_walk ────────────────────────────────────────────────────────


def test_random_walk_mostly_preserves_with_fixed_seed() -> None:
    """With seed=0, the 10% jump chance rarely fires — assert the
    distribution: majority of calls return current."""
    fn = lookup("mood.transition_strategy", "random_walk")
    rng = random.Random(0)
    kept = sum(
        1
        for _ in range(200)
        if fn(Mood.CURIOUS, {"action": "idle"}, rng) is Mood.CURIOUS
    )
    # 10% jump → ~180 kept. Allow wide tolerance.
    assert 150 <= kept <= 200


def test_random_walk_can_transition_to_a_different_mood() -> None:
    fn = lookup("mood.transition_strategy", "random_walk")
    rng = random.Random(42)
    results = {
        fn(Mood.CURIOUS, {"action": "idle"}, rng) for _ in range(500)
    }
    # At least one shift off CURIOUS over 500 samples.
    assert len(results) >= 2
