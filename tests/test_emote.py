"""Emote-bubble emission lives off the speech path so we don't grow a
parallel state machine. These tests pin that contract: a `_say` from a
moody agent fires both the speech event and the matching emote, and a
neutral mood stays silent."""

from __future__ import annotations

import pytest

from autonoma.agents.base import AutonomousAgent, MOOD_EMOTE
from autonoma.event_bus import bus
from autonoma.models import AgentPersona
from autonoma.world import Mood


def _make_agent(name: str = "Tester") -> AutonomousAgent:
    persona = AgentPersona(
        name=name,
        role="coder",
        emoji="🧪",
        color="cyan",
        background="test agent",
        catchphrase="ping",
    )
    return AutonomousAgent(persona=persona)


@pytest.mark.asyncio
async def test_say_emits_emote_for_known_mood() -> None:
    agent = _make_agent()
    agent.mood = Mood.EXCITED

    emotes: list[dict] = []
    bus.on("agent.emote", lambda **kw: emotes.append(kw))

    await agent._say("we did it!")

    assert len(emotes) == 1
    assert emotes[0]["agent"] == "Tester"
    assert emotes[0]["icon"] == MOOD_EMOTE["excited"]
    assert emotes[0]["ttl_ms"] > 0


@pytest.mark.asyncio
async def test_say_skips_emote_when_mood_has_no_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a mood is missing from the table we want silence, not a crash
    or a default icon — better to under-emote than spam."""
    agent = _make_agent()
    monkeypatch.setitem(MOOD_EMOTE, "excited", "")  # blank → suppressed
    agent.mood = Mood.EXCITED

    emotes: list[dict] = []
    bus.on("agent.emote", lambda **kw: emotes.append(kw))

    await agent._say("hi")

    assert emotes == []


@pytest.mark.asyncio
async def test_emote_helper_is_independent_of_say() -> None:
    """Other code paths (level-up, ghost spawn, etc.) can call _emote
    directly without going through speech. Verify the helper works
    standalone."""
    agent = _make_agent()
    received: list[dict] = []
    bus.on("agent.emote", lambda **kw: received.append(kw))

    await agent._emote("⭐", ttl_ms=1500)

    assert received == [{"agent": "Tester", "icon": "⭐", "ttl_ms": 1500}]
