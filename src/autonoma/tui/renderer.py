"""Kawaii animated renderer - composites agents, speech bubbles, workspace into a live scene."""

from __future__ import annotations

import asyncio
import random
from collections import deque
from datetime import datetime
from typing import Any

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from autonoma.agents.swarm import AgentSwarm
from autonoma.event_bus import bus
from autonoma.models import ProjectState, TaskStatus
from autonoma.tui.sprites import (
    KAWAII_PROGRESS,
    KAWAII_STATUS_ICONS,
    MOOD_EMOTES,
    get_sprite,
    render_nametag,
    render_speech_bubble,
)
from autonoma.world import ACHIEVEMENTS, Mood


# ── Kawaii decorations ────────────────────────────────────────────────────

SPARKLE_FRAMES = ["✦", "✧", "✦", "•"]
HEART_FRAMES = ["♥", "♡", "♥", "♡"]
MUSIC_FRAMES = ["♪", "♫", "♪", "♩"]
STAR_FRAMES = ["★", "☆", "★", "☆"]

KAWAII_TITLES = [
    "~* Autonoma *~",
    "~♪ Autonoma ♪~",
    "~★ Autonoma ★~",
    "~♥ Autonoma ♥~",
]

IDLE_MESSAGES = [
    "(^_^) All agents standing by~",
    "(-_-)zzZ Waiting for action...",
    "(o.o) Ready when you are!",
]


