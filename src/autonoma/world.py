"""World System - the living ecosystem where agents exist.

A comprehensive world simulation powering the Autonoma agent swarm:
- Deterministic personality (species, stats, traits) from hash
- True two-layer memory (private experiences + team hindsight notes)
- Directional trust & relationship graph
- XP / leveling / evolution system
- 25+ achievements across 4 tiers
- Dynamic world events with chain reactions
- Guild formation with synergy bonuses
- Gossip network for social intelligence
- Campfire mechanic for collective knowledge sharing
- Conflict resolution through structured debate
- Reputation leaderboard
- Narrative engine for story generation

Ported patterns from Claude Code:
- Deterministic identity from hash (companion "bones" / mulberry32)
- Two-layer memory with private/team scope (memdir)
- Progress tracker as performance record (XP/stamina)
- Coordinator synthesis protocol (relationship dynamics)
- Hindsight notes as accumulated lore (Confucius Code Agent)
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Agent Personality - deterministic "bones" from role hash
# ═══════════════════════════════════════════════════════════════════════════════

class Mood(str, Enum):
    HAPPY = "happy"
    FOCUSED = "focused"
    FRUSTRATED = "frustrated"
    EXCITED = "excited"
    TIRED = "tired"
    PROUD = "proud"
    WORRIED = "worried"
    CURIOUS = "curious"
    DETERMINED = "determined"
    RELAXED = "relaxed"
    INSPIRED = "inspired"
    MISCHIEVOUS = "mischievous"
    NOSTALGIC = "nostalgic"


class Trait(str, Enum):
    """Personality traits - Big Five inspired, kawaii flavored."""
    DILIGENT = "diligent"
    CREATIVE = "creative"
    FRIENDLY = "friendly"
    BOLD = "bold"
    CALM = "calm"


# ── Species & Evolution ───────────────────────────────────────────────────

SPECIES = [
    "cat", "rabbit", "fox", "owl", "bear",
    "penguin", "hamster", "dog", "panda", "duck",
]

EVOLVED_SPECIES: dict[str, dict[int, str]] = {
    "cat":     {5: "tiger", 10: "lion"},
    "rabbit":  {5: "hare", 10: "jackalope"},
    "fox":     {5: "wolf", 10: "kitsune"},
    "owl":     {5: "eagle", 10: "phoenix"},
    "bear":    {5: "grizzly", 10: "polar bear"},
    "penguin": {5: "emperor", 10: "ice dragon"},
    "hamster": {5: "chinchilla", 10: "capybara"},
    "dog":     {5: "husky", 10: "dire wolf"},
    "panda":   {5: "red panda", 10: "spirit bear"},
    "duck":    {5: "swan", 10: "thunderbird"},
}

EVOLVED_EMOJIS: dict[str, str] = {
    "tiger": "🐯", "lion": "🦁", "hare": "🐇", "jackalope": "🦌",
    "wolf": "🐺", "kitsune": "🦊", "eagle": "🦅", "phoenix": "🔥",
    "grizzly": "🐻", "polar bear": "❄", "emperor": "🐧", "ice dragon": "🐉",
    "chinchilla": "🐭", "capybara": "🦫", "husky": "🐕", "dire wolf": "🐺",
    "red panda": "🐼", "spirit bear": "✨", "swan": "🦢", "thunderbird": "⚡",
}

CATCHPHRASES = {
    "cat": ["Nyaa~!", "Purr-fect!", "Mew mew~"],
    "rabbit": ["Hop hop!", "Carrot power~!", "Boing!"],
    "fox": ["Kon kon~!", "Foxy move!", "Sly plan~"],
    "owl": ["Hoo-hoo!", "Wise choice~!", "Nocturnal vibes~"],
    "bear": ["Gao~!", "Bear hug!", "Honey time~"],
    "penguin": ["Waddle waddle~!", "Cool cool!", "Ice ice~"],
    "hamster": ["Chii~!", "Seeds seeds!", "Tiny but mighty!"],
    "dog": ["Wan wan~!", "Good boy!", "Fetch!"],
    "panda": ["Munch munch~!", "Bamboo break!", "Zzz..."],
    "duck": ["Quack quack~!", "Waddle on!", "Duck dive!"],
}

SPECIES_EMOJIS = {
    "cat": "🐱", "rabbit": "🐰", "fox": "🦊", "owl": "🦉", "bear": "🐻",
    "penguin": "🐧", "hamster": "🐹", "dog": "🐶", "panda": "🐼", "duck": "🦆",
}


def _mulberry32(seed: int) -> int:
    """Deterministic PRNG from Claude Code's companion system."""
    seed = (seed + 0x6D2B79F5) & 0xFFFFFFFF
    t = (seed ^ (seed >> 15)) * (seed | 1)
    t = (t ^ (t + (t ^ (t >> 7)) * (t | 61))) & 0xFFFFFFFF
    return (t ^ (t >> 14)) & 0xFFFFFFFF


