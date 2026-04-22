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

import asyncio
import hashlib
import logging
import random
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _fire_event(event_name: str, **kwargs: Any) -> None:
    """Fire-and-forget bus.emit from sync code.

    Uses asyncio.get_event_loop().create_task() when a running loop is
    available.  Silently skips emission when there is no loop (tests,
    CLI without an event loop).  Import is deferred to avoid a circular
    import between world.py and event_bus.py at module load time.
    """
    try:
        from autonoma.event_bus import bus  # noqa: PLC0415
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(bus.emit(event_name, **kwargs))
    except Exception as _exc:
        logger.debug(f"[world] Could not emit '{event_name}': {_exc}")


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


# ── Mood Contagion ───────────────────────────────────────────────────────────
# Maps mood names to contagion strength. Moods not present here have 0 strength.
# Probability that a given nearby agent is affected = strength * 0.5.

MOOD_CONTAGION_STRENGTH: dict[str, float] = {
    "excited": 0.3,
    "frustrated": 0.4,
    "happy": 0.2,
    "tired": 0.1,
    "inspired": 0.25,
    "mischievous": 0.2,
    "worried": 0.15,
}


def apply_mood_contagion(
    source_mood: "Mood",
    agents: list,
    source_agent_name: str,
    rng: random.Random,
) -> list[tuple[str, "Mood"]]:
    """Spread ``source_mood`` from one agent to nearby agents probabilistically.

    For each agent in ``agents`` that is not ``source_agent_name``, the
    probability of being affected is ``contagion_strength * 0.5``.

    Returns a list of ``(agent_name, new_mood)`` pairs for every agent
    whose mood changed as a result of contagion.
    """
    strength = MOOD_CONTAGION_STRENGTH.get(source_mood.value, 0.0)
    if strength == 0.0:
        return []

    affected: list[tuple[str, "Mood"]] = []
    prob = strength * 0.5
    for agent in agents:
        name = getattr(agent, "name", None)
        if name is None or name == source_agent_name:
            continue
        if rng.random() < prob:
            affected.append((name, source_mood))
    return affected


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
        rel = self.get(from_agent, to_agent)
        rel.record_interaction(description, positive)
        _fire_event(
            "relationship.update",
            from_agent=from_agent,
            to_agent=to_agent,
            description=description,
            positive=positive,
            trust=rel.trust,
        )

    def decay_all(self, decay_rate: float = 0.02) -> None:
        """Reduce all trust values toward 0 by ``decay_rate * current_trust``.

        Called once per round so long-dormant relationships slowly fade.
        Trust of 0.8 → 0.784 (never goes negative, never goes below 0).
        """
        for rel in self._graph.values():
            if rel.trust > 0.0:
                rel.trust = max(0.0, rel.trust - decay_rate * rel.trust)
            elif rel.trust < 0.0:
                rel.trust = min(0.0, rel.trust - decay_rate * rel.trust)

    def get_strong_pairs(self, threshold: float = 0.7) -> list[tuple[str, str]]:
        """Return all (from, to) pairs whose trust is above ``threshold``.

        These are "squad bonus" pairs — agents that trust each other enough
        to earn collaborative perks.
        """
        return [
            (frm, to)
            for (frm, to), rel in self._graph.items()
            if rel.trust >= threshold
        ]

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

    # Bounded summary cache: more than this many distinct
    # (version, formatter, limit) keys are unlikely in practice.
    _SUMMARY_CACHE_MAX = 8

    def __init__(self) -> None:
        # Layer 1: Private episodic memory
        self.private: list[MemoryEntry] = []
        # Layer 2: Structured hindsight notes
        self.hindsight: list[HindsightNote] = []
        # Guards `self.private` against concurrent mutation+iteration.
        # `remember` is a sync method called from within async coroutines
        # (agents/base.py, agents/swarm.py); readers like `get_summary`,
        # `recall`, `to_dict` and the `entries` property also touch the
        # same list from sync contexts. Using a threading.Lock is the
        # conservative choice that covers both sync and any future cross-
        # thread callers without forcing the API to become async.
        self._private_lock = threading.Lock()
        # Summary cache — keyed by (memory_version, formatter-id, limit).
        # Strategies today are cheap Python, but the prompt path is LLM-
        # hot and future strategies may be LLM-backed; caching keeps the
        # per-round cost O(1) even if the formatter grows expensive.
        self._summary_cache: OrderedDict[tuple, str] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._memory_version: int = 0

    def remember(self, text: str, memory_type: str = "observation", round_number: int = 0) -> None:
        """Add a private memory."""
        entry = MemoryEntry(text=text, memory_type=memory_type, round_number=round_number)
        with self._private_lock:
            self.private.append(entry)
            if len(self.private) > self.MAX_PRIVATE_MEMORIES:
                self.private.sort(
                    key=lambda e: (e.memory_type in ("lesson", "failure"), e.round_number)
                )
                self.private = self.private[-self.MAX_PRIVATE_MEMORIES:]
            self._memory_version += 1
        with self._cache_lock:
            self._summary_cache.clear()

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
        with self._private_lock:
            self._memory_version += 1
        with self._cache_lock:
            self._summary_cache.clear()
        return note

    def search_hindsight(self, query: str) -> list[HindsightNote]:
        """Search hindsight notes by keyword (the retrieval pattern from Confucius)."""
        return [n for n in self.hindsight if n.matches(query)]

    def recall(self, keyword: str = "") -> list[MemoryEntry]:
        """Recall private memories, optionally filtered by keyword."""
        with self._private_lock:
            if not keyword:
                return self.private[-10:]
            return [e for e in self.private if keyword.lower() in e.text.lower()][-5:]

    def get_summary(self, private_formatter=None, private_limit: int = 6) -> str:
        """Format both layers for injection into situation report.

        ``private_formatter`` is an optional ``(entries, limit) -> list[str]``
        callable from ``autonoma.harness.memory_strategies``. Passing ``None``
        preserves the pre-harness default (last 6 verbatim) so direct
        callers (tests, status dumps) don't need to know about policy.

        Cached by ``(memory_version, formatter-id, limit)``. Any
        ``remember``/``add_hindsight`` write invalidates the cache, so
        stale reads are not possible.
        """
        with self._private_lock:
            private_snapshot = list(self.private)
            version = self._memory_version

        cache_key = (version, id(private_formatter), private_limit)
        with self._cache_lock:
            cached = self._summary_cache.get(cache_key)
            if cached is not None:
                self._summary_cache.move_to_end(cache_key)
                return cached

        lines = []
        if private_snapshot:
            lines.append("  [Private Memories]")
            if private_formatter is None:
                lines.extend(f"    {e}" for e in private_snapshot[-private_limit:])
            else:
                lines.extend(private_formatter(private_snapshot, private_limit))
        else:
            lines.append("  No memories yet - this is a fresh start!")

        if self.hindsight:
            lines.append("  [Hindsight Notes]")
            for n in self.hindsight[-4:]:
                lines.append(f"    {n}")

        result = "\n".join(lines)
        with self._cache_lock:
            self._summary_cache[cache_key] = result
            while len(self._summary_cache) > self._SUMMARY_CACHE_MAX:
                self._summary_cache.popitem(last=False)
        return result

    def to_dict(self) -> dict:
        with self._private_lock:
            private_dump = [
                {"text": e.text, "type": e.memory_type, "round": e.round_number}
                for e in self.private
            ]
        return {
            "private": private_dump,
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
        # Return a snapshot so callers iterating outside the lock don't
        # observe mid-mutation state.
        with self._private_lock:
            return list(self.private)


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


def get_tier_progress(stats: AgentStats) -> dict[str, dict[str, int]]:
    """Return earned vs. total achievement counts broken down by tier.

    Example return value::

        {
            "bronze":  {"total": 4, "earned": 3},
            "silver":  {"total": 6, "earned": 1},
            "gold":    {"total": 4, "earned": 0},
            "diamond": {"total": 3, "earned": 0},
        }
    """
    progress: dict[str, dict[str, int]] = {}
    for tier in AchievementTier:
        tier_achs = [aid for aid, a in ACHIEVEMENTS.items() if a.get("tier") == tier]
        earned = [aid for aid in tier_achs if aid in stats.achievements]
        progress[tier.value] = {"total": len(tier_achs), "earned": len(earned)}
    return progress


def check_achievements(stats: AgentStats, agent_name: str = "") -> list[str]:
    """Check which achievements are newly earned and award XP.

    Double-call safety: `stats.achievements` is mutated in-place before
    this function returns, so a subsequent call on the **same** stats
    object (e.g. from both _update_world_stats in swarm.py and
    _check_and_emit_achievements in base.py) will find each ach_id
    already present and skip it.  This prevents double-XP as long as
    both callers share the same AgentStats instance (which they do —
    agent.stats is a single object per agent).

    When ``agent_name`` is provided, emits ``achievement.tier_complete``
    if completing a newly-earned achievement finishes an entire tier.
    """
    newly_earned: list[str] = []
    for ach_id, ach in ACHIEVEMENTS.items():
        # Guard: skip achievements already recorded on this stats object.
        if ach_id not in stats.achievements and ach["check"](stats):
            stats.achievements.append(ach_id)
            newly_earned.append(ach_id)
            stats.add_xp(ach.get("xp_reward", 0))

    # After awarding all newly earned achievements, check whether any tier
    # was just completed for the first time and emit a tier_complete event.
    if agent_name and newly_earned:
        for tier in AchievementTier:
            tier_achs = [aid for aid, a in ACHIEVEMENTS.items() if a.get("tier") == tier]
            if not tier_achs:
                continue
            all_earned = all(aid in stats.achievements for aid in tier_achs)
            # Only fire when THIS call was the one that completed the tier
            # (i.e. at least one of the newly earned belongs to this tier).
            tier_newly = [aid for aid in newly_earned if ACHIEVEMENTS[aid].get("tier") == tier]
            if all_earned and tier_newly:
                _fire_event(
                    "achievement.tier_complete",
                    agent=agent_name,
                    tier=tier.value,
                    tier_achievements=tier_achs,
                )

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
            f"~*~*~ 프로젝트 완료! ~*~*~ "
            f"'{project_name}' 프로젝트가 마무리되었습니다! "
            f"{len(agents)}명의 용감한 에이전트들이 함께 축하합니다! "
            f"불꽃놀이가 밤하늘을 수놓습니다! ♥★♪"
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
        """Render a final narrative summary of the entire project (in Korean)."""
        if not self.chronicle:
            return "아직 들려드릴 이야기가 없습니다~"

        total_rounds = max(e.round_number for e in self.chronicle) if self.chronicle else 0
        all_agents = set()
        for entry in self.chronicle:
            all_agents.update(entry.agents_involved)

        highlights = self.get_highlights(5)

        lines = [
            "╔═══════════════════════════════╗",
            "║    ~*~ 지금까지의 이야기 ~*~  ║",
            "╚═══════════════════════════════╝",
            "",
            f"총 {total_rounds} 라운드에 걸쳐, {len(all_agents)}명의 용감한 에이전트들이 모험을 떠났습니다.",
            "",
            "핵심 순간들:",
        ]
        for h in highlights:
            lines.append(f"  ★ {h.text}")

        lines.append("")
        lines.append("그리고 이야기는 계속됩니다... ~*~")
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


@dataclass
class WorldEventLedgerEntry:
    """Immutable record of a processed world event."""
    event_type: str
    title: str
    description: str
    round_number: int
    triggered_by: str  # agent name or "system"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class WorldEventLedger(WorldEventQueue):
    """WorldEventQueue extended with an append-only event ledger.

    Every event that is generated is also written to an in-memory ledger
    so callers can query history by round, type, or recency.
    """

    def __init__(self, seed: int = 42) -> None:
        super().__init__(seed=seed)
        self._ledger: list[WorldEventLedgerEntry] = []

    def _record_ledger(
        self, event: WorldEvent, triggered_by: str = "system"
    ) -> WorldEventLedgerEntry:
        entry = WorldEventLedgerEntry(
            event_type=event.event_type.value,
            title=event.title,
            description=event.description,
            round_number=event.round_number,
            triggered_by=triggered_by,
        )
        self._ledger.append(entry)
        _fire_event(
            "world.event_ledger_entry",
            event_type=entry.event_type,
            title=entry.title,
            description=entry.description,
            round=entry.round_number,
            triggered_by=entry.triggered_by,
            timestamp=entry.timestamp,
        )
        return entry

    def maybe_generate(
        self,
        round_number: int,
        agent_names: list[str],
        triggered_by: str = "system",
    ) -> WorldEvent | None:  # type: ignore[override]
        event = super().maybe_generate(round_number, agent_names)
        if event is not None:
            self._record_ledger(event, triggered_by=triggered_by)
        return event

    def get_ledger(self, limit: int = 50) -> list[WorldEventLedgerEntry]:
        """Return the most recent ``limit`` ledger entries (newest last)."""
        return self._ledger[-limit:]


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Day/Night Cycle & Seasonal Weather
# ═══════════════════════════════════════════════════════════════════════════════

class TimeOfDay(str, Enum):
    DAWN = "dawn"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class Season(str, Enum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class Weather(str, Enum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    STORMY = "stormy"
    SNOWY = "snowy"
    WINDY = "windy"
    STARRY = "starry"
    FOGGY = "foggy"


TIME_EMOJIS = {
    TimeOfDay.DAWN: "🌅",
    TimeOfDay.MORNING: "☀",
    TimeOfDay.AFTERNOON: "🌤",
    TimeOfDay.EVENING: "🌇",
    TimeOfDay.NIGHT: "🌙",
}

WEATHER_EMOJIS = {
    Weather.SUNNY: "☀",
    Weather.CLOUDY: "☁",
    Weather.RAINY: "🌧",
    Weather.STORMY: "⛈",
    Weather.SNOWY: "❄",
    Weather.WINDY: "🍃",
    Weather.STARRY: "✦",
    Weather.FOGGY: "🌫",
}

SEASON_EMOJIS = {
    Season.SPRING: "🌸",
    Season.SUMMER: "🌻",
    Season.AUTUMN: "🍂",
    Season.WINTER: "❄",
}

# Weather probabilities per season
SEASON_WEATHER: dict[Season, list[tuple[Weather, float]]] = {
    Season.SPRING: [
        (Weather.SUNNY, 0.3), (Weather.CLOUDY, 0.2), (Weather.RAINY, 0.3),
        (Weather.WINDY, 0.15), (Weather.FOGGY, 0.05),
    ],
    Season.SUMMER: [
        (Weather.SUNNY, 0.5), (Weather.CLOUDY, 0.15), (Weather.STORMY, 0.15),
        (Weather.WINDY, 0.1), (Weather.RAINY, 0.1),
    ],
    Season.AUTUMN: [
        (Weather.CLOUDY, 0.3), (Weather.RAINY, 0.25), (Weather.WINDY, 0.2),
        (Weather.FOGGY, 0.15), (Weather.SUNNY, 0.1),
    ],
    Season.WINTER: [
        (Weather.SNOWY, 0.35), (Weather.CLOUDY, 0.2), (Weather.STORMY, 0.15),
        (Weather.WINDY, 0.15), (Weather.SUNNY, 0.1), (Weather.FOGGY, 0.05),
    ],
}


class WorldClock:
    """Tracks the passage of time in the agent world."""

    TIMES = [TimeOfDay.DAWN, TimeOfDay.MORNING, TimeOfDay.AFTERNOON, TimeOfDay.EVENING, TimeOfDay.NIGHT]
    SEASONS = [Season.SPRING, Season.SUMMER, Season.AUTUMN, Season.WINTER]

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.time_of_day: TimeOfDay = TimeOfDay.MORNING
        self.season: Season = Season.SPRING
        self.weather: Weather = Weather.SUNNY
        self.day: int = 1

    def tick(self, round_number: int) -> dict[str, str]:
        """Advance the clock. Returns dict of what changed."""
        changes: dict[str, str] = {}

        # Time advances every round (5 time slots per day)
        time_idx = round_number % len(self.TIMES)
        new_time = self.TIMES[time_idx]
        if new_time != self.time_of_day:
            changes["time"] = new_time.value
            self.time_of_day = new_time

        # New day every 5 rounds
        new_day = (round_number // len(self.TIMES)) + 1
        if new_day != self.day:
            self.day = new_day
            changes["day"] = str(self.day)
            # Weather changes each day
            self._roll_weather()
            changes["weather"] = self.weather.value

        # Season changes every 20 rounds (4 days per season)
        season_idx = ((round_number - 1) // 20) % len(self.SEASONS)
        new_season = self.SEASONS[season_idx]
        if new_season != self.season:
            self.season = new_season
            changes["season"] = new_season.value
            self._roll_weather()
            changes["weather"] = self.weather.value

        return changes

    def _roll_weather(self) -> None:
        """Roll weather based on season probabilities."""
        weather_table = SEASON_WEATHER[self.season]
        roll = self._rng.random()
        cumulative = 0.0
        for weather, prob in weather_table:
            cumulative += prob
            if roll <= cumulative:
                self.weather = weather
                return
        self.weather = weather_table[0][0]

    @property
    def is_night(self) -> bool:
        return self.time_of_day in (TimeOfDay.NIGHT, TimeOfDay.EVENING)

    @property
    def sky_line(self) -> str:
        """Kawaii sky description for TUI."""
        t_emoji = TIME_EMOJIS[self.time_of_day]
        w_emoji = WEATHER_EMOJIS[self.weather]
        s_emoji = SEASON_EMOJIS[self.season]
        return f"{t_emoji} {self.time_of_day.value} {w_emoji} {self.weather.value} {s_emoji} {self.season.value} — Day {self.day}"

    def get_mood_modifier(self) -> Mood | None:
        """Some weather/time combos affect agent mood."""
        if self.weather == Weather.STORMY:
            return Mood.WORRIED
        if self.weather == Weather.SUNNY and self.time_of_day == TimeOfDay.MORNING:
            return Mood.HAPPY
        if self.time_of_day == TimeOfDay.NIGHT:
            return Mood.RELAXED
        if self.weather == Weather.SNOWY:
            return Mood.CURIOUS
        return None

    def get_xp_modifier(self) -> float:
        """Weather can affect XP gains."""
        if self.weather == Weather.SUNNY:
            return 1.1  # +10%
        if self.weather == Weather.STORMY:
            return 0.9  # -10%
        if self.time_of_day == TimeOfDay.NIGHT:
            return 1.15  # Night owl bonus
        return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Agent Dreams
# ═══════════════════════════════════════════════════════════════════════════════

DREAM_TEMPLATES = {
    "success": [
        "dreamed of climbing a mountain of {item} and planting a flag at the top",
        "had a vision of a golden {item} floating above the clouds",
        "dreamed of racing through fields of code, every line compiling perfectly",
        "saw a shimmering portal made of {item}s leading to a world of pure logic",
    ],
    "failure": [
        "had a nightmare about an infinite loop of {item}s chasing them",
        "dreamed of falling into a bottomless merge conflict",
        "saw a giant {item} blocking the only exit from a maze of bugs",
        "dreamed that every file they created turned into {item}",
    ],
    "observation": [
        "dreamed of floating through a starry sky with {agent}",
        "had a peaceful dream about a library made entirely of {item}",
        "dreamed of a campfire where {agent} told the best story ever",
        "saw a garden where each flower was a different {item}",
    ],
    "lesson": [
        "had a profound dream where a wise {species} taught them about {item}",
        "dreamed of discovering an ancient scroll about the secret of {item}",
        "saw themselves as a legendary {species} solving the ultimate {item} puzzle",
        "dreamed of a mirror that showed their future self mastering {item}",
    ],
}

DREAM_ITEMS = [
    "semicolons", "brackets", "functions", "variables", "commits",
    "pull requests", "tests", "databases", "APIs", "algorithms",
    "butterflies", "stars", "rainbows", "crystals", "cookies",
]


@dataclass
class Dream:
    """A dream that an agent experiences at night."""
    dreamer: str
    content: str
    dream_type: str  # "prophetic", "nightmare", "peaceful", "surreal"
    round_number: int
    bonus_effect: str = ""  # Effect applied next round
    bonus_xp: int = 0
    bonus_mood: Mood | None = None

    def __str__(self) -> str:
        icons = {"prophetic": "🔮", "nightmare": "👻", "peaceful": "🌙", "surreal": "🌀"}
        return f"{icons.get(self.dream_type, '💤')} {self.dreamer}: {self.content}"


class DreamEngine:
    """Generates dreams for agents based on their memories and experiences."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.dreams: list[Dream] = []

    def generate_dream(
        self,
        agent_name: str,
        species: str,
        memories: list[MemoryEntry],
        mood: Mood,
        relationships: list[str],
        round_number: int,
    ) -> Dream:
        """Generate a dream based on agent's state and memories."""
        # Pick a dream type based on mood
        if mood in (Mood.FRUSTRATED, Mood.WORRIED):
            dream_type = "nightmare"
        elif mood in (Mood.HAPPY, Mood.RELAXED, Mood.PROUD):
            dream_type = "peaceful"
        elif mood in (Mood.CURIOUS, Mood.INSPIRED):
            dream_type = "prophetic"
        else:
            dream_type = "surreal"

        # Pick template based on recent memory types
        recent_types = [m.memory_type for m in memories[-3:]] if memories else ["observation"]
        dominant_type = max(set(recent_types), key=recent_types.count)

        templates = DREAM_TEMPLATES.get(dominant_type, DREAM_TEMPLATES["observation"])
        template = self._rng.choice(templates)

        item = self._rng.choice(DREAM_ITEMS)
        agent_ref = self._rng.choice(relationships) if relationships else "a mysterious stranger"

        content = template.format(item=item, agent=agent_ref, species=species)

        # Dream effects
        bonus_xp = 0
        bonus_mood = None
        bonus_effect = ""

        if dream_type == "prophetic":
            bonus_xp = 15
            bonus_mood = Mood.INSPIRED
            bonus_effect = "Prophetic vision: +15 XP bonus!"
        elif dream_type == "nightmare":
            bonus_mood = Mood.DETERMINED
            bonus_effect = "Nightmare fuel: extra determination!"
        elif dream_type == "peaceful":
            bonus_xp = 5
            bonus_mood = Mood.RELAXED
            bonus_effect = "Restful sleep: +5 XP"
        else:  # surreal
            bonus_xp = 10
            bonus_effect = "Surreal insight: +10 XP"

        dream = Dream(
            dreamer=agent_name,
            content=content,
            dream_type=dream_type,
            round_number=round_number,
            bonus_effect=bonus_effect,
            bonus_xp=bonus_xp,
            bonus_mood=bonus_mood,
        )
        self.dreams.append(dream)
        return dream

    def get_recent_dreams(self, agent_name: str, count: int = 3) -> list[Dream]:
        return [d for d in self.dreams if d.dreamer == agent_name][-count:]


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Agent Diary
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DiaryEntry:
    """A personal diary entry written by an agent."""
    round_number: int
    mood: Mood
    content: str
    weather: str = ""
    time_of_day: str = ""

    def __str__(self) -> str:
        mood_faces = {
            "happy": "(^w^)", "focused": "(>_<)", "frustrated": "(>.<)",
            "excited": "(*^*)", "tired": "(-_-)", "proud": "(^_~)",
            "worried": "(o_o)", "curious": "(?.?)", "determined": "(!_!)",
            "relaxed": "(~_~)", "inspired": "(!!)", "mischievous": "(>w<)",
            "nostalgic": "(._.):",
        }
        face = mood_faces.get(self.mood.value, "(._.)") if isinstance(self.mood, Mood) else "(._. )"
        return f"Day R{self.round_number} {face}: {self.content}"


DIARY_TEMPLATES = {
    "task_complete": [
        "Finished {task}! Feeling accomplished~",
        "Another task down: {task}. I'm getting better at this!",
        "Crushed {task} today. {catchphrase}",
    ],
    "new_friend": [
        "Made a new friend today: {agent}! They seem nice~",
        "{agent} and I are getting along great!",
        "Bonding with {agent} over shared work. Good vibes~",
    ],
    "error": [
        "Had a rough time today... errors everywhere (>.<)",
        "Things went wrong, but I'll bounce back tomorrow!",
        "Bugs bugs bugs. Tomorrow will be better.",
    ],
    "idle": [
        "Quiet day today. Watched the {weather} outside.",
        "Not much happened. Just vibing in the {time}~",
        "Peaceful {time}. Sometimes doing nothing is nice.",
    ],
    "dream_reflection": [
        "Last night's dream was wild... something about {dream}",
        "Still thinking about that dream with {dream}",
        "The dream gave me an idea about {dream}!",
    ],
    "campfire": [
        "Campfire night! Heard amazing stories from the team.",
        "Shared my story at the campfire. Everyone listened~",
        "The campfire was so warm tonight. I love this team.",
    ],
    "achievement": [
        "ACHIEVEMENT UNLOCKED! {achievement}! I can't believe it!",
        "Finally earned {achievement}! All that work paid off~",
    ],
    "guild": [
        "Joined {guild}! Excited to work with this crew~",
        "Our guild {guild} is the best! Together we're unstoppable!",
    ],
}


class AgentDiary:
    """Personal diary for each agent - generates entries from events."""

    MAX_ENTRIES = 30

    def __init__(self, agent_name: str, species: str, catchphrase: str) -> None:
        self.agent_name = agent_name
        self.species = species
        self.catchphrase = catchphrase
        self.entries: list[DiaryEntry] = []

    def write(
        self,
        event_type: str,
        mood: Mood,
        round_number: int,
        weather: str = "",
        time_of_day: str = "",
        **kwargs: str,
    ) -> DiaryEntry:
        """Auto-generate a diary entry from an event."""
        templates = DIARY_TEMPLATES.get(event_type, DIARY_TEMPLATES["idle"])
        template = random.choice(templates)

        # Fill in template variables
        content = template.format(
            catchphrase=self.catchphrase,
            weather=weather or "sky",
            time=time_of_day or "day",
            **kwargs,
        )

        entry = DiaryEntry(
            round_number=round_number,
            mood=mood,
            content=content,
            weather=weather,
            time_of_day=time_of_day,
        )
        self.entries.append(entry)
        if len(self.entries) > self.MAX_ENTRIES:
            self.entries = self.entries[-self.MAX_ENTRIES:]
        return entry

    def get_memoir(self) -> str:
        """Generate a complete memoir from all diary entries."""
        if not self.entries:
            return f"📔 {self.agent_name}'s Diary: (empty - no adventures yet~)"

        lines = [
            f"╔══════════════════════════════════════╗",
            f"║  📔 {self.agent_name}'s Diary  ({self.species})  ║",
            f"╚══════════════════════════════════════╝",
            "",
        ]
        for entry in self.entries:
            lines.append(f"  {entry}")

        # Mood journey
        moods = [e.mood.value if isinstance(e.mood, Mood) else str(e.mood) for e in self.entries]
        mood_journey = " → ".join(moods[-5:])
        lines.append(f"\n  Emotional Journey: {mood_journey}")
        lines.append(f"  Total entries: {len(self.entries)}")
        return "\n".join(lines)

    def get_recent(self, count: int = 5) -> list[DiaryEntry]:
        return self.entries[-count:]


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Random Quests / Side Missions
# ═══════════════════════════════════════════════════════════════════════════════

class QuestStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"


@dataclass
class Quest:
    """A side mission for an agent to complete."""
    quest_id: str
    title: str
    description: str
    assigned_to: str
    condition: str  # What triggers completion
    xp_reward: int
    status: QuestStatus = QuestStatus.ACTIVE
    round_assigned: int = 0
    round_deadline: int = 0  # 0 = no deadline
    secret: bool = False  # Hidden achievement

    def __str__(self) -> str:
        icon = "🗡" if not self.secret else "🔮"
        status_icon = {"active": "◇", "completed": "◆", "expired": "✖"}
        return f"{icon}{status_icon.get(self.status.value, '?')} {self.title} (+{self.xp_reward}XP)"


QUEST_TEMPLATES = [
    {
        "id": "social_sprint",
        "title": "Social Sprint",
        "description": "Send 3 messages in one round!",
        "condition": "messages_in_round>=3",
        "xp": 30,
    },
    {
        "id": "night_owl",
        "title": "Night Owl",
        "description": "Complete a task during the night phase.",
        "condition": "task_at_night",
        "xp": 25,
    },
    {
        "id": "speed_demon",
        "title": "Speed Demon",
        "description": "Complete a task within 2 rounds of assignment.",
        "condition": "fast_task",
        "xp": 40,
    },
    {
        "id": "peacemaker",
        "title": "Peacemaker",
        "description": "Send a positive message to someone you've had a conflict with.",
        "condition": "reconcile",
        "xp": 35,
    },
    {
        "id": "campfire_star",
        "title": "Campfire Star",
        "description": "Tell a story at the campfire.",
        "condition": "tell_campfire_story",
        "xp": 20,
    },
    {
        "id": "gossip_master",
        "title": "Gossip Master",
        "description": "Share gossip with 3 different agents.",
        "condition": "gossip_spread>=3",
        "xp": 25,
    },
    {
        "id": "file_frenzy",
        "title": "File Frenzy",
        "description": "Create 3+ files in a single round.",
        "condition": "files_in_round>=3",
        "xp": 35,
    },
    {
        "id": "dreamer",
        "title": "Lucid Dreamer",
        "description": "Have a prophetic dream.",
        "condition": "prophetic_dream",
        "xp": 20,
        "secret": True,
    },
    {
        "id": "storm_worker",
        "title": "Storm Chaser",
        "description": "Complete a task during a thunderstorm.",
        "condition": "task_in_storm",
        "xp": 45,
        "secret": True,
    },
    {
        "id": "fortune_blessed",
        "title": "Fortune's Favorite",
        "description": "Fulfill a fortune cookie prediction.",
        "condition": "fortune_fulfilled",
        "xp": 50,
        "secret": True,
    },
]


class QuestBoard:
    """Manages active quests for all agents."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.active_quests: dict[str, list[Quest]] = {}  # agent_name -> quests
        self.completed_quests: list[Quest] = []

    def assign_quest(self, agent_name: str, round_number: int) -> Quest | None:
        """Assign a random quest to an agent."""
        current = self.active_quests.get(agent_name, [])
        active = [q for q in current if q.status == QuestStatus.ACTIVE]
        if len(active) >= 2:  # Max 2 active quests
            return None

        # Pick a quest they haven't done
        completed_ids = {q.quest_id for q in self.completed_quests if q.assigned_to == agent_name}
        available = [t for t in QUEST_TEMPLATES if t["id"] not in completed_ids]
        if not available:
            return None

        template = self._rng.choice(available)
        quest = Quest(
            quest_id=template["id"],
            title=template["title"],
            description=template["description"],
            assigned_to=agent_name,
            condition=template["condition"],
            xp_reward=template["xp"],
            round_assigned=round_number,
            round_deadline=round_number + 10,
            secret=template.get("secret", False),
        )
        self.active_quests.setdefault(agent_name, []).append(quest)
        _fire_event(
            "quest.assigned",
            agent=agent_name,
            quest_id=quest.quest_id,
            title=quest.title,
            round=round_number,
        )
        return quest

    def check_completion(self, agent_name: str, condition: str, round_number: int) -> list[Quest]:
        """Check if any active quests match the given condition."""
        completed: list[Quest] = []
        for quest in self.active_quests.get(agent_name, []):
            if quest.status != QuestStatus.ACTIVE:
                continue
            if quest.condition == condition or condition.startswith(quest.condition.split(">=")[0]):
                quest.status = QuestStatus.COMPLETED
                self.completed_quests.append(quest)
                completed.append(quest)
                _fire_event(
                    "quest.completed",
                    agent=agent_name,
                    quest_id=quest.quest_id,
                    title=quest.title,
                    xp_reward=quest.xp_reward,
                    round=round_number,
                )
        return completed

    def expire_quests(self, round_number: int) -> list[Quest]:
        """Expire quests past their deadline."""
        expired: list[Quest] = []
        for quests in self.active_quests.values():
            for quest in quests:
                if quest.status == QuestStatus.ACTIVE and quest.round_deadline > 0 and round_number > quest.round_deadline:
                    quest.status = QuestStatus.EXPIRED
                    expired.append(quest)
        return expired

    def get_active_quests(self, agent_name: str) -> list[Quest]:
        return [q for q in self.active_quests.get(agent_name, []) if q.status == QuestStatus.ACTIVE]

    def get_board_display(self) -> str:
        """Kawaii quest board display."""
        lines = ["~*~ QUEST BOARD ~*~", ""]
        for agent_name, quests in self.active_quests.items():
            active = [q for q in quests if q.status == QuestStatus.ACTIVE]
            if active:
                lines.append(f"  {agent_name}:")
                for q in active:
                    lines.append(f"    {q}")
        if len(lines) == 2:
            lines.append("  (No active quests~)")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Trading Post (Skill Point Exchange)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    """A skill point trade between two agents."""
    trader: str
    receiver: str
    offered_stat: str
    offered_amount: int
    requested_stat: str
    requested_amount: int
    round_number: int
    accepted: bool = False

    def __str__(self) -> str:
        status = "✓" if self.accepted else "?"
        return (
            f"{status} {self.trader} offers {self.offered_stat}+{self.offered_amount} "
            f"for {self.requested_stat}+{self.requested_amount} from {self.receiver}"
        )


class TradingPost:
    """Manages skill point trading between agents."""

    def __init__(self) -> None:
        self.trades: list[Trade] = []
        self.balances: dict[str, dict[str, int]] = {}  # agent -> stat bonuses

    def propose_trade(
        self,
        trader: str,
        receiver: str,
        offered_stat: str,
        offered_amount: int,
        requested_stat: str,
        requested_amount: int,
        round_number: int,
    ) -> Trade:
        trade = Trade(
            trader=trader,
            receiver=receiver,
            offered_stat=offered_stat,
            offered_amount=offered_amount,
            requested_stat=requested_stat,
            requested_amount=requested_amount,
            round_number=round_number,
        )
        self.trades.append(trade)
        return trade

    def accept_trade(self, trade: Trade) -> bool:
        """Execute a trade - swap stat bonuses."""
        if trade.accepted:
            return False
        trade.accepted = True

        # Apply bonuses
        self.balances.setdefault(trade.receiver, {})
        self.balances.setdefault(trade.trader, {})

        self.balances[trade.receiver][trade.offered_stat] = (
            self.balances[trade.receiver].get(trade.offered_stat, 0) + trade.offered_amount
        )
        self.balances[trade.trader][trade.requested_stat] = (
            self.balances[trade.trader].get(trade.requested_stat, 0) + trade.requested_amount
        )

        # Deduct from traders
        self.balances[trade.trader][trade.offered_stat] = (
            self.balances[trade.trader].get(trade.offered_stat, 0) - trade.offered_amount
        )
        self.balances[trade.receiver][trade.requested_stat] = (
            self.balances[trade.receiver].get(trade.requested_stat, 0) - trade.requested_amount
        )

        _fire_event(
            "trade.completed",
            trader=trade.trader,
            receiver=trade.receiver,
            offered_stat=trade.offered_stat,
            offered_amount=trade.offered_amount,
            requested_stat=trade.requested_stat,
            requested_amount=trade.requested_amount,
        )
        return True

    def get_bonus(self, agent_name: str, stat: str) -> int:
        """Get the trade bonus for a given stat."""
        return self.balances.get(agent_name, {}).get(stat, 0)

    def auto_trade(
        self,
        agent_a: str,
        agent_b: str,
        stats_a: dict[str, int],
        stats_b: dict[str, int],
        trust: float,
        round_number: int,
    ) -> Trade | None:
        """Automatically propose a trade if trust is high enough."""
        if trust < 0.6:
            return None

        # Find complementary stats: A's highest vs B's highest
        a_best = max(stats_a, key=stats_a.get)  # type: ignore[arg-type]
        b_best = max(stats_b, key=stats_b.get)  # type: ignore[arg-type]

        if a_best == b_best:
            return None

        trade = self.propose_trade(
            agent_a, agent_b,
            a_best, 1, b_best, 1,
            round_number,
        )
        self.accept_trade(trade)
        return trade


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Rival Boss Fight
# ═══════════════════════════════════════════════════════════════════════════════

class BossPhase(str, Enum):
    APPEARING = "appearing"
    FIGHTING = "fighting"
    DEFEATED = "defeated"
    ESCAPED = "escaped"


@dataclass
class BossAgent:
    """A rival boss that appears to challenge the team."""
    name: str
    species: str
    level: int
    hp: int
    max_hp: int
    attack_power: int
    phase: BossPhase = BossPhase.APPEARING
    round_appeared: int = 0
    damage_log: list[str] = field(default_factory=list)
    drops: dict[str, int] = field(default_factory=dict)  # reward on defeat

    @staticmethod
    def generate(round_number: int, team_avg_level: int, rng: random.Random) -> BossAgent:
        """Generate a boss scaled to the team's level."""
        boss_species = rng.choice(["dragon", "kraken", "golem", "shadow", "phoenix"])
        boss_names = {
            "dragon": "Draco the Debugger",
            "kraken": "Krakode the Merge Monster",
            "golem": "Legacy Golem",
            "shadow": "Shadow of Tech Debt",
            "phoenix": "Phoenix of Refactor",
        }
        level = max(team_avg_level + 2, 3)
        hp = level * 30
        return BossAgent(
            name=boss_names[boss_species],
            species=boss_species,
            level=level,
            hp=hp,
            max_hp=hp,
            attack_power=level * 5,
            round_appeared=round_number,
            drops={"xp": level * 25, "achievement_points": 1},
        )

    def take_damage(self, agent_name: str, damage: int) -> str:
        """Boss takes damage from an agent's contribution."""
        self.hp = max(0, self.hp - damage)
        msg = f"{agent_name} dealt {damage} damage to {self.name}! ({self.hp}/{self.max_hp} HP)"
        self.damage_log.append(msg)
        if self.hp <= 0:
            self.phase = BossPhase.DEFEATED
            msg += f" {self.name} is DEFEATED!"
        return msg

    @property
    def hp_bar(self) -> str:
        """Kawaii HP bar."""
        bar_width = 20
        filled = int((self.hp / self.max_hp) * bar_width) if self.max_hp > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        return f"[{bar}] {self.hp}/{self.max_hp}"

    def get_boss_card(self) -> str:
        """Render a boss encounter card."""
        return (
            f"╔═══════════════════════════════════╗\n"
            f"║  ☠ BOSS ENCOUNTER! ☠              ║\n"
            f"║  {self.name:<33}║\n"
            f"║  Species: {self.species:<23}║\n"
            f"║  Level: {self.level:<25}║\n"
            f"║  HP: {self.hp_bar:<28}║\n"
            f"╚═══════════════════════════════════╝"
        )


def compute_boss_hp(agent_count: int, avg_level: float, base_hp: int = 100) -> int:
    """Compute scaled boss HP based on the current team size and average level.

    Formula: ``hp = base_hp + (agent_count * 20) + int(avg_level * 10)``.
    Capped at 500 to prevent runaway values.
    """
    hp = base_hp + (agent_count * 20) + int(avg_level * 10)
    return min(hp, 500)


def compute_boss_reward(hp: int) -> dict[str, Any]:
    """Return XP and rarity rewards scaled by boss HP.

    Harder boss (higher HP) → better reward.
    - ``xp_bonus`` = ``hp // 10``
    - ``rarity_boost`` = ``min(hp / 500, 0.5)``
    """
    return {
        "xp_bonus": hp // 10,
        "rarity_boost": min(hp / 500, 0.5),
    }


class BossArena:
    """Manages boss encounters."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.current_boss: BossAgent | None = None
        self.defeated_bosses: list[BossAgent] = []
        self._boss_probability = 0.1
        self._min_round = 8

    def maybe_spawn_boss(
        self,
        round_number: int,
        team_avg_level: int,
        agent_count: int = 1,
    ) -> BossAgent | None:
        if self.current_boss and self.current_boss.phase in (BossPhase.APPEARING, BossPhase.FIGHTING):
            return None
        if round_number < self._min_round:
            return None
        if self._rng.random() > self._boss_probability:
            return None

        self.current_boss = BossAgent.generate(round_number, team_avg_level, self._rng)
        # Override the default level-based HP with the scaled value.
        scaled_hp = compute_boss_hp(agent_count, float(team_avg_level))
        self.current_boss.hp = scaled_hp
        self.current_boss.max_hp = scaled_hp
        self.current_boss.phase = BossPhase.FIGHTING
        return self.current_boss

    def agent_attack(self, agent_name: str, agent_stats: dict[str, int], agent_level: int) -> str | None:
        """Agent contributes damage based on their stats."""
        if not self.current_boss or self.current_boss.phase != BossPhase.FIGHTING:
            return None

        # Damage based on agent stats + level
        base_damage = sum(agent_stats.values()) + agent_level * 3
        damage = max(1, base_damage + self._rng.randint(-5, 10))
        result = self.current_boss.take_damage(agent_name, damage)

        if self.current_boss.phase == BossPhase.DEFEATED:
            self.defeated_bosses.append(self.current_boss)

        return result

    def get_defeat_rewards(self) -> dict[str, Any]:
        """Return scaled rewards for the current (or most recently defeated) boss.

        Callers should distribute ``xp_bonus`` to all participating agents.
        """
        boss = self.current_boss
        if boss is None and self.defeated_bosses:
            boss = self.defeated_bosses[-1]
        if boss is None:
            return {"xp_bonus": 0, "rarity_boost": 0.0}
        return compute_boss_reward(boss.max_hp)

    def check_escape(self, round_number: int) -> bool:
        """Boss escapes if not defeated within 5 rounds."""
        if not self.current_boss or self.current_boss.phase != BossPhase.FIGHTING:
            return False
        if round_number - self.current_boss.round_appeared > 5:
            self.current_boss.phase = BossPhase.ESCAPED
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Love Letters & Hate Mail
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Letter:
    """A letter sent between agents based on their relationship."""
    sender: str
    recipient: str
    letter_type: str  # "love", "rivalry", "thank_you", "challenge"
    content: str
    round_number: int

    def __str__(self) -> str:
        icons = {"love": "💌", "rivalry": "⚔", "thank_you": "🎁", "challenge": "🗡"}
        return f"{icons.get(self.letter_type, '✉')} {self.sender} → {self.recipient}: {self.content}"


LOVE_TEMPLATES = [
    "Dear {recipient}, working with you makes every bug worth fixing! ♥ From, {sender}",
    "{recipient}~! You're the best teammate a {species} could ask for! ♥♥ Love, {sender}",
    "To {recipient}: Our friendship is my favorite achievement. Never change! ♥ — {sender}",
    "Hey {recipient}, you + me = unstoppable! Let's keep being awesome~ ♥ {sender}",
]

RIVALRY_TEMPLATES = [
    "Dear {recipient}, I WILL surpass you. Watch your back! ✖ — {sender}",
    "{recipient}... the leaderboard gap between us is about to CLOSE. ✖ — {sender}",
    "To my rival {recipient}: May the best {species} win! ⚔ — {sender}",
    "Hey {recipient}, your code is... fine. Mine's better though. ✖ {sender}",
]

THANK_YOU_TEMPLATES = [
    "Dear {recipient}, thanks for the help today! You're a real one. ★ — {sender}",
    "{recipient}! That mentorship moment meant everything. Thank you~ ★ — {sender}",
]

CHALLENGE_TEMPLATES = [
    "CHALLENGE: {recipient}, I bet I can complete more tasks than you this round! ⚔ — {sender}",
    "{recipient}, race you to the next level up! Loser tells a campfire story. ⚔ — {sender}",
]


class PostOffice:
    """Manages letters between agents based on relationship changes."""

    def __init__(self) -> None:
        self.mailbox: dict[str, list[Letter]] = {}  # recipient -> letters
        self.all_letters: list[Letter] = []

    def check_and_send(
        self,
        sender: str,
        recipient: str,
        trust: float,
        sender_species: str,
        round_number: int,
    ) -> Letter | None:
        """Auto-generate a letter based on relationship trust level."""
        # Already sent a letter this round?
        recent = [l for l in self.all_letters if l.sender == sender and l.recipient == recipient and l.round_number == round_number]
        if recent:
            return None

        letter_type = None
        templates = None

        if trust >= 0.9:
            letter_type = "love"
            templates = LOVE_TEMPLATES
        elif trust <= 0.15:
            letter_type = "rivalry"
            templates = RIVALRY_TEMPLATES
        elif trust >= 0.7:
            letter_type = "thank_you"
            templates = THANK_YOU_TEMPLATES
        else:
            return None

        content = random.choice(templates).format(
            sender=sender, recipient=recipient, species=sender_species,
        )
        letter = Letter(
            sender=sender,
            recipient=recipient,
            letter_type=letter_type,
            content=content,
            round_number=round_number,
        )
        self.mailbox.setdefault(recipient, []).append(letter)
        self.all_letters.append(letter)
        _fire_event(
            "letter.sent",
            sender=sender,
            recipient=recipient,
            letter_type=letter_type,
            round=round_number,
        )
        return letter

    def send_challenge(self, sender: str, recipient: str, sender_species: str, round_number: int) -> Letter:
        content = random.choice(CHALLENGE_TEMPLATES).format(
            sender=sender, recipient=recipient, species=sender_species,
        )
        letter = Letter(sender=sender, recipient=recipient, letter_type="challenge",
                        content=content, round_number=round_number)
        self.mailbox.setdefault(recipient, []).append(letter)
        self.all_letters.append(letter)
        _fire_event(
            "letter.sent",
            sender=sender,
            recipient=recipient,
            letter_type="challenge",
            round=round_number,
        )
        return letter

    def get_mail(self, agent_name: str) -> list[Letter]:
        return self.mailbox.get(agent_name, [])

    def get_unread(self, agent_name: str) -> list[Letter]:
        """Get all letters for an agent (and clear the mailbox)."""
        letters = self.mailbox.pop(agent_name, [])
        return letters


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Fortune Cookies
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FortuneCookie:
    """A fortune cookie with a prediction and bonus effect."""
    recipient: str
    fortune: str
    condition: str  # What fulfills the fortune
    bonus_xp: int
    round_given: int
    fulfilled: bool = False
    picked_up: bool = False

    def __str__(self) -> str:
        status = "✓" if self.fulfilled else "◇"
        return f"🥠{status} {self.fortune} (+{self.bonus_xp}XP if fulfilled)"


FORTUNE_TEMPLATES = [
    {"fortune": "Create a file today and good luck will follow~", "condition": "create_file", "xp": 20},
    {"fortune": "Send a message to a friend for double karma!", "condition": "send_message", "xp": 15},
    {"fortune": "Complete a task before nightfall for bonus XP!", "condition": "complete_task", "xp": 25},
    {"fortune": "Help someone today and the universe will reward you~", "condition": "request_help", "xp": 20},
    {"fortune": "A surprise awaits if you survive the next storm...", "condition": "survive_storm", "xp": 30},
    {"fortune": "Your lucky number is 7. Do 7 things today!", "condition": "seven_actions", "xp": 35},
    {"fortune": "Share gossip with someone new for a twist of fate!", "condition": "share_gossip", "xp": 15},
    {"fortune": "Tell a story at the campfire to unlock your potential~", "condition": "tell_story", "xp": 20},
    {"fortune": "Your bond with a teammate will grow stronger today!", "condition": "strengthen_bond", "xp": 15},
    {"fortune": "A dream tonight will reveal hidden truths...", "condition": "have_dream", "xp": 10},
]


class FortuneCookieJar:
    """Distributes fortune cookies to agents."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.active_fortunes: dict[str, FortuneCookie] = {}  # agent -> active fortune
        self.history: list[FortuneCookie] = []

    def give_cookie(self, agent_name: str, round_number: int) -> FortuneCookie | None:
        """Give a fortune cookie to an agent (one at a time)."""
        if agent_name in self.active_fortunes:
            return None

        template = self._rng.choice(FORTUNE_TEMPLATES)
        cookie = FortuneCookie(
            recipient=agent_name,
            fortune=template["fortune"],
            condition=template["condition"],
            bonus_xp=template["xp"],
            round_given=round_number,
        )
        self.active_fortunes[agent_name] = cookie
        self.history.append(cookie)
        return cookie

    def check_fulfillment(self, agent_name: str, action: str) -> FortuneCookie | None:
        """Check if an action fulfills the agent's fortune."""
        cookie = self.active_fortunes.get(agent_name)
        if not cookie or cookie.fulfilled:
            return None

        if cookie.condition == action:
            cookie.fulfilled = True
            del self.active_fortunes[agent_name]
            return cookie
        return None

    def get_active_fortune(self, agent_name: str) -> FortuneCookie | None:
        return self.active_fortunes.get(agent_name)

    def open_cookie(self, agent_name: str) -> FortuneCookie | None:
        """Mark the agent's active cookie as physically picked up.

        Idempotent: returns None if the agent has no active cookie or the
        cookie has already been opened. The cookie stays in ``active_fortunes``
        so the action-based fulfilment in ``check_fulfillment`` can still fire.
        """
        cookie = self.active_fortunes.get(agent_name)
        if cookie is None or cookie.picked_up:
            return None
        cookie.picked_up = True
        return cookie


# ═══════════════════════════════════════════════════════════════════════════════
# 20. Agent Ghosts
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GhostAgent:
    """A ghost of a retired/crashed agent that lingers and gives hints."""
    name: str
    species: str
    species_emoji: str
    cause_of_death: str  # "timeout", "errors", "retired", "sacrificed"
    round_died: int
    last_words: str
    memories: list[str] = field(default_factory=list)  # Preserved wisdom
    appearances: int = 0
    max_appearances: int = 5

    def __str__(self) -> str:
        return f"👻 {self.species_emoji} {self.name} (ghost) — \"{self.last_words}\""

    def appear(self) -> str | None:
        """Ghost appears and shares wisdom. Returns hint or None if max appearances reached."""
        if self.appearances >= self.max_appearances:
            return None
        self.appearances += 1
        if self.memories:
            hint = random.choice(self.memories)
            return f"👻 The ghost of {self.name} whispers: \"{hint}\""
        return f"👻 The ghost of {self.name} watches silently..."

    @property
    def is_fading(self) -> bool:
        return self.appearances >= self.max_appearances


class GhostRealm:
    """Manages ghosts of fallen agents."""

    def __init__(self) -> None:
        self.ghosts: list[GhostAgent] = []

    def create_ghost(
        self,
        name: str,
        species: str,
        species_emoji: str,
        cause: str,
        round_died: int,
        memories: list[str],
    ) -> GhostAgent:
        """Create a ghost from a fallen agent."""
        last_words_map = {
            "timeout": "I... ran out of time... don't make the same mistake...",
            "errors": "The bugs... they got me... avenge me...",
            "retired": "My work here is done. Carry on, friends~",
            "sacrificed": "I did it for the team. Remember me...",
        }
        ghost = GhostAgent(
            name=name,
            species=species,
            species_emoji=species_emoji,
            cause_of_death=cause,
            round_died=round_died,
            last_words=last_words_map.get(cause, "..."),
            memories=memories[-5:],  # Keep last 5 memories as wisdom
        )
        self.ghosts.append(ghost)
        return ghost

    def maybe_appear(self, round_number: int) -> list[str]:
        """Ghosts have a chance of appearing each round."""
        messages: list[str] = []
        for ghost in self.ghosts:
            if not ghost.is_fading and random.random() < 0.3:
                msg = ghost.appear()
                if msg:
                    messages.append(msg)
        return messages

    def get_active_ghosts(self) -> list[GhostAgent]:
        return [g for g in self.ghosts if not g.is_fading]

    def get_graveyard(self) -> str:
        """Render the graveyard display."""
        if not self.ghosts:
            return "(^_^) No ghosts yet~ Everyone is alive!"
        lines = ["~*~ GRAVEYARD ~*~", ""]
        for ghost in self.ghosts:
            status = "👻" if not ghost.is_fading else "💀"
            lines.append(f"  {status} {ghost.species_emoji} {ghost.name} — {ghost.cause_of_death} (R{ghost.round_died})")
            lines.append(f"    Last words: \"{ghost.last_words}\"")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 21. Multiverse Branching
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MultiverseChoice:
    """A decision point that creates a branching timeline."""
    round_number: int
    description: str
    chosen_path: str
    alternate_path: str
    chosen_outcome: str = ""
    alternate_outcome: str = ""

    def __str__(self) -> str:
        return f"[R{self.round_number}] {self.description}: chose '{self.chosen_path}' over '{self.alternate_path}'"


ALTERNATE_OUTCOMES = {
    "task_complete": [
        "In the alternate timeline, the task was never completed and chaos ensued...",
        "In another universe, a different agent handled this and created 3x more bugs.",
        "The multiverse shows: skipping this task caused a cascade of failures.",
    ],
    "spawn": [
        "In an alternate timeline, this agent was never created. The team struggled without them.",
        "The multiverse reveals: a different agent was spawned here, with very different skills.",
    ],
    "guild_formed": [
        "In another reality, this guild never formed and agents worked alone. Efficiency dropped 40%.",
        "The multiverse shows: rival guilds formed instead, leading to epic debates.",
    ],
    "boss_defeated": [
        "In the alternate timeline, the boss won. All agents lost 50% XP. Dark times...",
        "Another universe: the team tried to negotiate instead of fight. It... didn't go well.",
    ],
    "evolution": [
        "In another timeline, this evolution happened 3 rounds earlier. Butterfly effect...",
        "The multiverse reveals: this agent evolved into a completely different species!",
    ],
}


class MultiverseEngine:
    """Tracks branching decision points and generates alternate timelines."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.branches: list[MultiverseChoice] = []

    def record_branch(
        self,
        round_number: int,
        description: str,
        chosen_path: str,
        alternate_path: str,
        event_type: str = "task_complete",
    ) -> MultiverseChoice:
        """Record a decision point with what-if."""
        alternates = ALTERNATE_OUTCOMES.get(event_type, ALTERNATE_OUTCOMES["task_complete"])
        branch = MultiverseChoice(
            round_number=round_number,
            description=description,
            chosen_path=chosen_path,
            alternate_path=alternate_path,
            chosen_outcome=f"Our timeline: {chosen_path} succeeded!",
            alternate_outcome=self._rng.choice(alternates),
        )
        self.branches.append(branch)
        return branch

    def get_what_if_report(self) -> str:
        """Generate the final multiverse comparison report."""
        if not self.branches:
            return "No branching points recorded~ This timeline was the only one."

        lines = [
            "╔═══════════════════════════════════════════╗",
            "║    ~*~ MULTIVERSE REPORT ~*~              ║",
            "║    What could have been...                ║",
            "╚═══════════════════════════════════════════╝",
            "",
        ]

        for i, branch in enumerate(self.branches[-8:], 1):
            lines.append(f"  ━━━ Branch Point #{i} (Round {branch.round_number}) ━━━")
            lines.append(f"  Decision: {branch.description}")
            lines.append(f"  ✓ Our path: {branch.chosen_path}")
            lines.append(f"  ✖ Alt path: {branch.alternate_path}")
            lines.append(f"    → {branch.alternate_outcome}")
            lines.append("")

        our_score = len([b for b in self.branches if "succeeded" in b.chosen_outcome])
        lines.append(f"  ~*~ Our timeline scored {our_score}/{len(self.branches)} optimal decisions! ~*~")

        return "\n".join(lines)

    def get_branch_count(self) -> int:
        return len(self.branches)
