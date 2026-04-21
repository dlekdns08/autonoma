"""Tests for ``memory.summarization`` strategies."""

from __future__ import annotations

from types import SimpleNamespace

from autonoma.harness import memory_strategies  # noqa: F401 — registers
from autonoma.harness.strategies import is_stub, lookup


def _entry(i: int, kind: str = "observation") -> SimpleNamespace:
    ns = SimpleNamespace(
        text=f"entry-{i}",
        memory_type=kind,
        round_number=i,
    )
    ns.__str__ = lambda self=ns: f"[{self.memory_type}#{self.round_number}] {self.text}"
    return ns


def test_strategies_registered() -> None:
    for v in ("none", "tail_window", "rolling_summary"):
        assert is_stub("memory.summarization", v) is False, v


# ── none ───────────────────────────────────────────────────────────────


def test_none_returns_tail_matching_pre_harness() -> None:
    """Pre-harness behavior was ``private[-6:]`` — the ``none`` strategy
    must preserve that byte-for-byte."""
    fn = lookup("memory.summarization", "none")
    entries = [_entry(i) for i in range(10)]
    got = fn(entries, limit=6)
    assert len(got) == 6
    assert all(line.startswith("    ") for line in got)
    # Last entry must be the most recent (index 9).
    assert "entry-9" in got[-1]
    assert "entry-4" in got[0]  # 10 - 6 = 4


def test_none_under_limit_returns_all() -> None:
    fn = lookup("memory.summarization", "none")
    entries = [_entry(i) for i in range(3)]
    got = fn(entries, limit=6)
    assert len(got) == 3


# ── tail_window ────────────────────────────────────────────────────────


def test_tail_window_matches_none_at_default() -> None:
    """Documented synonym — same output. The separate slot exists so
    future window-tuning lives under a dedicated name."""
    none_fn = lookup("memory.summarization", "none")
    tw_fn = lookup("memory.summarization", "tail_window")
    entries = [_entry(i) for i in range(10)]
    assert none_fn(entries, 6) == tw_fn(entries, 6)


def test_tail_window_respects_custom_limit() -> None:
    fn = lookup("memory.summarization", "tail_window")
    entries = [_entry(i) for i in range(10)]
    assert len(fn(entries, limit=3)) == 3


# ── rolling_summary ────────────────────────────────────────────────────


def test_rolling_summary_prepends_roll_up_line() -> None:
    fn = lookup("memory.summarization", "rolling_summary")
    entries = (
        [_entry(i, "observation") for i in range(5)]
        + [_entry(i, "failure") for i in range(5, 8)]
        + [_entry(i, "lesson") for i in range(8, 12)]
    )
    got = fn(entries, limit=4)
    assert got[0].startswith("    [Prior 8:")
    assert "observation" in got[0] or "5 observation" in got[0]
    assert "failure" in got[0]
    assert len(got) == 5  # 1 summary + 4 tail


def test_rolling_summary_below_limit_no_summary_line() -> None:
    fn = lookup("memory.summarization", "rolling_summary")
    entries = [_entry(i) for i in range(3)]
    got = fn(entries, limit=6)
    assert len(got) == 3
    assert not any("Prior" in line for line in got)
