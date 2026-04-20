"""Main engine: orchestrates swarm + TUI + workspace in one unified loop."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from autonoma.agents.swarm import AgentSwarm
from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.models import ProjectState, TaskStatus
from autonoma.progress import ProgressTracker
from autonoma.tui.renderer import AnimatedRenderer
from autonoma.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class AutonomaEngine:
    """Unified engine that runs the autonomous swarm with animated TUI."""

    def __init__(self, console: Console | None = None, output_dir: Path | None = None) -> None:
        self.console = console or Console()
        self.swarm = AgentSwarm()
        self.renderer = AnimatedRenderer(self.console)
        self.workspace = WorkspaceManager()
        self._output_dir = output_dir

    async def run(
        self,
        name: str,
        description: str,
        max_rounds: int = 30,
        animate: bool = True,
    ) -> ProjectState:
        """Run the full autonomous pipeline with animation."""
        project = ProjectState(name=name, description=description)

        try:
            # Attach renderer inside the try so the outer `finally` always
            # runs `detach()` even if attach itself partially registers
            # handlers before raising.
            self.renderer.attach(self.swarm, project)
            # Phase 1: Director plans
            self.console.print(Panel(
                f"[bold]{name}[/]: {description}",
                title="[bold magenta]Autonoma[/]",
                border_style="magenta",
            ))

            await self.swarm.initialize(project)

            # Add spawned agents to project state
            for agent_name, agent in self.swarm.agents.items():
                if not any(a.name == agent_name for a in project.agents):
                    project.agents.append(agent.persona)

            # Phase 2: Animated swarm execution
            if animate:
                await self._run_animated(project, max_rounds)
            else:
                await self._run_headless(project, max_rounds)

            # Phase 3: Write files to disk
            if project.files:
                out = self._output_dir or settings.output_dir / name
                await self.workspace.write_all(project)
                self.console.print(f"\n[bold green]Files written to:[/] {out}")

        except KeyboardInterrupt:
            self.console.print("\n[bold yellow]Interrupted by user. Shutting down...[/]")
            self.swarm.stop()
        finally:
            # Always clean up event handlers
            self.renderer.detach()

            # Save progress for cross-session continuity
            out = self._output_dir or settings.output_dir / name
            tracker = ProgressTracker(out)
            tracker.save(project)
            self.console.print(f"[dim]Progress saved to {out / 'autonoma-progress.json'}[/]")

        # Phase 4: Summary
        self._print_summary(project)
        return project

    async def _run_animated(self, project: ProjectState, max_rounds: int) -> None:
        """Run with Rich Live animated TUI."""
        refresh = max(1, int(1 / settings.tick_rate))
        with Live(
            self.renderer.render(),
            console=self.console,
            refresh_per_second=refresh,
        ) as live:
            run_task = asyncio.create_task(
                self.swarm.run(project, max_rounds=max_rounds)
            )

            try:
                while not run_task.done():
                    live.update(self.renderer.render())
                    await asyncio.sleep(settings.tick_rate)
                # Final render
                live.update(self.renderer.render())
            except (asyncio.CancelledError, KeyboardInterrupt):
                self.swarm.stop()
                if not run_task.done():
                    run_task.cancel()
                    try:
                        await run_task
                    except asyncio.CancelledError:
                        pass
                return

            # Collect result (may raise if swarm had unhandled error)
            await run_task

    async def _run_headless(self, project: ProjectState, max_rounds: int) -> None:
        """Run without TUI, just console output."""

        async def on_speech(agent: str = "", text: str = "", **_: Any) -> None:
            self.console.print(f"  [{agent}] {text}")

        async def on_file(agent: str = "", path: str = "", **_: Any) -> None:
            self.console.print(f"  [{agent}] created {path}")

        # Register each handler inside try/finally so that even if a later
        # bus.on(...) raises we still unregister the ones that succeeded,
        # and swarm.run() errors always release our subscriptions.
        _headless_handlers: list[tuple[str, Any]] = []
        try:
            bus.on("agent.speech", on_speech)
            _headless_handlers.append(("agent.speech", on_speech))
            bus.on("file.created", on_file)
            _headless_handlers.append(("file.created", on_file))

            await self.swarm.run(project, max_rounds=max_rounds)
        finally:
            # Unsubscribe every successfully-registered handler, ignoring
            # any secondary failures so we never mask the original error.
            for event, handler in _headless_handlers:
                try:
                    bus.off(event, handler)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Failed to unregister headless handler %s for %s: %r",
                        handler,
                        event,
                        exc,
                    )

    def _print_summary(self, project: ProjectState) -> None:
        table = Table(title="🌟 Build Summary")
        table.add_column("Metric", style="bold")
        table.add_column("Value")

        done = sum(1 for t in project.tasks if t.status == TaskStatus.DONE)
        total = len(project.tasks)
        status = "✅ Complete" if project.completed else "⚠️ Incomplete"

        table.add_row("Status", status)
        table.add_row("Tasks", f"{done}/{total} done")
        table.add_row("Files Created", str(len(project.files)))
        table.add_row("Agents Used", str(len(project.agents)))
        table.add_row(
            "Duration",
            f"{(datetime.now() - project.started_at).total_seconds():.1f}s"
        )

        self.console.print(table)

        if project.agents:
            agent_table = Table(title="Agent Team")
            agent_table.add_column("Agent", style="bold")
            agent_table.add_column("Role")
            for a in project.agents:
                agent_table.add_row(f"{a.emoji} {a.name}", a.role)
            self.console.print(agent_table)
