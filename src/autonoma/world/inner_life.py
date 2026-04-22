"""Dreams + diary — agent-level inner-life subsystems.

Both live off each agent's memory/mood and have no outward effect apart
from emitting logs / returning content. That decoupling makes them a
clean slice to pull out of the monolith.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from autonoma.world.personality import Mood

if TYPE_CHECKING:
    from autonoma.world import MemoryEntry


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Dreams
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
        memories: "list[MemoryEntry]",
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
# Agent Diary
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
