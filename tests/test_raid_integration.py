"""Integration: when a guild fights a boss, the raid path emits
victory + reward-split events."""

from __future__ import annotations

import pytest

from autonoma.event_bus import bus
from autonoma.world.raids import GuildRaid, RaidArena, RaidPhase


def test_record_does_not_amplify_damage():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="x",
        boss_max_hp=100,
        boss_hp=100,
        synergy_bonus=0.5,  # would multiply ``attribute`` by 1.5
        started_round=1,
        deadline_round=6,
    )
    applied = raid.record("alice", 20)
    assert applied == 20  # NOT 30 — record bypasses synergy
    assert raid.boss_hp == 80


def test_record_caps_at_remaining_hp():
    raid = GuildRaid(
        raid_id="r1",
        guild_name="A",
        boss_name="x",
        boss_max_hp=10,
        boss_hp=10,
        synergy_bonus=0.0,
        started_round=1,
        deadline_round=6,
    )
    raid.record("alice", 8)
    last = raid.record("bob", 100)
    assert last == 2  # only the remaining HP applied
    assert raid.boss_hp == 0
    assert raid.phase is RaidPhase.VICTORY


@pytest.mark.asyncio
async def test_swarm_emits_raid_victory_when_guild_clears_boss():
    """End-to-end-ish: build a swarm with a 2-member guild, spawn a
    boss, manually drain HP via the boss arena, verify raid.victory
    fires with non-empty split."""
    from autonoma.agents.swarm import AgentSwarm
    from autonoma.harness.policy import HarnessPolicyContent
    from autonoma.world.personality import AgentBones, Mood

    swarm = AgentSwarm(policy=HarnessPolicyContent())
    swarm._round = 10
    # Bypass the LLM by injecting two fake agents into the registry.
    # We only need ``stats``, ``bones``, and a name-keyed entry.

    class _FakeAgent:
        def __init__(self, name: str) -> None:
            self.name = name
            self.bones = AgentBones.from_role("coder", name=name)
            self.persona = type("P", (), {"name": name})()

            class _Stats:
                level = 5
                xp = 0

                def add_xp(self, n: int) -> None:
                    self.xp += n

            self.stats = _Stats()
            self.mood = Mood.HAPPY

        async def _set_mood(self, m):
            self.mood = m

    swarm.agents["alice"] = _FakeAgent("alice")  # type: ignore[assignment]
    swarm.agents["bob"] = _FakeAgent("bob")  # type: ignore[assignment]

    # Form a guild with both members so synergy>0.
    guild = swarm.guilds.create("DragonSlayers", "rawr", "alice", round_number=10)
    guild.add_member("bob")
    swarm.relationships.get("alice", "bob").trust = 1.0
    swarm.relationships.get("bob", "alice").trust = 1.0
    guild.calculate_synergy(swarm.relationships)

    captured: list[tuple[str, dict]] = []

    async def listener(**data):
        captured.append(("victory", data))

    bus.on("raid.victory", listener)
    try:
        await swarm._check_boss_fight(["alice", "bob"])
        # _check_boss_fight may or may not spawn a boss this round; if
        # it didn't, force one through and re-run the fight.
        if swarm.boss_arena.current_boss is None:
            from autonoma.world import BossAgent

            import random as _r
            swarm.boss_arena.current_boss = BossAgent.generate(10, 5, _r.Random(1))
            swarm.boss_arena.current_boss.hp = 1  # one-tap kill
            swarm.boss_arena.current_boss.max_hp = 1
            from autonoma.world import BossPhase

            swarm.boss_arena.current_boss.phase = BossPhase.FIGHTING
            # Manually start a raid and record a hit so we can call the
            # finalizer through the public route the swarm uses.
            await swarm._maybe_start_raid(swarm.boss_arena.current_boss)
            swarm.raid_arena.active.record("alice", 1)
            swarm.boss_arena.current_boss.phase = BossPhase.DEFEATED
            await swarm._finalize_raid(base_xp=100, defeated=True)
    finally:
        bus.off("raid.victory", listener)

    victories = [c for c in captured if c[0] == "victory"]
    assert victories, "expected at least one raid.victory event"
    payload = victories[-1][1]
    assert payload["guild"] == "DragonSlayers"
    # Bonus pool = base_xp (100) * synergy. Synergy = 1.0 (max trust).
    assert payload["bonus_pool"] >= 1
    # The split must include alice (the only contributor).
    assert "alice" in payload["split"]
