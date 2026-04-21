"""Tests for ``decision.message_priority`` inbox-trimming strategies.

Uses SimpleNamespace stand-ins so the strategy module stays decoupled
from ``AgentMessage``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from autonoma.harness import message_strategies  # noqa: F401 — registers
from autonoma.harness.strategies import is_stub, lookup


def _msg(sender: str, t: int, prio: int, tag: str = "") -> SimpleNamespace:
    """``t`` is minutes offset so timestamp ordering matches insertion order."""
    base = datetime(2026, 1, 1, 12, 0, 0)
    return SimpleNamespace(
        sender=sender,
        timestamp=base + timedelta(minutes=t),
        priority=prio,
        tag=tag or f"{sender}#{t}",
    )


def _prio(m: SimpleNamespace) -> int:
    return m.priority


# ── registration ──────────────────────────────────────────────────────


def test_strategies_registered() -> None:
    for v in ("urgency_ordered", "fifo", "round_robin"):
        assert is_stub("decision.message_priority", v) is False, v


# ── under-cap: all strategies return unchanged ────────────────────────


def test_under_cap_all_strategies_pass_through() -> None:
    msgs = [_msg("A", i, 5) for i in range(3)]
    for name in ("urgency_ordered", "fifo", "round_robin"):
        fn = lookup("decision.message_priority", name)
        got = fn(msgs, max_size=10, priority_fn=_prio)
        assert [m.tag for m in got] == [m.tag for m in msgs], name


# ── urgency_ordered ───────────────────────────────────────────────────


def test_urgency_ordered_keeps_high_urgency_drops_low() -> None:
    # Mix of urgency — keep top 3 by priority (lower number = higher urgency).
    msgs = [
        _msg("A", 0, prio=9, tag="chatty-old"),
        _msg("A", 1, prio=0, tag="task-assign"),
        _msg("B", 2, prio=9, tag="chatty-new"),
        _msg("B", 3, prio=1, tag="help-req"),
        _msg("A", 4, prio=9, tag="noise"),
    ]
    fn = lookup("decision.message_priority", "urgency_ordered")
    kept = fn(msgs, max_size=3, priority_fn=_prio)
    tags = {m.tag for m in kept}
    assert "task-assign" in tags
    assert "help-req" in tags
    # Among the three prio=9 chatty messages, the newest wins ties.
    assert "noise" in tags  # t=4, most recent chatty
    assert "chatty-old" not in tags


def test_urgency_ordered_preserves_timestamp_order() -> None:
    msgs = [_msg("A", i, prio=i) for i in range(5)]  # 0 is most urgent
    fn = lookup("decision.message_priority", "urgency_ordered")
    kept = fn(msgs, max_size=3, priority_fn=_prio)
    assert [m.tag for m in kept] == ["A#0", "A#1", "A#2"]


# ── fifo ──────────────────────────────────────────────────────────────


def test_fifo_keeps_newest_ignores_priority() -> None:
    msgs = [
        _msg("A", 0, prio=0, tag="urgent-but-old"),
        _msg("A", 1, prio=9),
        _msg("A", 2, prio=9),
        _msg("A", 3, prio=9),
    ]
    fn = lookup("decision.message_priority", "fifo")
    kept = fn(msgs, max_size=2, priority_fn=_prio)
    tags = [m.tag for m in kept]
    assert tags == ["A#2", "A#3"]
    assert "urgent-but-old" not in tags


# ── round_robin ───────────────────────────────────────────────────────


def test_round_robin_balances_across_senders() -> None:
    msgs = [
        _msg("A", 0, prio=5),
        _msg("A", 1, prio=5),
        _msg("A", 2, prio=5),
        _msg("A", 3, prio=5),
        _msg("B", 4, prio=5),
        _msg("C", 5, prio=5),
    ]
    fn = lookup("decision.message_priority", "round_robin")
    kept = fn(msgs, max_size=3, priority_fn=_prio)
    senders = {m.sender for m in kept}
    # Each distinct sender should have at least one message.
    assert senders == {"A", "B", "C"}


def test_round_robin_prefers_newest_within_sender() -> None:
    msgs = [
        _msg("A", 0, prio=5, tag="A-old"),
        _msg("A", 1, prio=5, tag="A-mid"),
        _msg("A", 2, prio=5, tag="A-new"),
        _msg("B", 3, prio=5, tag="B-new"),
    ]
    fn = lookup("decision.message_priority", "round_robin")
    kept = fn(msgs, max_size=2, priority_fn=_prio)
    tags = {m.tag for m in kept}
    assert tags == {"A-new", "B-new"}


def test_round_robin_returns_timestamp_ordered() -> None:
    msgs = [
        _msg("A", 0, prio=5),
        _msg("B", 1, prio=5),
        _msg("A", 2, prio=5),
        _msg("B", 3, prio=5),
    ]
    fn = lookup("decision.message_priority", "round_robin")
    kept = fn(msgs, max_size=3, priority_fn=_prio)
    assert [m.timestamp for m in kept] == sorted(m.timestamp for m in kept)