class AnimatedRenderer:
    """Renders the kawaii animated TUI scene with Rich Live display."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._swarm: AgentSwarm | None = None
        self._project: ProjectState | None = None
        self._event_log: deque[str] = deque(maxlen=100)
        self._frame = 0
        self._round = 0
        self._max_rounds = 0
        self._files: list[str] = []
        self._total_tokens = 0
        self._handlers_registered = False
        self._particles: list[dict[str, Any]] = []  # floating particles for celebration
        self._sky_line: str = ""

    def attach(self, swarm: AgentSwarm, project: ProjectState) -> None:
        self._swarm = swarm
        self._project = project
        if not self._handlers_registered:
            self._register_handlers()
            self._handlers_registered = True

    def detach(self) -> None:
        """Unsubscribe all event handlers to prevent memory leaks."""
        if self._handlers_registered:
            bus.off("agent.speech", self._on_speech)
            bus.off("agent.state", self._on_state)
            bus.off("agent.spawned", self._on_spawned)
            bus.off("file.created", self._on_file)
            bus.off("task.assigned", self._on_task_assigned)
            bus.off("task.completed", self._on_task_completed)
            bus.off("swarm.round", self._on_round)
            bus.off("director.plan_ready", self._on_plan)
            bus.off("project.completed", self._on_project_done)
            bus.off("swarm.finished", self._on_swarm_finished)
            bus.off("agent.level_up", self._on_level_up)
            bus.off("world.event", self._on_world_event)
            bus.off("guild.formed", self._on_guild_formed)
            bus.off("campfire.complete", self._on_campfire)
            bus.off("world.clock", self._on_clock)
            bus.off("fortune.given", self._on_fortune)
            bus.off("agent.dream", self._on_dream)
            bus.off("boss.appeared", self._on_boss_appeared)
            bus.off("boss.defeated", self._on_boss_defeated)
            bus.off("boss.damage", self._on_boss_damage)
            bus.off("ghost.appears", self._on_ghost)
            self._handlers_registered = False

    def _register_handlers(self) -> None:
        bus.on("agent.speech", self._on_speech)
        bus.on("agent.state", self._on_state)
        bus.on("agent.spawned", self._on_spawned)
        bus.on("file.created", self._on_file)
        bus.on("task.assigned", self._on_task_assigned)
        bus.on("task.completed", self._on_task_completed)
        bus.on("swarm.round", self._on_round)
        bus.on("director.plan_ready", self._on_plan)
        bus.on("project.completed", self._on_project_done)
        bus.on("swarm.finished", self._on_swarm_finished)
        bus.on("agent.level_up", self._on_level_up)
        bus.on("world.event", self._on_world_event)
        bus.on("guild.formed", self._on_guild_formed)
        bus.on("campfire.complete", self._on_campfire)
        bus.on("world.clock", self._on_clock)
        bus.on("fortune.given", self._on_fortune)
        bus.on("agent.dream", self._on_dream)
        bus.on("boss.appeared", self._on_boss_appeared)
        bus.on("boss.defeated", self._on_boss_defeated)
        bus.on("boss.damage", self._on_boss_damage)
        bus.on("ghost.appears", self._on_ghost)

    def render(self) -> Layout:
        """Render the complete kawaii animated scene."""
        self._frame += 1
        self._tick_particles()
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["header"].update(self._render_header())

        layout["body"].split_row(
            Layout(name="scene", ratio=3),
            Layout(name="sidebar", ratio=2),
        )

        layout["scene"].split_column(
            Layout(name="stage", ratio=3),
            Layout(name="events", ratio=1),
        )

        layout["stage"].update(self._render_stage())
        layout["events"].update(self._render_events())

        layout["sidebar"].split_column(
            Layout(name="tasks", ratio=2),
            Layout(name="files", ratio=1),
        )

        layout["tasks"].update(self._render_tasks())
        layout["files"].update(self._render_files())
        layout["footer"].update(self._render_footer())

        return layout

    # ── Kawaii Scene Rendering ─────────────────────────────────────────────

    def _render_header(self) -> Panel:
        title = Text()
        # Animated kawaii title
        kawaii_title = KAWAII_TITLES[self._frame % len(KAWAII_TITLES)]
        title.append(f" {kawaii_title} ", style="bold magenta")
        title.append("Self-Organizing Agent Swarm ", style="bold white")
        if self._project:
            title.append(f"| {self._project.name} ", style="cyan")
        if self._round > 0:
            sparkle = SPARKLE_FRAMES[self._frame % len(SPARKLE_FRAMES)]
            title.append(f"| {sparkle} Round {self._round}/{self._max_rounds} {sparkle} ", style="yellow")
        if self._sky_line:
            title.append(f"| {self._sky_line} ", style="dim cyan")
        return Panel(Align.center(title), style="magenta", border_style="bright_magenta")

    def _render_stage(self) -> Panel:
        """Render the animated stage with kawaii agent sprites and speech bubbles."""
        if not self._swarm:
            msg = IDLE_MESSAGES[self._frame % len(IDLE_MESSAGES)]
            return Panel(f"[dim]{msg}[/]", title="[bold cyan]~ Stage ~[/]", border_style="bright_cyan")

        WIDTH = 80
        HEIGHT = 18
        canvas: list[list[str]] = [[" "] * WIDTH for _ in range(HEIGHT)]

        # Draw floating particles (sparkles, hearts, stars)
        for p in self._particles:
            px, py = int(p["x"]), int(p["y"])
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                canvas[py][px] = p["char"]

        for name, agent in self._swarm.agents.items():
            x = min(max(agent.position.x, 0), WIDTH - 14)
            y = min(max(agent.position.y, 0), HEIGHT - 6)
            color = agent.persona.color

            # Draw speech bubble above agent
            if agent.speech:
                bubble_lines = render_speech_bubble(agent.speech.text, max_width=26)
                bubble_y = max(0, y - len(bubble_lines))
                for i, line in enumerate(bubble_lines):
                    row = bubble_y + i
                    if 0 <= row < HEIGHT:
                        for j, ch in enumerate(line[:WIDTH - x]):
                            col = x + j
                            if 0 <= col < WIDTH:
                                canvas[row][col] = ch

            # Draw kawaii sprite (4 lines tall now)
            sprite_lines = get_sprite(agent.state, agent.persona.emoji, self._frame)
            for i, line in enumerate(sprite_lines):
                row = y + i
                if 0 <= row < HEIGHT:
                    for j, ch in enumerate(line[:WIDTH - x]):
                        col = x + j
                        if 0 <= col < WIDTH:
                            canvas[row][col] = ch

            # Draw cute name tag with level and evolved species
            name_row = y + len(sprite_lines)
            level_str = f"Lv{agent.stats.level}" if hasattr(agent, 'stats') else ""
            species_emoji = ""
            species_name = ""
            if hasattr(agent, 'bones') and agent.bones:
                evolved_sp, evolved_ej = agent.bones.get_evolved_form(
                    agent.stats.level if hasattr(agent, 'stats') else 1
                )
                species_emoji = evolved_ej
                species_name = evolved_sp
            mood_str = ""
            if hasattr(agent, 'mood'):
                mood_map = {
                    "happy": "(^w^)", "focused": "(>_<)", "frustrated": "(>.<)",
                    "excited": "(*^*)", "tired": "(-_-)", "proud": "(^_~)",
                    "worried": "(o_o)", "curious": "(?.?)", "determined": "(!!)",
                    "relaxed": "(~_~)", "inspired": "(!!)", "mischievous": "(>w<)",
                    "nostalgic": "(._.)","determined": "(!_!)",
                }
                mood_str = mood_map.get(agent.mood.value, "")
            tag = f"{species_emoji}~{name[:6]}~{level_str}"
            if name_row < HEIGHT:
                for j, ch in enumerate(tag[:WIDTH - x]):
                    col = x + j
                    if 0 <= col < WIDTH:
                        canvas[name_row][col] = ch
            # Mood face below name
            mood_row = name_row + 1
            if mood_str and mood_row < HEIGHT:
                for j, ch in enumerate(mood_str[:WIDTH - x]):
                    col = x + j
                    if 0 <= col < WIDTH:
                        canvas[mood_row][col] = ch

        scene_text = Text()
        for row in canvas:
            scene_text.append("".join(row) + "\n")

        heart = HEART_FRAMES[self._frame % len(HEART_FRAMES)]
        return Panel(
            scene_text,
            title=f"[bold cyan]{heart} Stage {heart}[/]",
            border_style="bright_cyan",
            subtitle=f"[dim]frame {self._frame % 1000}[/]",
        )

    def _render_events(self) -> Panel:
        if not self._event_log:
            content = f"[dim]{MOOD_EMOTES['waiting']} Waiting for activity...[/]"
        else:
            content = "\n".join(list(self._event_log)[-8:])
        music = MUSIC_FRAMES[self._frame % len(MUSIC_FRAMES)]
        return Panel(
            content,
            title=f"[bold green]{music} Activity {music}[/]",
            border_style="bright_green",
        )

    def _render_tasks(self) -> Panel:
        star = STAR_FRAMES[self._frame % len(STAR_FRAMES)]
        if not self._project or not self._project.tasks:
            return Panel(
                f"[dim]{MOOD_EMOTES['thinking']} No tasks yet...[/]",
                title=f"[bold yellow]{star} Tasks {star}[/]",
                border_style="bright_yellow",
            )

        table = Table(show_header=True, header_style="bold", expand=True, show_lines=False)
        table.add_column("", width=2)
        table.add_column("Task", ratio=2)
        table.add_column("Agent", ratio=1)
        table.add_column("Status")

        status_styles = {
            TaskStatus.OPEN: "dim",
            TaskStatus.ASSIGNED: "yellow",
            TaskStatus.IN_PROGRESS: "bold cyan",
            TaskStatus.REVIEW: "magenta",
            TaskStatus.DONE: "green",
            TaskStatus.BLOCKED: "red",
        }

        for task in self._project.tasks:
            icon = KAWAII_STATUS_ICONS.get(task.status, "☆")
            style = status_styles.get(task.status, "")

            # Animated indicator for in-progress
            if task.status == TaskStatus.IN_PROGRESS:
                music_notes = ["♪", "♫", "♩", "♬"]
                icon = music_notes[self._frame % 4]

            table.add_row(
                Text(icon, style=style),
                Text(task.title[:28], style=style),
                task.assigned_to or "[dim]-[/]",
                Text(task.status.value, style=style),
            )

        done = sum(1 for t in self._project.tasks if t.status == TaskStatus.DONE)
        total = len(self._project.tasks)
        pct = (done / total * 100) if total else 0
        bar_w = 20
        filled = int(pct / 100 * bar_w)

        # Kawaii progress bar with stars
        filled_str = KAWAII_PROGRESS["filled"] * filled
        empty_str = KAWAII_PROGRESS["empty"] * (bar_w - filled)
        bar = f"[yellow]{filled_str}[/][dim]{empty_str}[/] {pct:.0f}%"

        mood = "done" if pct == 100 else "working" if pct > 0 else "thinking"
        emote = MOOD_EMOTES.get(mood, "")

        return Panel(
            Group(table, Text(f"\n{bar}  ({done}/{total} done) {emote}")),
            title=f"[bold yellow]{star} Tasks {star}[/]",
            border_style="bright_yellow",
        )

    def _render_files(self) -> Panel:
        sparkle = SPARKLE_FRAMES[self._frame % len(SPARKLE_FRAMES)]
        if not self._files:
            return Panel(
                f"[dim]{MOOD_EMOTES['waiting']} No files yet...[/]",
                title=f"[bold magenta]{sparkle} Files {sparkle}[/]",
                border_style="bright_magenta",
            )

        tree = Tree(f"[bold]{sparkle} output/[/]")
        dirs: dict[str, Any] = {}
        for f in self._files:
            parts = f.split("/")
            current = dirs
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = None

        def build(node: dict, parent: Tree, depth: int = 0) -> None:
            if depth > 6:
                parent.add("[dim]...[/]")
                return
            for name, children in sorted(node.items()):
                if children is None:
                    parent.add(f"[cyan]♪ {name}[/]")
                else:
                    branch = parent.add(f"[bold]♥ {name}/[/]")
                    build(children, branch, depth + 1)

        build(dirs, tree)
        return Panel(tree, title=f"[bold magenta]{sparkle} Files {sparkle}[/]", border_style="bright_magenta")

    def _render_footer(self) -> Panel:
        agents = len(self._swarm.agents) if self._swarm else 0
        heart = HEART_FRAMES[self._frame % len(HEART_FRAMES)]
        return Panel(
            Align.center(Text.from_markup(
                f"[dim]{heart} Autonoma v0.1.0 | "
                f"Agents: {agents} {MOOD_EMOTES['happy']} | "
                f"Files: {len(self._files)} | "
                f"Tokens: {self._total_tokens:,} | "
                f"Ctrl+C to stop {heart}[/]"
            )),
            style="dim",
            border_style="bright_magenta",
        )

    # ── Particle System (floating sparkles/hearts) ────────────────────────

    def _tick_particles(self) -> None:
        """Update floating particle positions and spawn new ones."""
        # Move existing particles
        self._particles = [
            {**p, "y": p["y"] - 0.5, "x": p["x"] + random.choice([-0.5, 0, 0.5])}
            for p in self._particles
            if p["y"] > 0
        ]

        # Spawn celebration particles when tasks complete
        if self._project and self._swarm:
            for agent in self._swarm.agents.values():
                if agent.state.value == "celebrating" and random.random() < 0.3:
                    chars = ["✦", "♥", "★", "♪", "✧", "♡", "☆"]
                    self._particles.append({
                        "x": agent.position.x + random.randint(-2, 6),
                        "y": agent.position.y - 1,
                        "char": random.choice(chars),
                    })

        # Cap particles
        if len(self._particles) > 30:
            self._particles = self._particles[-30:]

    # ── Event Handlers ─────────────────────────────────────────────────────

    async def _on_speech(self, agent: str = "", text: str = "", style: str = "dim", **_: Any) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        emoji = "?"
        if self._swarm and agent in self._swarm.agents:
            emoji = self._swarm.agents[agent].persona.emoji
        self._log(f"[dim]{ts}[/] {emoji} [bold]{agent}[/]: [{style}]{text}[/]")

    async def _on_state(self, agent: str = "", state: str = "", **_: Any) -> None:
        pass

    async def _on_spawned(self, name: str = "", role: str = "", emoji: str = "?", **_: Any) -> None:
        self._log(f"[bold green]★ New agent:[/] {emoji} {name} ({role}) {MOOD_EMOTES['excited']}")

    async def _on_file(self, agent: str = "", path: str = "", **_: Any) -> None:
        self._files.append(path)
        self._log(f"[bold cyan]♪ {agent}[/] -> [bold]{path}[/]")

    async def _on_task_assigned(self, agent: str = "", assigned_to: str = "", title: str = "", **_: Any) -> None:
        self._log(f"[bold yellow]♫ {agent}[/] -> {assigned_to}: [italic]{title}[/]")

    async def _on_task_completed(self, agent: str = "", title: str = "", **_: Any) -> None:
        self._log(f"[bold green]★ {agent}[/] done: {title} {MOOD_EMOTES['done']}")

    async def _on_round(self, round: int = 0, max_rounds: int = 0, sky: str = "", **_: Any) -> None:
        self._round = round
        self._max_rounds = max_rounds
        self._frame = 0
        if sky:
            self._sky_line = sky

    async def _on_plan(self, task_count: int = 0, agent_count: int = 0, analysis: str = "", **_: Any) -> None:
        self._log(f"[bold yellow]♥ Director:[/] Plan ready - {task_count} tasks, {agent_count} agents {MOOD_EMOTES['proud']}")
        if analysis:
            self._log(f"  [dim]{analysis[:80]}[/]")

    async def _on_project_done(self, **_: Any) -> None:
        self._log(f"[bold green]★ PROJECT COMPLETE! {MOOD_EMOTES['excited']}[/]")
        # Spawn celebration particles
        for _ in range(10):
            self._particles.append({
                "x": random.randint(10, 70),
                "y": random.randint(10, 16),
                "char": random.choice(["✦", "♥", "★", "♪"]),
            })

    async def _on_swarm_finished(
        self, total_tokens: int = 0, epilogue: str = "", leaderboard: str = "",
        multiverse: str = "", graveyard: str = "", **_: Any,
    ) -> None:
        self._total_tokens = total_tokens
        if leaderboard:
            for line in leaderboard.split("\n"):
                self._log(f"[bold yellow]{line}[/]")
        if graveyard and "No ghosts" not in graveyard:
            for line in graveyard.split("\n")[-4:]:
                self._log(f"[dim]{line}[/]")
        if epilogue:
            for line in epilogue.split("\n")[-5:]:
                self._log(f"[bold magenta]{line}[/]")
        if multiverse and "No branching" not in multiverse:
            for line in multiverse.split("\n")[-6:]:
                self._log(f"[bold cyan]{line}[/]")

    async def _on_level_up(self, agent: str = "", level: int = 0, species: str = "", **_: Any) -> None:
        self._log(f"[bold yellow]★★★ {agent} LEVELED UP to Lv{level}! ★★★ {MOOD_EMOTES['excited']}[/]")
        # Celebration particles
        for _ in range(8):
            self._particles.append({
                "x": random.randint(10, 70),
                "y": random.randint(5, 15),
                "char": random.choice(["★", "♥", "♪", "✦", "☆"]),
            })

    async def _on_world_event(self, event_type: str = "", title: str = "", description: str = "", **_: Any) -> None:
        self._log(f"[bold magenta]~*~ WORLD EVENT: {title} ~*~[/]")
        self._log(f"  [dim]{description}[/]")

    async def _on_guild_formed(self, name: str = "", members: list[str] | None = None, synergy: float = 0, **_: Any) -> None:
        member_str = ", ".join(members or [])
        self._log(f"[bold cyan]♥♥ Guild formed: {name} ♥♥[/] ({member_str}) Synergy: +{synergy * 100:.0f}%")
        for _ in range(5):
            self._particles.append({
                "x": random.randint(10, 70),
                "y": random.randint(5, 15),
                "char": random.choice(["♥", "♡", "✦"]),
            })

    async def _on_campfire(self, stories: int = 0, **_: Any) -> None:
        self._log(f"[bold yellow]🔥 Campfire! {stories} stories shared under the stars~ 🔥[/]")

    async def _on_clock(self, sky: str = "", **_: Any) -> None:
        if sky:
            self._sky_line = sky

    async def _on_fortune(self, agent: str = "", fortune: str = "", **_: Any) -> None:
        self._log(f"[bold yellow]🥠 {agent} opens a fortune cookie: [italic]{fortune}[/][/]")

    async def _on_dream(self, agent: str = "", dream: str = "", dream_type: str = "", **_: Any) -> None:
        icons = {"prophetic": "🔮", "nightmare": "👻", "peaceful": "🌙", "surreal": "🌀"}
        icon = icons.get(dream_type, "💤")
        self._log(f"[dim]{icon} {agent} dreams: {dream}[/]")

    async def _on_boss_appeared(self, name: str = "", species: str = "", level: int = 0, hp: int = 0, **_: Any) -> None:
        self._log(f"[bold red]☠☠☠ BOSS APPEARED: {name} (Lv{level}, {hp}HP) ☠☠☠[/]")
        for _ in range(8):
            self._particles.append({
                "x": random.randint(5, 75),
                "y": random.randint(3, 15),
                "char": random.choice(["☠", "⚔", "✖", "!"]),
            })

    async def _on_boss_defeated(self, name: str = "", xp_reward: int = 0, **_: Any) -> None:
        self._log(f"[bold green]★★★ BOSS DEFEATED: {name}! +{xp_reward}XP to all! ★★★[/]")
        for _ in range(15):
            self._particles.append({
                "x": random.randint(5, 75),
                "y": random.randint(3, 15),
                "char": random.choice(["★", "♥", "✦", "♪", "☆", "♡"]),
            })

    async def _on_boss_damage(self, agent: str = "", message: str = "", **_: Any) -> None:
        self._log(f"[bold red]⚔ {message}[/]")

    async def _on_ghost(self, message: str = "", **_: Any) -> None:
        self._log(f"[dim italic]{message}[/]")

    def _log(self, msg: str) -> None:
        self._event_log.append(msg)
