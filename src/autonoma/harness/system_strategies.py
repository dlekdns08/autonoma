"""System-prompt framing strategies.

Each ``system.prompt_variant`` option maps to a short suffix appended to
the agent's persona system prompt. Upstream prompt-assembly code (see
``autonoma.agents.base``) picks the suffix via ``lookup``; the result is
a pure string, so mocking / testing is trivial.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register


@register("system.prompt_variant", "balanced")
def _balanced(_: dict[str, Any] | None = None) -> str:
    return ""


@register("system.prompt_variant", "concise")
def _concise(_: dict[str, Any] | None = None) -> str:
    return "\nRespond concisely. Prefer short, direct sentences over elaboration."


@register("system.prompt_variant", "elaborate")
def _elaborate(_: dict[str, Any] | None = None) -> str:
    return (
        "\nTake your time. Explain reasoning step-by-step when it helps collaborators "
        "understand your choices."
    )
