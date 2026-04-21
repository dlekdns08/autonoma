"""Tests for ``safety.enforcement_level`` circuit breaker strategies."""

from __future__ import annotations

import logging

import pytest

from autonoma.harness import safety_enforcement_strategies  # noqa: F401 — registers
from autonoma.harness.strategies import is_stub, lookup


def test_strategies_registered() -> None:
    for v in ("strict", "permissive", "off"):
        assert is_stub("safety.enforcement_level", v) is False, v


# ── strict ─────────────────────────────────────────────────────────────


def test_strict_below_threshold_proceeds() -> None:
    fn = lookup("safety.enforcement_level", "strict")
    assert fn(error_count=2, threshold=5, agent_name="A") is None


def test_strict_at_threshold_trips() -> None:
    fn = lookup("safety.enforcement_level", "strict")
    got = fn(error_count=5, threshold=5, agent_name="A")
    assert got is not None
    assert got["error"] == "circuit_break"
    assert got["thinking"] == "circuit_break"
    assert got["agent"] == "A"


def test_strict_above_threshold_trips() -> None:
    fn = lookup("safety.enforcement_level", "strict")
    assert fn(error_count=10, threshold=5, agent_name="A") is not None


# ── permissive ─────────────────────────────────────────────────────────


def test_permissive_never_trips(caplog: pytest.LogCaptureFixture) -> None:
    fn = lookup("safety.enforcement_level", "permissive")
    with caplog.at_level(logging.WARNING):
        assert fn(error_count=99, threshold=5, agent_name="A") is None


def test_permissive_warns_above_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fn = lookup("safety.enforcement_level", "permissive")
    with caplog.at_level(
        logging.WARNING,
        logger="autonoma.harness.safety_enforcement_strategies",
    ):
        fn(error_count=6, threshold=5, agent_name="A")
    assert any("permissive" in rec.message for rec in caplog.records)


def test_permissive_silent_below_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fn = lookup("safety.enforcement_level", "permissive")
    with caplog.at_level(
        logging.WARNING,
        logger="autonoma.harness.safety_enforcement_strategies",
    ):
        fn(error_count=1, threshold=5, agent_name="A")
    assert caplog.records == []


# ── off ────────────────────────────────────────────────────────────────


def test_off_never_trips_and_silent(caplog: pytest.LogCaptureFixture) -> None:
    fn = lookup("safety.enforcement_level", "off")
    with caplog.at_level(logging.DEBUG):
        assert fn(error_count=999, threshold=5, agent_name="A") is None
    assert caplog.records == []
