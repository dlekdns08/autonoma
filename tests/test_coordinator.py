"""Unit tests for the swarm-vs-swarm matchmaking coordinator (feature #1)."""

from __future__ import annotations

import asyncio

import pytest

from autonoma.config import settings
from autonoma.coordinator.model import MatchInvite
from autonoma.coordinator.store import CoordinatorStore
from autonoma.event_bus import bus


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Fresh ``CoordinatorStore`` rooted under ``tmp_path``.

    Also points ``settings.data_dir`` at ``tmp_path`` so any code path
    that constructs the singleton lazily lands in the scratch dir.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return CoordinatorStore(root=tmp_path / "coordinator")


async def _register_pair(store: CoordinatorStore) -> None:
    await store.register_instance("inst-a", "Alpha", "https://a.example")
    await store.register_instance("inst-b", "Beta", "https://b.example")


# ── pairing ────────────────────────────────────────────────────────


async def test_pair_two_same_goal_invites(store):
    await _register_pair(store)
    inv_a = await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="solve fizzbuzz")
    )
    inv_b = await store.enqueue_match(
        MatchInvite(instance_id="inst-b", goal="solve fizzbuzz")
    )

    pairs = await store.pair_pending_matches()
    assert len(pairs) == 1
    a, b = pairs[0]
    assert {a.instance_id, b.instance_id} == {"inst-a", "inst-b"}
    assert a.match_id is not None
    assert a.match_id == b.match_id
    assert a.opponent_invite_id == b.id and b.opponent_invite_id == a.id
    # Calling again must NOT re-pair already-paired invites.
    assert await store.pair_pending_matches() == []
    # Persisted ids match what we enqueued.
    assert {a.id, b.id} == {inv_a.id, inv_b.id}


async def test_no_pairing_for_different_goals(store):
    await _register_pair(store)
    await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="goal-x")
    )
    await store.enqueue_match(
        MatchInvite(instance_id="inst-b", goal="goal-y")
    )
    assert await store.pair_pending_matches() == []


async def test_no_self_pairing(store):
    await store.register_instance("inst-a", "Alpha", "https://a.example")
    await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="solo")
    )
    await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="solo")
    )
    # Same instance can't be both sides of a match.
    assert await store.pair_pending_matches() == []


# ── scoring + ELO ──────────────────────────────────────────────────


async def test_submit_scores_resolves_match_and_updates_elo(store):
    await _register_pair(store)
    inv_a = await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="benchmark")
    )
    inv_b = await store.enqueue_match(
        MatchInvite(instance_id="inst-b", goal="benchmark")
    )
    pairs = await store.pair_pending_matches()
    assert len(pairs) == 1
    match_id = pairs[0][0].match_id
    assert match_id is not None

    # Listen for the bus event the spec requires.
    seen: list[dict] = []

    async def handler(**data):
        seen.append(data)

    bus.on("coordinator.match_resolved", handler)

    # Side A wins on every dimension.
    pending = await store.submit_score(
        match_id,
        "inst-a",
        {
            "task_completed": True,
            "tasks_done": 5,
            "rounds_used": 8,
            "files_created": 3,
        },
    )
    # First submission alone must NOT resolve the match.
    assert pending is None

    result = await store.submit_score(
        match_id,
        "inst-b",
        {
            "task_completed": False,
            "tasks_done": 2,
            "rounds_used": 12,
            "files_created": 1,
        },
    )
    assert result is not None
    assert result.winner == "inst-a"
    assert result.match_id == match_id
    assert {result.instance_a, result.instance_b} == {"inst-a", "inst-b"}
    # ELO: winner gains, loser loses, total is conserved (sum unchanged).
    assert result.elo_delta_a + result.elo_delta_b == pytest.approx(0.0)
    if result.instance_a == "inst-a":
        assert result.elo_delta_a > 0
        assert result.elo_delta_b < 0
    else:
        assert result.elo_delta_b > 0
        assert result.elo_delta_a < 0
    # Bus event was emitted exactly once.
    assert len(seen) == 1
    assert seen[0]["match_id"] == match_id

    # Re-submitting after resolution returns the cached result, no extra event.
    cached = await store.submit_score(
        match_id, "inst-a", {"task_completed": True}
    )
    assert cached is not None
    assert cached.match_id == match_id
    assert len(seen) == 1
    assert (inv_a.id, inv_b.id)  # silence unused-var lint while keeping the references for future debugging


