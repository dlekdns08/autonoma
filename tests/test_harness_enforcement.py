"""Tests for ``action.harness_enforcement`` gate strategies.

Uses a minimal fake harness so we don't depend on the real
``AgentHarness`` capability set.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from autonoma.harness import enforcement_strategies  # noqa: F401 — registers
from autonoma.harness.strategies import is_stub, lookup


def _fake_harness(allowed: set[str], name: str = "fake") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        can_perform=lambda action: action in allowed,
    )


def test_strategies_registered() -> None:
    for v in ("strict", "permissive", "off"):
        assert is_stub("action.harness_enforcement", v) is False, v


# ── strict ─────────────────────────────────────────────────────────────


def test_strict_allows_capability_in_harness() -> None:
    fn = lookup("action.harness_enforcement", "strict")
    h = _fake_harness({"work_on_task"})
    assert fn("A", "work_on_task", h) is True


def test_strict_blocks_capability_not_in_harness() -> None:
    fn = lookup("action.harness_enforcement", "strict")
    h = _fake_harness({"work_on_task"})
    assert fn("A", "run_code", h) is False


# ── permissive ─────────────────────────────────────────────────────────


def test_permissive_always_allows_but_warns_on_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fn = lookup("action.harness_enforcement", "permissive")
    h = _fake_harness(set(), name="reader")
    with caplog.at_level(
        logging.WARNING, logger="autonoma.harness.enforcement_strategies"
    ):
        assert fn("A", "run_code", h) is True
    assert any("permissive" in rec.message for rec in caplog.records)


def test_permissive_silent_on_hit(caplog: pytest.LogCaptureFixture) -> None:
    fn = lookup("action.harness_enforcement", "permissive")
    h = _fake_harness({"work_on_task"})
    with caplog.at_level(
        logging.WARNING, logger="autonoma.harness.enforcement_strategies"
    ):
        assert fn("A", "work_on_task", h) is True
    assert caplog.records == []


# ── off ────────────────────────────────────────────────────────────────


def test_off_never_calls_can_perform() -> None:
    """``off`` shouldn't even query the harness — it's the escape hatch
    for benchmarking/dev where capability checks are pure overhead."""
    calls: list[str] = []

    def _track(action: str) -> bool:
        calls.append(action)
        return False

    h = SimpleNamespace(name="n", can_perform=_track)
    fn = lookup("action.harness_enforcement", "off")
    assert fn("A", "anything", h) is True
    assert calls == []
