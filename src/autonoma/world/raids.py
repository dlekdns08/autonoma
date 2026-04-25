"""Phase 4-C — Guild vs Boss raid mechanic.

Guild raids extend the single-agent ``BossArena`` into a coop encounter:
every member of an elected guild attacks the same boss and earns a
shared reward. Synergy bonus applies to every member's damage roll, so
high-trust guilds clear bosses faster than a similarly-sized random
crew of solo agents.

The module is pure data + math — it doesn't reach into ``AgentSwarm`` or
the LLM, which keeps it cheap to test in isolation. Wiring the raid
into a live swarm is the responsibility of ``swarm.py`` (the swarm
calls ``RaidArena.start`` on a periodic cadence and ``contribute`` for
each agent's per-round attack).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RaidPhase(str, Enum):
    PREPARING = "preparing"
    FIGHTING = "fighting"
    VICTORY = "victory"
    WIPED = "wiped"  # boss escaped — guild ran out of rounds


@dataclass
class RaidContribution:
    agent: str
    damage: int


@dataclass
class GuildRaid:
    """A single guild's attempt to take down a single boss."""

    raid_id: str
    guild_name: str
    boss_name: str
    boss_max_hp: int
    boss_hp: int
    synergy_bonus: float  # 0.0..1.0; multiplies each contribution
    started_round: int
    deadline_round: int
    contributions: list[RaidContribution] = field(default_factory=list)
    phase: RaidPhase = RaidPhase.FIGHTING

    @property
    def damage_taken(self) -> int:
        return self.boss_max_hp - self.boss_hp

    @property
    def damage_pct(self) -> float:
        if self.boss_max_hp <= 0:
            return 0.0
        return (self.damage_taken / self.boss_max_hp) * 100.0

    @property
    def participants(self) -> set[str]:
        return {c.agent for c in self.contributions}

    def remaining_rounds(self, current_round: int) -> int:
        return max(0, self.deadline_round - current_round)

    def attribute(self, agent: str, base_damage: int) -> int:
        """Apply synergy and record an attack. Returns final damage dealt."""
        if self.phase != RaidPhase.FIGHTING:
            return 0
        boosted = int(base_damage * (1.0 + self.synergy_bonus))
        boosted = max(1, boosted)
        applied = min(boosted, self.boss_hp)
        self.boss_hp -= applied
        self.contributions.append(RaidContribution(agent=agent, damage=applied))
        if self.boss_hp <= 0:
            self.boss_hp = 0
            self.phase = RaidPhase.VICTORY
        return applied

    def expire_if_over(self, current_round: int) -> bool:
        if self.phase != RaidPhase.FIGHTING:
            return False
        if current_round >= self.deadline_round:
            self.phase = RaidPhase.WIPED
            return True
        return False

    def reward_split(self, total_xp: int) -> dict[str, int]:
        """Distribute ``total_xp`` proportionally to damage dealt.

        Every participant gets at least 1 XP — the bottom-floor guards
        against rounding zeroing out a member who only landed one weak
        hit. Leftover XP from rounding is added to the top damage
        dealer so the totals add up exactly.
        """
        if not self.contributions:
            return {}
        totals: dict[str, int] = {}
        for c in self.contributions:
            totals[c.agent] = totals.get(c.agent, 0) + c.damage
        damage_sum = sum(totals.values()) or 1
        share: dict[str, int] = {}
        running = 0
        for agent, dmg in totals.items():
            portion = max(1, total_xp * dmg // damage_sum)
            share[agent] = portion
            running += portion
        # Reconcile rounding so we hand out exactly ``total_xp``.
        if share:
            top = max(share, key=share.get)
            share[top] += total_xp - running
        return share


class RaidArena:
    """Spawns guild raids and tracks the active one.

    A single concurrent raid is plenty — the goal is a centerpiece,
    not constant background noise. Future expansion can lift this
    cap by switching ``self._active`` to a list and indexing by guild.
    """

    DEFAULT_REWARD_XP: int = 200
    DEFAULT_DEADLINE_OFFSET: int = 5  # rounds the guild has to clear

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._active: GuildRaid | None = None
        self._archive: list[GuildRaid] = []

    @property
    def active(self) -> GuildRaid | None:
        return self._active

    def start(
        self,
        *,
        guild_name: str,
        boss_name: str,
        boss_max_hp: int,
        synergy_bonus: float,
        current_round: int,
        deadline_offset: int | None = None,
        raid_id: str | None = None,
    ) -> GuildRaid:
        if self._active is not None and self._active.phase == RaidPhase.FIGHTING:
            raise RuntimeError(
                f"raid already in progress for guild {self._active.guild_name}"
            )
        offset = deadline_offset or self.DEFAULT_DEADLINE_OFFSET
        rid = raid_id or f"raid-{current_round}-{guild_name}"
        raid = GuildRaid(
            raid_id=rid,
            guild_name=guild_name,
            boss_name=boss_name,
            boss_max_hp=int(boss_max_hp),
            boss_hp=int(boss_max_hp),
            synergy_bonus=max(0.0, min(1.0, float(synergy_bonus))),
            started_round=current_round,
            deadline_round=current_round + offset,
        )
        self._active = raid
        return raid

    def contribute(self, agent: str, base_damage: int) -> int:
        if self._active is None or self._active.phase != RaidPhase.FIGHTING:
            return 0
        applied = self._active.attribute(agent, base_damage)
        if self._active.phase == RaidPhase.VICTORY:
            self._archive.append(self._active)
        return applied

    def tick(self, current_round: int) -> bool:
        """Run end-of-round bookkeeping. Returns True if a raid resolved."""
        raid = self._active
        if raid is None:
            return False
        if raid.phase == RaidPhase.VICTORY:
            self._active = None
            return True
        if raid.expire_if_over(current_round):
            self._archive.append(raid)
            self._active = None
            return True
        return False

    def history(self) -> list[GuildRaid]:
        return list(self._archive)