@dataclass
class AgentBones:
    """Deterministic identity computed from role hash - never persisted."""
    species: str
    species_emoji: str
    catchphrase: str
    stats: dict[str, int]
    traits: list[Trait]
    rarity: str

    @staticmethod
    def from_role(role: str, name: str = "") -> AgentBones:
        seed_str = f"{role}:{name}:autonoma-world-v1"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)

        r1 = _mulberry32(seed)
        r2 = _mulberry32(r1)
        r3 = _mulberry32(r2)
        r4 = _mulberry32(r3)
        r5 = _mulberry32(r4)
        r6 = _mulberry32(r5)
        r7 = _mulberry32(r6)

        species = SPECIES[r1 % len(SPECIES)]
        catchphrases = CATCHPHRASES[species]
        catchphrase = catchphrases[r2 % len(catchphrases)]

        stats = {
            "debugging": (r3 % 10) + 1,
            "patience": (r4 % 10) + 1,
            "chaos": (r5 % 10) + 1,
            "wisdom": (r6 % 10) + 1,
            "speed": (r7 % 10) + 1,
        }

        all_traits = list(Trait)
        trait_scores = [(all_traits[i], list(stats.values())[i]) for i in range(5)]
        trait_scores.sort(key=lambda x: x[1], reverse=True)
        traits = [t[0] for t in trait_scores[:2]]

        total = sum(stats.values())
        if total >= 42:
            rarity = "legendary"
        elif total >= 35:
            rarity = "rare"
        elif total >= 25:
            rarity = "uncommon"
        else:
            rarity = "common"

        return AgentBones(
            species=species,
            species_emoji=SPECIES_EMOJIS[species],
            catchphrase=catchphrase,
            stats=stats,
            traits=traits,
            rarity=rarity,
        )

    def get_evolved_form(self, level: int) -> tuple[str, str]:
        """Return (evolved_species, evolved_emoji) if level threshold met."""
        evolutions = EVOLVED_SPECIES.get(self.species, {})
        current_species = self.species
        current_emoji = self.species_emoji
        for threshold in sorted(evolutions.keys()):
            if level >= threshold:
                current_species = evolutions[threshold]
                current_emoji = EVOLVED_EMOJIS.get(current_species, self.species_emoji)
        return current_species, current_emoji


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Relationship & Trust Graph
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Relationship:
    trust: float = 0.5
    familiarity: int = 0
    last_interaction: str = ""
    sentiment: str = "neutral"
    shared_tasks: int = 0
    conflicts: int = 0

    def record_interaction(self, description: str, positive: bool = True) -> None:
        self.familiarity += 1
        self.last_interaction = description
        delta = 0.1 if positive else -0.15
        self.trust = max(0.0, min(1.0, self.trust + delta))
        if self.trust > 0.7:
            self.sentiment = "positive"
        elif self.trust < 0.3:
            self.sentiment = "negative"
        else:
            self.sentiment = "neutral"

    def record_conflict(self, description: str) -> None:
        """Record a conflict/disagreement between agents."""
        self.conflicts += 1
        self.record_interaction(f"Conflict: {description}", positive=False)

    def record_collaboration(self, description: str) -> None:
        """Record successful collaboration."""
        self.shared_tasks += 1
        self.record_interaction(f"Collab: {description}", positive=True)

    @property
    def bond_level(self) -> str:
        """Kawaii bond level description."""
        if self.trust >= 0.9:
            return "soulmates ♥♥♥"
        elif self.trust >= 0.7:
            return "best friends ♥♥"
        elif self.trust >= 0.5:
            return "friends ♥"
        elif self.trust >= 0.3:
            return "acquaintances ~"
        else:
            return "rivals ✖"


class RelationshipGraph:
    def __init__(self) -> None:
        self._graph: dict[tuple[str, str], Relationship] = {}

    def get(self, from_agent: str, to_agent: str) -> Relationship:
        key = (from_agent, to_agent)
        if key not in self._graph:
            self._graph[key] = Relationship()
        return self._graph[key]

    def record(self, from_agent: str, to_agent: str, description: str, positive: bool = True) -> None:
        self.get(from_agent, to_agent).record_interaction(description, positive)

    def get_friends(self, agent: str, threshold: float = 0.7) -> list[str]:
        return [
            to for (frm, to), rel in self._graph.items()
            if frm == agent and rel.trust >= threshold
        ]

    def get_rivals(self, agent: str, threshold: float = 0.3) -> list[str]:
        return [
            to for (frm, to), rel in self._graph.items()
            if frm == agent and rel.trust < threshold and rel.familiarity > 0
        ]

    def get_summary_for(self, agent: str) -> str:
        lines = []
        for (frm, to), rel in self._graph.items():
            if frm == agent and rel.familiarity > 0:
                emoji = "♥" if rel.sentiment == "positive" else "~" if rel.sentiment == "neutral" else "✖"
                lines.append(
                    f"  {emoji} {to}: {rel.bond_level} "
                    f"(trust={rel.trust:.1f}, x{rel.familiarity})"
                )
        return "\n".join(lines) if lines else "  No relationships yet"

    def get_all_pairs(self) -> list[tuple[str, str, Relationship]]:
        return [(frm, to, rel) for (frm, to), rel in self._graph.items() if rel.familiarity > 0]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Two-Layer Memory (proper structural separation)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MemoryEntry:
    text: str
    memory_type: str  # "lesson", "success", "failure", "observation"
    round_number: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __str__(self) -> str:
        icon = {"lesson": "📝", "success": "★", "failure": "✖", "observation": "👁"}.get(self.memory_type, "•")
        return f"{icon} [R{self.round_number}] {self.text}"


@dataclass
class HindsightNote:
    """A structured lesson learned from experience - searchable by keywords.

    Ported from the Confucius Code Agent pattern: keyword-indexed notes that
    future sessions can auto-retrieve. Separate from episodic memories.
    """
    title: str
    lesson: str
    keywords: list[str]
    source_agent: str = ""
    round_number: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    upvotes: int = 0  # Other agents can upvote useful notes

    def __str__(self) -> str:
        return f"📖 [{', '.join(self.keywords)}] {self.title}: {self.lesson}"

    def matches(self, query: str) -> bool:
        """Check if this note matches a keyword query."""
        q = query.lower()
        return (
            q in self.title.lower()
            or q in self.lesson.lower()
            or any(q in kw.lower() for kw in self.keywords)
        )


