"""Tests for the guild raid mechanic (Phase 4-C)."""

from __future__ import annotations

import pytest

from autonoma.world.raids import GuildRaid, RaidArena, RaidPhase


def test_synergy_amplifies_damage():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="Drake",
        boss_max_hp=200,
        boss_hp=200,
        synergy_bonus=0.5,
        started_round=1,
        deadline_round=6,
    )
    # 50% synergy → 10 base = 15 damage applied.
    applied = raid.attribute("alice", 10)
    assert applied == 15
    assert raid.boss_hp == 185


def test_zero_synergy_passthrough():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="Drake",
        boss_max_hp=10,
        boss_hp=10,
        synergy_bonus=0.0,
        started_round=1,
        deadline_round=6,
    )
    assert raid.attribute("a", 4) == 4
    assert raid.boss_hp == 6


def test_boss_capped_at_zero_and_phase_victory():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="Drake",
        boss_max_hp=10,
        boss_hp=10,
        synergy_bonus=0.0,
        started_round=1,
        deadline_round=6,
    )
    raid.attribute("alice", 9)
    final = raid.attribute("bob", 5)  # would overshoot
    assert raid.boss_hp == 0
    assert final == 1  # only the remaining HP applied
    assert raid.phase is RaidPhase.VICTORY


def test_deadline_wipes_raid():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="Drake",
        boss_max_hp=100,
        boss_hp=80,
        synergy_bonus=0.2,
        started_round=1,
        deadline_round=4,
    )
    assert raid.expire_if_over(3) is False
    assert raid.expire_if_over(4) is True
    assert raid.phase is RaidPhase.WIPED


def test_reward_split_proportional_to_damage():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="Drake",
        boss_max_hp=100,
        boss_hp=100,
        synergy_bonus=0.0,
        started_round=1,
        deadline_round=6,
    )
    raid.attribute("alice", 60)
    raid.attribute("bob", 30)
    raid.attribute("carol", 10)
    split = raid.reward_split(total_xp=100)
    # Damage is 60/30/10 → reward roughly 60/30/10 (rounding goes to top)
    assert split["alice"] >= 60
    assert split["bob"] == 30
    assert split["carol"] == 10
    assert sum(split.values()) == 100


def test_reward_split_floors_at_one_xp():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="Drake",
        boss_max_hp=10_000,
        boss_hp=10_000,
        synergy_bonus=0.0,
        started_round=1,
        deadline_round=6,
    )
    raid.attribute("alice", 9_999)
    raid.attribute("bob", 1)  # tiny tap-in
    split = raid.reward_split(total_xp=10)
    assert split["bob"] >= 1
    assert sum(split.values()) == 10


def test_arena_rejects_concurrent_raids():
    arena = RaidArena()
    arena.start(
        guild_name="A",
        boss_name="x",
        boss_max_hp=50,
        synergy_bonus=0.1,
        current_round=1,
    )
    with pytest.raises(RuntimeError):
        arena.start(
            guild_name="B",
            boss_name="y",
            boss_max_hp=50,
            synergy_bonus=0.1,
            current_round=1,
        )


def test_arena_archives_completed_raids():
    arena = RaidArena()
    arena.start(
        guild_name="A",
        boss_name="x",
        boss_max_hp=10,
        synergy_bonus=0.0,
        current_round=1,
    )
    arena.contribute("alice", 10)  # one-shot kill
    arena.tick(current_round=2)
    assert arena.active is None
    assert len(arena.history()) == 1
    assert arena.history()[0].phase is RaidPhase.VICTORY


def test_arena_tick_wipes_overdue_raids():
    arena = RaidArena()
    arena.start(
        guild_name="A",
        boss_name="x",
        boss_max_hp=100,
        synergy_bonus=0.0,
        current_round=1,
        deadline_offset=2,
    )
    # No contributions; 2 rounds later the boss escapes.
    arena.tick(current_round=3)
    assert arena.active is None
    history = arena.history()
    assert history[0].phase is RaidPhase.WIPED
