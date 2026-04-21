"""Tests for ``spawn.approval_mode`` strategies.

Strategies are pure predicates; the swarm dispatcher that translates
denials into ``agent.spawn_failed`` events is covered by existing
swarm integration tests.
"""

from __future__ import annotations

from autonoma.harness import spawn_strategies  # noqa: F401 — ensures registration
from autonoma.harness.strategies import is_stub, lookup


def test_strategies_registered() -> None:
    for value in ("director_only", "peer_vote", "automatic"):
        assert is_stub("spawn.approval_mode", value) is False, value


# ── director_only ─────────────────────────────────────────────────────


def test_director_only_approves_director() -> None:
    ok, reason = lookup("spawn.approval_mode", "director_only")("Director", [])
    assert ok is True and reason == ""


def test_director_only_denies_peer() -> None:
    ok, reason = lookup("spawn.approval_mode", "director_only")(
        "Alice", ["Director", "Alice", "Bob"]
    )
    assert ok is False
    assert "director_only" in reason
    assert "Alice" in reason


def test_director_only_denies_empty_requester() -> None:
    """Legacy callers with no requester field are treated as outside
    requests — they shouldn't be able to spawn under director_only."""
    ok, reason = lookup("spawn.approval_mode", "director_only")("", [])
    assert ok is False
    assert "unknown" in reason


# ── peer_vote ─────────────────────────────────────────────────────────


def test_peer_vote_allows_director_unconditionally() -> None:
    ok, _ = lookup("spawn.approval_mode", "peer_vote")("Director", [])
    assert ok is True


def test_peer_vote_requires_two_peers() -> None:
    fn = lookup("spawn.approval_mode", "peer_vote")
    # Only Director present → no peers → deny
    ok, reason = fn("Alice", ["Director"])
    assert ok is False
    assert "peer_count=0" in reason

    # One peer besides sender isn't enough
    ok, reason = fn("Alice", ["Director", "Alice"])
    assert ok is False

    # Two+ non-Director peers in existence → approve
    ok, _ = fn("Alice", ["Director", "Alice", "Bob"])
    assert ok is True


# ── automatic ─────────────────────────────────────────────────────────


def test_automatic_always_approves() -> None:
    fn = lookup("spawn.approval_mode", "automatic")
    assert fn("", [])[0] is True
    assert fn("Director", ["Director"])[0] is True
    assert fn("Randall", ["Director", "Alice", "Bob", "Randall"])[0] is True