class AgentMemory:
    """Two-layer memory system: private experiences + shared hindsight notes.

    Layer 1 - Private Memories: personal experiences, observations, successes, failures
    Layer 2 - Hindsight Notes: structured lessons with keywords, shareable at campfire
    """

    MAX_PRIVATE_MEMORIES = 20
    MAX_HINDSIGHT_NOTES = 15

    def __init__(self) -> None:
        # Layer 1: Private episodic memory
        self.private: list[MemoryEntry] = []
        # Layer 2: Structured hindsight notes
        self.hindsight: list[HindsightNote] = []

    def remember(self, text: str, memory_type: str = "observation", round_number: int = 0) -> None:
        """Add a private memory."""
        entry = MemoryEntry(text=text, memory_type=memory_type, round_number=round_number)
        self.private.append(entry)
        if len(self.private) > self.MAX_PRIVATE_MEMORIES:
            self.private.sort(
                key=lambda e: (e.memory_type in ("lesson", "failure"), e.round_number)
            )
            self.private = self.private[-self.MAX_PRIVATE_MEMORIES:]

    def add_hindsight(
        self,
        title: str,
        lesson: str,
        keywords: list[str],
        source_agent: str = "",
        round_number: int = 0,
    ) -> HindsightNote:
        """Add a structured hindsight note (Layer 2)."""
        note = HindsightNote(
            title=title,
            lesson=lesson,
            keywords=keywords,
            source_agent=source_agent,
            round_number=round_number,
        )
        self.hindsight.append(note)
        if len(self.hindsight) > self.MAX_HINDSIGHT_NOTES:
            # Keep most-upvoted notes
            self.hindsight.sort(key=lambda n: (n.upvotes, n.round_number))
            self.hindsight = self.hindsight[-self.MAX_HINDSIGHT_NOTES:]
        return note

    def search_hindsight(self, query: str) -> list[HindsightNote]:
        """Search hindsight notes by keyword (the retrieval pattern from Confucius)."""
        return [n for n in self.hindsight if n.matches(query)]

    def recall(self, keyword: str = "") -> list[MemoryEntry]:
        """Recall private memories, optionally filtered by keyword."""
        if not keyword:
            return self.private[-10:]
        return [e for e in self.private if keyword.lower() in e.text.lower()][-5:]

    def get_summary(self) -> str:
        """Format both layers for injection into situation report."""
        lines = []
        if self.private:
            lines.append("  [Private Memories]")
            for e in self.private[-6:]:
                lines.append(f"    {e}")
        else:
            lines.append("  No memories yet - this is a fresh start!")

        if self.hindsight:
            lines.append("  [Hindsight Notes]")
            for n in self.hindsight[-4:]:
                lines.append(f"    {n}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "private": [
                {"text": e.text, "type": e.memory_type, "round": e.round_number}
                for e in self.private
            ],
            "hindsight": [
                {
                    "title": n.title,
                    "lesson": n.lesson,
                    "keywords": n.keywords,
                    "source": n.source_agent,
                    "upvotes": n.upvotes,
                }
                for n in self.hindsight
            ],
        }

    # Backwards compatibility
    @property
    def entries(self) -> list[MemoryEntry]:
        return self.private


# ═══════════════════════════════════════════════════════════════════════════════
# 4. XP / Level / Evolution / Achievement System
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentStats:
    xp: int = 0
    level: int = 1
    tasks_completed: int = 0
    files_created: int = 0
    messages_sent: int = 0
    reviews_done: int = 0
    help_given: int = 0
    help_received: int = 0
    errors: int = 0
    tokens_used: int = 0
    rounds_active: int = 0
    gossip_shared: int = 0
    debates_won: int = 0
    campfire_stories: int = 0
    achievements: list[str] = field(default_factory=list)

    @property
    def xp_to_next_level(self) -> int:
        return self.level * 50

    @property
    def total_xp_earned(self) -> int:
        """Total XP earned across all levels."""
        # Sum of all previous level thresholds + current XP
        return sum(i * 50 for i in range(1, self.level)) + self.xp

    def add_xp(self, amount: int) -> bool:
        """Add XP and return True if leveled up."""
        self.xp += amount
        if self.xp >= self.xp_to_next_level:
            self.xp -= self.xp_to_next_level
            self.level += 1
            return True
        return False

    @property
    def title(self) -> str:
        """Kawaii title based on level."""
        if self.level >= 15:
            return "Legendary Hero"
        elif self.level >= 10:
            return "Grand Master"
        elif self.level >= 7:
            return "Elite Agent"
        elif self.level >= 5:
            return "Veteran"
        elif self.level >= 3:
            return "Journeyman"
        else:
            return "Rookie"


# ── Achievement System (4 tiers: Bronze ☆, Silver ★, Gold ★★, Diamond ★★★) ──

