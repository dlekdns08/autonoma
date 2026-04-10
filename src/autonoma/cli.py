"""CLI entry points for Autonoma."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()

BANNER = """[bold magenta]
     ╔═╗╦ ╦╔╦╗╔═╗╔╗╔╔═╗╔╦╗╔═╗
     ╠═╣║ ║ ║ ║ ║║║║║ ║║║║╠═╣
     ╩ ╩╚═╝ ╩ ╚═╝╝╚╝╚═╝╩ ╩╩ ╩
[/][dim]     Self-Organizing Agent Swarm v0.1.0[/]
"""


def _check_api_key() -> bool:
    """Validate that an API key is configured before running."""
    from autonoma.config import settings

    if not settings.anthropic_api_key:
        console.print(
            "[bold red]Error:[/] No Anthropic API key configured.\n"
            "Set the [bold]ANTHROPIC_API_KEY[/] environment variable or add it to .env"
        )
        return False
    return True


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """Autonoma - Self-organizing agent swarm with animated TUI."""


@cli.command()
@click.argument("description")
@click.option("--name", "-n", default=None, help="Project name")
@click.option("--rounds", "-r", default=30, help="Maximum rounds")
@click.option("--no-animate", is_flag=True, help="Disable animated TUI")
@click.option("--output", "-o", default=None, type=click.Path(), help="Output directory for generated files")
def build(description: str, name: str | None, rounds: int, no_animate: bool, output: str | None) -> None:
    """Build a project from a description using autonomous agents."""
    console.print(BANNER)

    if not _check_api_key():
        sys.exit(1)

    project_name = name or _derive_name(description)
    output_dir = Path(output) if output else None

    from autonoma.engine import AutonomaEngine

    engine = AutonomaEngine(console, output_dir=output_dir)

    async def _run():
        return await engine.run(
            name=project_name,
            description=description,
            max_rounds=rounds,
            animate=not no_animate,
        )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted.[/]")


@cli.command()
def interactive() -> None:
    """Interactive mode - describe your project step by step."""
    console.print(BANNER)

    if not _check_api_key():
        sys.exit(1)

    name = Prompt.ask("[bold cyan]Project name[/]")
    description = Prompt.ask("[bold cyan]What should it do?[/]")
    rounds = int(Prompt.ask("[bold cyan]Max rounds[/]", default="30"))

    console.print(Panel(
        f"[bold]{name}[/]: {description}\nRounds: {rounds}",
        title="[bold cyan]Configuration[/]",
    ))

    if Prompt.ask("Start?", choices=["y", "n"], default="y") != "y":
        return

    from autonoma.engine import AutonomaEngine

    engine = AutonomaEngine(console)
    try:
        asyncio.run(engine.run(name=name, description=description, max_rounds=rounds))
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted.[/]")


@cli.command()
def demo() -> None:
    """Run a demo build to see the swarm in action."""
    console.print(BANNER)

    if not _check_api_key():
        sys.exit(1)

    console.print("[bold cyan]Running demo: Building a URL shortener...[/]\n")

    from autonoma.engine import AutonomaEngine

    engine = AutonomaEngine(console)
    try:
        asyncio.run(engine.run(
            name="url-shortener",
            description="A URL shortener service with FastAPI, SQLite storage, click tracking, and a simple HTML frontend",
            max_rounds=20,
        ))
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted.[/]")


def _derive_name(desc: str) -> str:
    words = desc.lower().split()[:3]
    return "-".join(w for w in words if w.isalpha())[:30] or "project"


if __name__ == "__main__":
    cli()