async def test_concurrent_score_submissions_resolve_once(store):
    """Two ``submit_score`` calls firing concurrently must resolve the
    match exactly once (single ``coordinator.match_resolved`` event,
    ELO sum conserved, deterministic A/B ordering)."""
    await _register_pair(store)
    await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="race")
    )
    await store.enqueue_match(
        MatchInvite(instance_id="inst-b", goal="race")
    )
    pairs = await store.pair_pending_matches()
    assert len(pairs) == 1
    match_id = pairs[0][0].match_id
    assert match_id is not None

    seen: list[dict] = []

    async def handler(**data):
        seen.append(data)

    bus.on("coordinator.match_resolved", handler)

    # Both sides submit at the same time; the async lock inside the
    # store is the only thing keeping the resolution single-shot.
    results = await asyncio.gather(
        store.submit_score(
            match_id,
            "inst-a",
            {
                "task_completed": True,
                "tasks_done": 5,
                "rounds_used": 8,
                "files_created": 3,
            },
        ),
        store.submit_score(
            match_id,
            "inst-b",
            {
                "task_completed": False,
                "tasks_done": 2,
                "rounds_used": 12,
                "files_created": 1,
            },
        ),
    )

    # Exactly one of the two coroutines should observe the resolution
    # (the other returns ``None`` because its half arrives first).
    resolved = [r for r in results if r is not None]
    assert len(resolved) == 1, f"expected exactly one resolution, got {results}"
    result = resolved[0]
    assert result.match_id == match_id
    assert result.winner == "inst-a"

    # ELO is conserved (winner+loser deltas sum to zero).
    assert result.elo_delta_a + result.elo_delta_b == pytest.approx(0.0)

    # A/B ordering is deterministic regardless of which coroutine
    # crossed the lock first.
    assert {result.instance_a, result.instance_b} == {"inst-a", "inst-b"}
    # Stable A/B order is alphabetical by instance_id.
    assert result.instance_a < result.instance_b

    # Bus event fired exactly once.
    assert len(seen) == 1
    assert seen[0]["match_id"] == match_id


async def test_draw_splits_elo(store):
    await _register_pair(store)
    await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="tied")
    )
    await store.enqueue_match(
        MatchInvite(instance_id="inst-b", goal="tied")
    )
    pairs = await store.pair_pending_matches()
    match_id = pairs[0][0].match_id
    assert match_id is not None

    identical_kpi = {
        "task_completed": True,
        "tasks_done": 4,
        "rounds_used": 10,
        "files_created": 2,
    }
    assert await store.submit_score(match_id, "inst-a", identical_kpi) is None
    result = await store.submit_score(match_id, "inst-b", identical_kpi)
    assert result is not None
    assert result.winner == "draw"
    # Equal starting ratings → no movement at all on a draw.
    assert result.elo_delta_a == pytest.approx(0.0)
    assert result.elo_delta_b == pytest.approx(0.0)


# ── leaderboard ────────────────────────────────────────────────────


async def test_leaderboard_sorted_after_match(store):
    await _register_pair(store)
    await store.register_instance("inst-c", "Gamma", "https://c.example")
    await store.enqueue_match(
        MatchInvite(instance_id="inst-a", goal="rank-test")
    )
    await store.enqueue_match(
        MatchInvite(instance_id="inst-b", goal="rank-test")
    )
    pairs = await store.pair_pending_matches()
    match_id = pairs[0][0].match_id
    assert match_id is not None
    await store.submit_score(
        match_id,
        "inst-a",
        {"task_completed": True, "tasks_done": 5, "rounds_used": 5},
    )
    await store.submit_score(
        match_id,
        "inst-b",
        {"task_completed": False, "tasks_done": 0, "rounds_used": 9},
    )

    board = await store.get_leaderboard()
    # All three registered instances appear.
    ids = [e.instance_id for e in board]
    assert set(ids) == {"inst-a", "inst-b", "inst-c"}
    # Sorted by rating descending.
    ratings = [e.rating for e in board]
    assert ratings == sorted(ratings, reverse=True)
    # Winner is on top.
    assert board[0].instance_id == "inst-a"
    assert board[0].wins == 1
    # Loser carries a loss.
    loser = next(e for e in board if e.instance_id == "inst-b")
    assert loser.losses == 1
    # The instance that never played stays at the default rating.
    bystander = next(e for e in board if e.instance_id == "inst-c")
    assert bystander.matches == 0
    assert bystander.rating == pytest.approx(1200.0)


async def test_leaderboard_limit_clamps(store):
    await store.register_instance("inst-a", "A", "https://a")
    await store.register_instance("inst-b", "B", "https://b")
    await store.register_instance("inst-c", "C", "https://c")
    board = await store.get_leaderboard(limit=2)
    assert len(board) == 2


# ── persistence ────────────────────────────────────────────────────


async def test_state_round_trips_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = tmp_path / "coordinator"

    s1 = CoordinatorStore(root=root)
    await s1.register_instance("inst-a", "Alpha", "https://a")
    await s1.register_instance("inst-b", "Beta", "https://b")
    await s1.enqueue_match(MatchInvite(instance_id="inst-a", goal="persist"))
    await s1.enqueue_match(MatchInvite(instance_id="inst-b", goal="persist"))
    pairs = await s1.pair_pending_matches()
    match_id = pairs[0][0].match_id
    assert match_id is not None
    await s1.submit_score(
        match_id, "inst-a", {"task_completed": True, "tasks_done": 3}
    )
    await s1.submit_score(
        match_id, "inst-b", {"task_completed": False, "tasks_done": 1}
    )

    # Fresh instance pointed at the same directory must see the same state.
    s2 = CoordinatorStore(root=root)
    instances = await s2.list_instances()
    assert {i.instance_id for i in instances} == {"inst-a", "inst-b"}
    board = await s2.get_leaderboard()
    winner = next(e for e in board if e.instance_id == "inst-a")
    assert winner.wins == 1
    assert winner.rating > 1200.0
