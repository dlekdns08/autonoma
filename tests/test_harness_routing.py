"""Tests for ``routing.strategy`` implementations.

The three strategies share a signature, so each test block exercises the
same inputs — broadcast message and a directed message — and asserts on
who the strategy selects for delivery. Round-robin also needs its
cross-call state to be honored.
"""

from __future__ import annotations

import pytest

from autonoma.harness import routing_strategies  # noqa: F401 — ensures registration
from autonoma.harness.strategies import is_stub, lookup
from autonoma.models import AgentMessage, MessageType


def _msg(sender: str, recipient: str) -> AgentMessage:
    return AgentMessage(
        sender=sender,
        recipient=recipient,
        msg_type=MessageType.CHAT,
        content="hi",
    )


def test_registry_entries_are_no_longer_stubs() -> None:
    for value in ("priority", "round_robin", "broadcast"):
        assert is_stub("routing.strategy", value) is False, value


def test_priority_broadcasts_all_to_everyone_except_sender() -> None:
    fn = lookup("routing.strategy", "priority")
    agents = ["Director", "Alice", "Bob"]
    state: dict = {}
    assert fn(_msg("Alice", "all"), agents, state) == ["Director", "Bob"]


def test_priority_directs_to_named_recipient() -> None:
    fn = lookup("routing.strategy", "priority")
    agents = ["Director", "Alice", "Bob"]
    state: dict = {}
    assert fn(_msg("Alice", "Bob"), agents, state) == ["Bob"]


def test_priority_drops_unknown_recipient_silently() -> None:
    fn = lookup("routing.strategy", "priority")
    agents = ["Director", "Alice"]
    assert fn(_msg("Alice", "Ghost"), agents, {}) == []


def test_round_robin_cycles_through_candidates_on_broadcast() -> None:
    fn = lookup("routing.strategy", "round_robin")
    agents = ["Director", "Alice", "Bob", "Carol"]
    state: dict = {}

    picks = [fn(_msg("Alice", "all"), agents, state)[0] for _ in range(6)]
    # Sender "Alice" is excluded; the other three cycle.
    assert picks[:3] == ["Director", "Bob", "Carol"]
    assert picks[3:] == ["Director", "Bob", "Carol"]


def test_round_robin_still_directs_when_recipient_is_named() -> None:
    fn = lookup("routing.strategy", "round_robin")
    agents = ["Director", "Alice", "Bob"]
    state: dict = {"routing_rr_cursor": 99}
    assert fn(_msg("Alice", "Bob"), agents, state) == ["Bob"]
    # Directed path should not touch the cursor.
    assert state["routing_rr_cursor"] == 99


def test_round_robin_empty_candidate_list_returns_empty() -> None:
    fn = lookup("routing.strategy", "round_robin")
    # sender is the only agent left — nobody to fan out to.
    assert fn(_msg("Alice", "all"), ["Alice"], {}) == []


def test_broadcast_ignores_named_recipient_and_fans_out() -> None:
    fn = lookup("routing.strategy", "broadcast")
    agents = ["Director", "Alice", "Bob"]
    # Even though the message is addressed to Bob, broadcast sends it
    # to everyone (except the sender).
    got = fn(_msg("Alice", "Bob"), agents, {})
    assert got == ["Director", "Bob"]


def test_broadcast_excludes_sender_on_all() -> None:
    fn = lookup("routing.strategy", "broadcast")
    agents = ["Director", "Alice", "Bob"]
    assert fn(_msg("Alice", "all"), agents, {}) == ["Director", "Bob"]


def test_unknown_strategy_value_raises_keyerror() -> None:
    # The Pydantic model rejects this too, but the registry is the
    # last line of defense at dispatch time.
    with pytest.raises(KeyError):
        lookup("routing.strategy", "not_a_real_strategy")
