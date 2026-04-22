"""Deterministic agent personality — moods, traits, species, bones.

An agent's ``bones`` (species, stats, traits, catchphrase, rarity) are
derived from a hash of their role + name, so the same role always looks
and feels the same across runs without any persistence.

Kept as its own module because mood/trait/species are the vocabulary
the rest of the world system speaks in — everywhere else (memory,
social, events…) imports from here.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from enum import Enum


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
