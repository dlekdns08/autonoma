"""Async Anthropic client for the Managed Agents surfaces."""

from __future__ import annotations

from typing import Any

from autonoma.config import settings

MANAGED_BETA = "managed-agents-2026-04-01"
MEMORY_BETA = "agent-memory-2026-07-22"
DREAM_BETA = "dreaming-2026-04-21"


def is_configured() -> bool:
    """Managed Agents need the Anthropic key (first-party API only)."""
    return settings.provider == "anthropic" and bool(settings.anthropic_api_key)


def get_client() -> Any:
    import anthropic

    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