class AchievementTier(str, Enum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    DIAMOND = "diamond"


ACHIEVEMENTS: dict[str, dict[str, Any]] = {
    # Bronze Tier ☆
    "first_blood": {
        "title": "First Blood ☆",
        "description": "Completed first task",
        "tier": AchievementTier.BRONZE,
        "xp_reward": 10,
        "check": lambda s: s.tasks_completed >= 1,
    },
    "hello_world": {
        "title": "Hello World ☆",
        "description": "Created first file",
        "tier": AchievementTier.BRONZE,
        "xp_reward": 10,
        "check": lambda s: s.files_created >= 1,
    },
    "chatty": {
        "title": "Chatty ☆",
        "description": "Sent first message",
        "tier": AchievementTier.BRONZE,
        "xp_reward": 5,
        "check": lambda s: s.messages_sent >= 1,
    },
    "oops": {
        "title": "Oops! ☆",
        "description": "Encountered first error",
        "tier": AchievementTier.BRONZE,
        "xp_reward": 5,
        "check": lambda s: s.errors >= 1,
    },

    # Silver Tier ★
    "prolific": {
        "title": "Prolific Writer ★",
        "description": "Created 5+ files",
        "tier": AchievementTier.SILVER,
        "xp_reward": 25,
        "check": lambda s: s.files_created >= 5,
    },
    "social_butterfly": {
        "title": "Social Butterfly ★",
        "description": "Sent 10+ messages",
        "tier": AchievementTier.SILVER,
        "xp_reward": 25,
        "check": lambda s: s.messages_sent >= 10,
    },
    "helper": {
        "title": "Helpful Friend ★",
        "description": "Helped 3+ teammates",
        "tier": AchievementTier.SILVER,
        "xp_reward": 25,
        "check": lambda s: s.help_given >= 3,
    },
    "storyteller": {
        "title": "Storyteller ★",
        "description": "Shared 3+ campfire stories",
        "tier": AchievementTier.SILVER,
        "xp_reward": 20,
        "check": lambda s: s.campfire_stories >= 3,
    },
    "gossip_queen": {
        "title": "Gossip Queen ★",
        "description": "Shared 5+ gossip items",
        "tier": AchievementTier.SILVER,
        "xp_reward": 20,
        "check": lambda s: s.gossip_shared >= 5,
    },
    "resilient": {
        "title": "Resilient Spirit ★",
        "description": "Recovered from 3+ errors and completed a task",
        "tier": AchievementTier.SILVER,
        "xp_reward": 30,
        "check": lambda s: s.errors >= 3 and s.tasks_completed >= 1,
    },

    # Gold Tier ★★
    "veteran": {
        "title": "Veteran Agent ★★",
        "description": "Reached level 5",
        "tier": AchievementTier.GOLD,
        "xp_reward": 50,
        "check": lambda s: s.level >= 5,
    },
    "perfectionist": {
        "title": "Perfectionist ★★",
        "description": "Completed 10+ tasks",
        "tier": AchievementTier.GOLD,
        "xp_reward": 50,
        "check": lambda s: s.tasks_completed >= 10,
    },
    "architect": {
        "title": "Architect ★★",
        "description": "Created 15+ files",
        "tier": AchievementTier.GOLD,
        "xp_reward": 50,
        "check": lambda s: s.files_created >= 15,
    },
    "debater": {
        "title": "Silver Tongue ★★",
        "description": "Won 3+ debates",
        "tier": AchievementTier.GOLD,
        "xp_reward": 40,
        "check": lambda s: s.debates_won >= 3,
    },

    # Diamond Tier ★★★
    "legendary": {
        "title": "Legendary ★★★",
        "description": "Reached level 10",
        "tier": AchievementTier.DIAMOND,
        "xp_reward": 100,
        "check": lambda s: s.level >= 10,
    },
    "polymath": {
        "title": "Polymath ★★★",
        "description": "Completed 20+ tasks and created 20+ files",
        "tier": AchievementTier.DIAMOND,
        "xp_reward": 100,
        "check": lambda s: s.tasks_completed >= 20 and s.files_created >= 20,
    },
    "beloved": {
        "title": "Beloved ★★★",
        "description": "Sent 30+ messages and helped 10+ teammates",
        "tier": AchievementTier.DIAMOND,
        "xp_reward": 100,
        "check": lambda s: s.messages_sent >= 30 and s.help_given >= 10,
    },
}

TIER_EMOJIS = {
    AchievementTier.BRONZE: "☆",
    AchievementTier.SILVER: "★",
    AchievementTier.GOLD: "★★",
    AchievementTier.DIAMOND: "★★★",
}


def check_achievements(stats: AgentStats) -> list[str]:
    newly_earned: list[str] = []
    for ach_id, ach in ACHIEVEMENTS.items():
        if ach_id not in stats.achievements and ach["check"](stats):
            stats.achievements.append(ach_id)
            newly_earned.append(ach_id)
            stats.add_xp(ach.get("xp_reward", 0))
    return newly_earned


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Guild / Team Formation System
# ═══════════════════════════════════════════════════════════════════════════════

class GuildRole(str, Enum):
    LEADER = "leader"
    MEMBER = "member"
    MENTOR = "mentor"
    APPRENTICE = "apprentice"


@dataclass
class Guild:
    """A sub-team formed by agents working closely together."""
    name: str
    motto: str
    members: dict[str, GuildRole] = field(default_factory=dict)
    founded_round: int = 0
    total_tasks_completed: int = 0
    synergy_bonus: float = 0.0  # XP multiplier (0.0 = no bonus, 0.5 = +50%)

    @property
    def size(self) -> int:
        return len(self.members)

    def add_member(self, agent_name: str, role: GuildRole = GuildRole.MEMBER) -> None:
        self.members[agent_name] = role

    def calculate_synergy(self, relationship_graph: RelationshipGraph) -> float:
        """Calculate synergy bonus based on average mutual trust."""
        names = list(self.members.keys())
        if len(names) < 2:
            return 0.0
        total_trust = 0.0
        pairs = 0
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                total_trust += relationship_graph.get(a, b).trust
                total_trust += relationship_graph.get(b, a).trust
                pairs += 2
        avg_trust = total_trust / max(1, pairs)
        self.synergy_bonus = max(0.0, (avg_trust - 0.5) * 2)  # 0% at 0.5 trust, 100% at 1.0
        return self.synergy_bonus

    def get_banner(self) -> str:
        leader = next((n for n, r in self.members.items() if r == GuildRole.LEADER), "???")
        return (
            f"~*~ {self.name} ~*~\n"
            f'  "{self.motto}"\n'
            f"  Leader: {leader} | Members: {self.size} | "
            f"Synergy: +{self.synergy_bonus * 100:.0f}%"
        )


class GuildRegistry:
    """Manages all guilds in the world."""

    def __init__(self) -> None:
        self.guilds: dict[str, Guild] = {}

    def create(self, name: str, motto: str, leader: str, round_number: int = 0) -> Guild:
        guild = Guild(name=name, motto=motto, founded_round=round_number)
        guild.add_member(leader, GuildRole.LEADER)
        self.guilds[name] = guild
        return guild

    def get_agent_guild(self, agent_name: str) -> Guild | None:
        for guild in self.guilds.values():
            if agent_name in guild.members:
                return guild
        return None

    def auto_form_guilds(
        self, agent_names: list[str], relationship_graph: RelationshipGraph, round_number: int
    ) -> list[Guild]:
        """Automatically form guilds based on strong relationships."""
        formed: list[Guild] = []
        assigned: set[str] = set()

        # Find clusters of high-trust agents
        for name in agent_names:
            if name in assigned or self.get_agent_guild(name):
                continue
            friends = relationship_graph.get_friends(name, threshold=0.6)
            available_friends = [f for f in friends if f not in assigned and not self.get_agent_guild(f)]

            if available_friends:
                guild_name = f"Team {name[:4]}"
                motto_options = [
                    "Together we code!", "Stronger together~", "One for all!",
                    "Dream team!", "Unstoppable force~",
                ]
                motto = random.choice(motto_options)
                guild = self.create(guild_name, motto, name, round_number)
                assigned.add(name)
                for friend in available_friends[:3]:  # Max 4 per guild
                    guild.add_member(friend)
                    assigned.add(friend)
                guild.calculate_synergy(relationship_graph)
                formed.append(guild)

        return formed


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Gossip Network
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GossipItem:
    """A piece of social intelligence shared between agents."""
    about: str           # Who the gossip is about
    content: str         # What was observed
    spreader: str        # Who shared it
    original_observer: str  # Who first saw it
    round_number: int = 0
    spread_count: int = 1
    sentiment: str = "neutral"  # positive/neutral/negative

    def __str__(self) -> str:
        emoji = "♥" if self.sentiment == "positive" else "✖" if self.sentiment == "negative" else "~"
        return f"{emoji} About {self.about}: {self.content} (from {self.original_observer})"


class GossipNetwork:
    """Manages social intelligence flow between agents.

    Agents observe each other's actions and share observations.
    Gossip affects relationships and reputation.
    """

    def __init__(self) -> None:
        self.items: list[GossipItem] = []
        self._heard_by: dict[int, set[str]] = {}  # gossip_index -> set of agents who heard it

    def observe(
        self,
        observer: str,
        about: str,
        content: str,
        sentiment: str = "neutral",
        round_number: int = 0,
    ) -> GossipItem:
        """Record an observation about another agent."""
        item = GossipItem(
            about=about,
            content=content,
            spreader=observer,
            original_observer=observer,
            round_number=round_number,
            sentiment=sentiment,
        )
        idx = len(self.items)
        self.items.append(item)
        self._heard_by[idx] = {observer}
        return item

    def spread(self, spreader: str, listener: str, max_items: int = 2) -> list[GossipItem]:
        """Agent shares gossip with another agent."""
        shared: list[GossipItem] = []
        for idx, item in enumerate(self.items):
            if len(shared) >= max_items:
                break
            if spreader in self._heard_by.get(idx, set()) and listener not in self._heard_by.get(idx, set()):
                self._heard_by[idx].add(listener)
                item.spread_count += 1
                shared.append(item)
        return shared

    def get_gossip_about(self, agent_name: str) -> list[GossipItem]:
        return [g for g in self.items if g.about == agent_name]

    def get_reputation_summary(self, agent_name: str) -> str:
        """Get what others are saying about an agent."""
        gossip = self.get_gossip_about(agent_name)
        if not gossip:
            return "No gossip yet~"
        positive = sum(1 for g in gossip if g.sentiment == "positive")
        negative = sum(1 for g in gossip if g.sentiment == "negative")
        return f"♥{positive} ~{len(gossip) - positive - negative} ✖{negative} ({len(gossip)} total)"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Campfire - End-of-round knowledge sharing ritual
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CampfireStory:
    """A story shared at the campfire - becomes part of collective memory."""
    teller: str
    title: str
    content: str
    moral: str  # The lesson learned
    round_number: int = 0
    listeners: list[str] = field(default_factory=list)
    reactions: dict[str, str] = field(default_factory=dict)  # agent -> reaction emoji

    def __str__(self) -> str:
        return f"🔥 {self.teller}: \"{self.title}\" - {self.moral}"


class Campfire:
    """The campfire is where agents share stories and lessons at the end of each round.

    Stories shared here become hindsight notes for all listeners.
    """

    def __init__(self) -> None:
        self.stories: list[CampfireStory] = []
        self.is_active: bool = False

    def gather(self) -> None:
        self.is_active = True

    def dismiss(self) -> None:
        self.is_active = False

    def tell_story(
        self,
        teller: str,
        title: str,
        content: str,
        moral: str,
        listeners: list[str],
        round_number: int = 0,
    ) -> CampfireStory:
        """An agent shares a story at the campfire."""
        story = CampfireStory(
            teller=teller,
            title=title,
            content=content,
            moral=moral,
            round_number=round_number,
            listeners=listeners,
        )
        self.stories.append(story)
        return story

    def react(self, story: CampfireStory, agent: str, reaction: str) -> None:
        """An agent reacts to a story."""
        story.reactions[agent] = reaction

    def get_recent_stories(self, count: int = 5) -> list[CampfireStory]:
        return self.stories[-count:]


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Conflict Resolution / Debate System
# ═══════════════════════════════════════════════════════════════════════════════

class DebateOutcome(str, Enum):
    PROPOSER_WINS = "proposer_wins"
    OPPONENT_WINS = "opponent_wins"
    COMPROMISE = "compromise"
    UNRESOLVED = "unresolved"


@dataclass
class Debate:
    """A structured disagreement between two agents."""
    topic: str
    proposer: str
    opponent: str
    proposer_argument: str = ""
    opponent_argument: str = ""
    outcome: DebateOutcome = DebateOutcome.UNRESOLVED
    round_number: int = 0
    audience: list[str] = field(default_factory=list)
    votes: dict[str, str] = field(default_factory=dict)  # agent -> "proposer" or "opponent"

    def resolve(self) -> DebateOutcome:
        """Resolve the debate by audience votes."""
        if not self.votes:
            self.outcome = DebateOutcome.COMPROMISE
            return self.outcome

        proposer_votes = sum(1 for v in self.votes.values() if v == "proposer")
        opponent_votes = sum(1 for v in self.votes.values() if v == "opponent")

        if proposer_votes > opponent_votes:
            self.outcome = DebateOutcome.PROPOSER_WINS
        elif opponent_votes > proposer_votes:
            self.outcome = DebateOutcome.OPPONENT_WINS
        else:
            self.outcome = DebateOutcome.COMPROMISE

        return self.outcome


class DebateArena:
    """Manages debates between agents for conflict resolution."""

    def __init__(self) -> None:
        self.debates: list[Debate] = []

    def start_debate(
        self,
        topic: str,
        proposer: str,
        opponent: str,
        audience: list[str],
        round_number: int = 0,
    ) -> Debate:
        debate = Debate(
            topic=topic,
            proposer=proposer,
            opponent=opponent,
            audience=audience,
            round_number=round_number,
        )
        self.debates.append(debate)
        return debate

    def get_agent_record(self, agent: str) -> dict[str, int]:
        """Get win/loss/draw record for an agent."""
        wins = 0
        losses = 0
        draws = 0
        for d in self.debates:
            if d.outcome == DebateOutcome.UNRESOLVED:
                continue
            if d.proposer == agent:
                if d.outcome == DebateOutcome.PROPOSER_WINS:
                    wins += 1
                elif d.outcome == DebateOutcome.OPPONENT_WINS:
                    losses += 1
                else:
                    draws += 1
            elif d.opponent == agent:
                if d.outcome == DebateOutcome.OPPONENT_WINS:
                    wins += 1
                elif d.outcome == DebateOutcome.PROPOSER_WINS:
                    losses += 1
                else:
                    draws += 1
        return {"wins": wins, "losses": losses, "draws": draws}


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Reputation Leaderboard
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ReputationScore:
    """Composite reputation score for the leaderboard."""
    agent_name: str
    species: str = ""
    level: int = 1
    total_xp: int = 0
    tasks_completed: int = 0
    trust_avg: float = 0.5
    gossip_score: int = 0  # positive - negative
    debate_wins: int = 0
    achievement_count: int = 0

    @property
    def composite_score(self) -> float:
        """Weighted composite reputation score."""
        return (
            self.total_xp * 1.0
            + self.tasks_completed * 20.0
            + self.trust_avg * 100.0
            + self.gossip_score * 10.0
            + self.debate_wins * 15.0
            + self.achievement_count * 25.0
        )


class Leaderboard:
    """Maintains and renders the reputation leaderboard."""

    def __init__(self) -> None:
        self.scores: dict[str, ReputationScore] = {}

    def update(
        self,
        agent_name: str,
        stats: AgentStats,
        bones: AgentBones,
        relationship_graph: RelationshipGraph,
        gossip_network: GossipNetwork,
        debate_arena: DebateArena,
    ) -> ReputationScore:
        """Recalculate an agent's reputation score."""
        # Average trust others have in this agent
        trust_values = [
            rel.trust
            for (frm, to), rel in relationship_graph._graph.items()
            if to == agent_name and rel.familiarity > 0
        ]
        trust_avg = sum(trust_values) / max(1, len(trust_values))

        # Gossip score
        gossip = gossip_network.get_gossip_about(agent_name)
        positive = sum(1 for g in gossip if g.sentiment == "positive")
        negative = sum(1 for g in gossip if g.sentiment == "negative")

        # Debate record
        record = debate_arena.get_agent_record(agent_name)

        evolved_species, _ = bones.get_evolved_form(stats.level)

        score = ReputationScore(
            agent_name=agent_name,
            species=evolved_species,
            level=stats.level,
            total_xp=stats.total_xp_earned,
            tasks_completed=stats.tasks_completed,
            trust_avg=trust_avg,
            gossip_score=positive - negative,
            debate_wins=record["wins"],
            achievement_count=len(stats.achievements),
        )
        self.scores[agent_name] = score
        return score

    def get_ranking(self) -> list[ReputationScore]:
        """Get all agents ranked by composite score."""
        return sorted(self.scores.values(), key=lambda s: s.composite_score, reverse=True)

    def get_top(self, n: int = 5) -> list[ReputationScore]:
        return self.get_ranking()[:n]

    def render(self) -> str:
        """Render kawaii leaderboard text."""
        ranking = self.get_ranking()
        if not ranking:
            return "(^_^) No rankings yet~"

        medals = ["👑", "🥈", "🥉"]
        lines = ["~*~ REPUTATION LEADERBOARD ~*~", ""]
        for i, score in enumerate(ranking):
            medal = medals[i] if i < 3 else f"#{i + 1}"
            lines.append(
                f"  {medal} {score.agent_name} "
                f"({score.species} Lv{score.level}) "
                f"- Score: {score.composite_score:.0f}"
            )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Narrative Engine - story text generation
# ═══════════════════════════════════════════════════════════════════════════════

class NarrativeEvent(str, Enum):
    SPAWN = "spawn"
    TASK_COMPLETE = "task_complete"
    LEVEL_UP = "level_up"
    EVOLUTION = "evolution"
    ACHIEVEMENT = "achievement"
    GUILD_FORMED = "guild_formed"
    DEBATE = "debate"
    WORLD_EVENT = "world_event"
    CAMPFIRE = "campfire"
    PROJECT_COMPLETE = "project_complete"
    RELATIONSHIP_MILESTONE = "relationship_milestone"


@dataclass
class NarrativeEntry:
    """A story beat in the project's narrative."""
    event: NarrativeEvent
    text: str
    round_number: int
    agents_involved: list[str] = field(default_factory=list)
    dramatic_weight: int = 1  # 1-5 scale of narrative importance

    def __str__(self) -> str:
        return f"[R{self.round_number}] {self.text}"


class NarrativeEngine:
    """Generates narrative text for world events, creating a story from agent actions."""

    def __init__(self) -> None:
        self.chronicle: list[NarrativeEntry] = []

    def narrate_spawn(self, name: str, species: str, role: str, rarity: str, round_number: int) -> str:
        rarity_text = {
            "legendary": f"A legendary {species} emerges! The ground trembles...",
            "rare": f"A rare {species} appears from the shadows~",
            "uncommon": f"An uncommon {species} joins the adventure!",
            "common": f"A {species} wanders into the scene.",
        }
        text = rarity_text.get(rarity, f"A {species} appears!")
        text += f" {name} the {role} has arrived!"
        self._add(NarrativeEvent.SPAWN, text, round_number, [name], dramatic_weight=2 if rarity in ("rare", "legendary") else 1)
        return text

    def narrate_task_complete(self, agent: str, task_title: str, species: str, round_number: int) -> str:
        templates = [
            f"{agent} the {species} triumphantly finishes '{task_title}'!",
            f"With a flourish, {agent} completes '{task_title}'~",
            f"'{task_title}' is done! {agent} wipes their brow.",
            f"{agent} proudly presents the finished '{task_title}'!",
        ]
        text = random.choice(templates)
        self._add(NarrativeEvent.TASK_COMPLETE, text, round_number, [agent])
        return text

    def narrate_level_up(self, agent: str, new_level: int, species: str, round_number: int) -> str:
        text = f"★ {agent} the {species} reached Level {new_level}! ★"
        if new_level >= 10:
            text += " The legends speak of this moment!"
        elif new_level >= 5:
            text += " A true veteran emerges~"
        self._add(NarrativeEvent.LEVEL_UP, text, round_number, [agent], dramatic_weight=2)
        return text

    def narrate_evolution(self, agent: str, old_species: str, new_species: str, round_number: int) -> str:
        text = (
            f"~*~*~ EVOLUTION! ~*~*~ "
            f"{agent} the {old_species} evolves into a {new_species}! "
            f"A brilliant light fills the scene!"
        )
        self._add(NarrativeEvent.EVOLUTION, text, round_number, [agent], dramatic_weight=4)
        return text

    def narrate_achievement(self, agent: str, achievement_title: str, round_number: int) -> str:
        text = f"♪ Achievement Unlocked! {agent} earned '{achievement_title}' ♪"
        self._add(NarrativeEvent.ACHIEVEMENT, text, round_number, [agent])
        return text

    def narrate_guild_formed(self, guild_name: str, members: list[str], round_number: int) -> str:
        text = f"A new guild is born! '{guild_name}' ({', '.join(members)}) forms a bond of friendship!"
        self._add(NarrativeEvent.GUILD_FORMED, text, round_number, members, dramatic_weight=3)
        return text

    def narrate_debate(self, debate: Debate, round_number: int) -> str:
        if debate.outcome == DebateOutcome.PROPOSER_WINS:
            text = f"{debate.proposer} wins the debate about '{debate.topic}'! {debate.opponent} concedes gracefully."
        elif debate.outcome == DebateOutcome.OPPONENT_WINS:
            text = f"{debate.opponent} wins the debate about '{debate.topic}'! A surprising twist!"
        else:
            text = f"The debate about '{debate.topic}' ends in compromise. Both sides learn something new~"
        self._add(NarrativeEvent.DEBATE, text, round_number, [debate.proposer, debate.opponent], dramatic_weight=2)
        return text

    def narrate_campfire(self, stories_told: int, agents: list[str], round_number: int) -> str:
        text = f"🔥 The team gathers around the campfire. {stories_told} stories are shared under the stars~"
        self._add(NarrativeEvent.CAMPFIRE, text, round_number, agents, dramatic_weight=2)
        return text

    def narrate_project_complete(self, project_name: str, agents: list[str], round_number: int) -> str:
        text = (
            f"~*~*~ PROJECT COMPLETE! ~*~*~ "
            f"'{project_name}' is finished! "
            f"{len(agents)} brave agents celebrate together! "
            f"Fireworks fill the sky! ♥★♪"
        )
        self._add(NarrativeEvent.PROJECT_COMPLETE, text, round_number, agents, dramatic_weight=5)
        return text

    def narrate_relationship_milestone(
        self, agent_a: str, agent_b: str, bond_level: str, round_number: int
    ) -> str:
        text = f"{agent_a} and {agent_b} have become {bond_level}!"
        self._add(NarrativeEvent.RELATIONSHIP_MILESTONE, text, round_number, [agent_a, agent_b])
        return text

    def _add(
        self, event: NarrativeEvent, text: str, round_number: int,
        agents: list[str], dramatic_weight: int = 1,
    ) -> None:
        self.chronicle.append(NarrativeEntry(
            event=event,
            text=text,
            round_number=round_number,
            agents_involved=agents,
            dramatic_weight=dramatic_weight,
        ))

    def get_chapter(self, round_number: int) -> list[NarrativeEntry]:
        """Get all narrative entries for a specific round."""
        return [e for e in self.chronicle if e.round_number == round_number]

    def get_highlights(self, top_n: int = 10) -> list[NarrativeEntry]:
        """Get the most dramatic moments of the project."""
        return sorted(self.chronicle, key=lambda e: e.dramatic_weight, reverse=True)[:top_n]

    def render_epilogue(self) -> str:
        """Render a final narrative summary of the entire project."""
        if not self.chronicle:
            return "No story to tell yet~"

        total_rounds = max(e.round_number for e in self.chronicle) if self.chronicle else 0
        all_agents = set()
        for entry in self.chronicle:
            all_agents.update(entry.agents_involved)

        highlights = self.get_highlights(5)

        lines = [
            "╔═══════════════════════════════╗",
            "║    ~*~ THE STORY SO FAR ~*~   ║",
            "╚═══════════════════════════════╝",
            "",
            f"Over {total_rounds} rounds, {len(all_agents)} brave agents embarked on an adventure.",
            "",
            "Key moments:",
        ]
        for h in highlights:
            lines.append(f"  ★ {h.text}")

        lines.append("")
        lines.append("And so the story continues... ~*~")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. World Events (expanded with chain reactions)
# ═══════════════════════════════════════════════════════════════════════════════

class WorldEventType(str, Enum):
    REQUIREMENT_CHANGE = "requirement_change"
    BUG_DISCOVERED = "bug_discovered"
    PRIORITY_ESCALATION = "priority_escalation"
    MORALE_BOOST = "morale_boost"
    COFFEE_BREAK = "coffee_break"
    INSPIRATION = "inspiration"
    CHALLENGE = "challenge"
    THUNDERSTORM = "thunderstorm"
    LUCKY_STAR = "lucky_star"
    RIVAL_APPEARS = "rival_appears"
    MENTORSHIP = "mentorship"
    TREASURE_FOUND = "treasure_found"
    FRIENDSHIP_DAY = "friendship_day"


@dataclass
class WorldEvent:
    event_type: WorldEventType
    title: str
    description: str
    round_number: int
    affects: list[str] = field(default_factory=list)
    resolved: bool = False
    chain_event: WorldEventType | None = None  # Triggers another event

    def __str__(self) -> str:
        return f"[{self.event_type.value}] {self.title}"


EVENT_TEMPLATES = [
    {
        "type": WorldEventType.MORALE_BOOST,
        "title": "Team Spirit! ♥",
        "description": "The team is feeling great! All agents get a mood boost.",
        "min_round": 3,
    },
    {
        "type": WorldEventType.COFFEE_BREAK,
        "title": "Coffee Break! ☕",
        "description": "Everyone takes a moment to recharge. +5 XP to all.",
        "min_round": 4,
    },
    {
        "type": WorldEventType.INSPIRATION,
        "title": "Flash of Inspiration! ★",
        "description": "A creative breakthrough! Random agent gets +25 XP.",
        "min_round": 2,
    },
    {
        "type": WorldEventType.CHALLENGE,
        "title": "Pop Quiz! ♪",
        "description": "A reviewer challenges the team's work quality.",
        "min_round": 4,
    },
    {
        "type": WorldEventType.THUNDERSTORM,
        "title": "Thunderstorm! ⚡",
        "description": "Dark clouds gather! Agents with low patience get frustrated.",
        "min_round": 5,
        "chain": WorldEventType.MORALE_BOOST,  # Storm passes, morale rises
    },
    {
        "type": WorldEventType.LUCKY_STAR,
        "title": "Lucky Star! ☆",
        "description": "A shooting star! Everyone makes a wish. +10 XP to all!",
        "min_round": 6,
    },
    {
        "type": WorldEventType.RIVAL_APPEARS,
        "title": "Rival Arrives! ✖",
        "description": "A mysterious rival agent appears! Time to prove your worth.",
        "min_round": 3,
        "chain": WorldEventType.CHALLENGE,
    },
    {
        "type": WorldEventType.MENTORSHIP,
        "title": "Mentorship Moment! ♥",
        "description": "The highest-level agent offers wisdom to the newest member.",
        "min_round": 4,
    },
    {
        "type": WorldEventType.TREASURE_FOUND,
        "title": "Treasure Found! ★",
        "description": "A hidden treasure is discovered! +50 XP to the finder!",
        "min_round": 7,
    },
    {
        "type": WorldEventType.FRIENDSHIP_DAY,
        "title": "Friendship Day! ♥♥♥",
        "description": "It's Friendship Day! All trust scores get a small boost.",
        "min_round": 5,
    },
]


class WorldEventQueue:
    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.events: list[WorldEvent] = []
        self._history: list[WorldEvent] = []
        self._event_probability = 0.25
        self._chain_queue: list[WorldEventType] = []

    def maybe_generate(self, round_number: int, agent_names: list[str]) -> WorldEvent | None:
        # Process chain events first
        if self._chain_queue:
            chain_type = self._chain_queue.pop(0)
            template = next((t for t in EVENT_TEMPLATES if t["type"] == chain_type), None)
            if template:
                event = WorldEvent(
                    event_type=chain_type,
                    title=f"[Chain] {template['title']}",
                    description=template["description"],
                    round_number=round_number,
                )
                self.events.append(event)
                self._history.append(event)
                return event

        if self._rng.random() > self._event_probability:
            return None

        eligible = [t for t in EVENT_TEMPLATES if t["min_round"] <= round_number]
        if not eligible:
            return None

        template = self._rng.choice(eligible)
        affected = []
        if agent_names and self._rng.random() < 0.5:
            affected = [self._rng.choice(agent_names)]

        event = WorldEvent(
            event_type=template["type"],
            title=template["title"],
            description=template["description"],
            round_number=round_number,
            affects=affected,
            chain_event=template.get("chain"),
        )
        self.events.append(event)
        self._history.append(event)

        # Queue chain event for next round
        if event.chain_event:
            self._chain_queue.append(event.chain_event)

        return event

    def get_unresolved(self) -> list[WorldEvent]:
        return [e for e in self.events if not e.resolved]

    def resolve(self, event: WorldEvent) -> None:
        event.resolved = True
