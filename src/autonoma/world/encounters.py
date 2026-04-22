"""End-of-life and what-if subsystems: ghosts of retired agents and
the multiverse branching engine.

These are lightweight narrative subsystems — no persistent agent state,
just logs of what happened (ghosts) or what could have happened
(multiverse). Sitting at the tail of the monolith with no inbound
references from other subsystems made them clean candidates for a slice.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Ghosts
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
# Multiverse Branching
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
