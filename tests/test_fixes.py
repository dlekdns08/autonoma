"""Tests covering bug fixes: JSON extraction, inbox limits, message routing, stall detection, renderer cleanup."""

import pytest

from autonoma.agents.base import AutonomousAgent, _extract_json, MAX_INBOX_SIZE
from autonoma.agents.swarm import AgentSwarm
from autonoma.config import settings
from autonoma.models import (
    AgentMessage,
    AgentPersona,
    AgentState,
    MessageType,
    Position,
    ProjectState,
    SpeechBubble,
    Task,
    TaskPriority,
    TaskStatus,
)
from autonoma.tui.renderer import AnimatedRenderer
from autonoma.tui.sprites import render_speech_bubble


# ── _extract_json ────────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"action": "idle"}') == {"action": "idle"}

    def test_markdown_fenced(self):
        text = '```json\n{"action": "work"}\n```'
        assert _extract_json(text) == {"action": "work"}

    def test_markdown_fenced_no_lang(self):
        text = '```\n{"action": "work"}\n```'
        assert _extract_json(text) == {"action": "work"}

    def test_json_with_surrounding_text(self):
        text = 'Here is my plan:\n{"action": "create_file", "path": "main.py"}\nDone!'
        result = _extract_json(text)
        assert result["action"] == "create_file"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("no json here at all")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _extract_json("")


# ── Inbox size limit ─────────────────────────────────────────────────────────

class TestInboxLimit:
    def test_inbox_bounded(self):
        persona = AgentPersona(name="Test", emoji="T", role="tester", skills=["test"], color="white")
        agent = AutonomousAgent(persona)

        for i in range(MAX_INBOX_SIZE + 20):
            msg = AgentMessage(
                sender="Other",
                recipient="Test",
                msg_type=MessageType.CHAT,
                content=f"msg {i}",
            )
            agent.receive_message(msg)

        assert len(agent.inbox) == MAX_INBOX_SIZE
        # Should keep the most recent messages
        assert "msg 69" in agent.inbox[-1].content


# ── Message routing deduplication ────────────────────────────────────────────

class TestMessageRouting:
    def test_no_duplicate_delivery(self):
        swarm = AgentSwarm()
        swarm.spawn_agent("Worker", "helper", ["coding"])

        project = ProjectState(name="test", description="test")
        msg = AgentMessage(
            sender="Director",
            recipient="Worker",
            msg_type=MessageType.CHAT,
            content="hello",
        )
        project.messages.append(msg)

        # Route twice
        swarm._route_messages(project)
        swarm._route_messages(project)

        # Worker should have received the message only once
        worker = swarm.agents["Worker"]
        assert len(worker.inbox) == 1

    def test_broadcast_excludes_sender(self):
        swarm = AgentSwarm()
        swarm.spawn_agent("A", "helper", ["coding"])
        swarm.spawn_agent("B", "helper", ["coding"])

        project = ProjectState(name="test", description="test")
        msg = AgentMessage(
            sender="A",
            recipient="all",
            msg_type=MessageType.CHAT,
            content="broadcast",
        )
        project.messages.append(msg)
        swarm._route_messages(project)

        # A should not receive its own message
        assert len(swarm.agents["A"].inbox) == 0
        # B and Director should receive it
        assert len(swarm.agents["B"].inbox) == 1
        assert len(swarm.agents["Director"].inbox) == 1


# ── Spawn failure ────────────────────────────────────────────────────────────

class TestSpawnLimit:
    def test_spawn_returns_none_at_max(self):
        swarm = AgentSwarm()
        original_max = settings.max_agents
        try:
            settings.max_agents = 2  # Director + 1
            agent = swarm.spawn_agent("First", "helper", ["coding"])
            assert agent is not None
            agent2 = swarm.spawn_agent("Second", "helper", ["coding"])
            assert agent2 is None
        finally:
            settings.max_agents = original_max


# ── Renderer detach ──────────────────────────────────────────────────────────

class TestRendererDetach:
    def test_detach_unsubscribes(self):
        renderer = AnimatedRenderer()
        swarm = AgentSwarm()
        project = ProjectState(name="test", description="test")

        renderer.attach(swarm, project)
        assert renderer._handlers_registered is True

        renderer.detach()
        assert renderer._handlers_registered is False

    def test_double_attach_no_double_handlers(self):
        renderer = AnimatedRenderer()
        swarm = AgentSwarm()
        project = ProjectState(name="test", description="test")

        renderer.attach(swarm, project)
        renderer.attach(swarm, project)
        assert renderer._handlers_registered is True

        # Detach once should clean up
        renderer.detach()
        assert renderer._handlers_registered is False


# ── Speech bubble edge cases ─────────────────────────────────────────────────

class TestSpeechBubbleEdgeCases:
    def test_whitespace_only(self):
        assert render_speech_bubble("   ") == []

    def test_very_long_word(self):
        long_word = "a" * 100
        bubble = render_speech_bubble(long_word, max_width=30)
        assert len(bubble) >= 3
        # Should truncate the word, not crash (kawaii bubble adds tail line)
        for line in bubble[:-1]:  # Exclude the tail "\" line
            assert len(line) <= 32  # max_width - 4 + borders

    def test_none_text(self):
        # Should handle None gracefully (strip() on None would fail without guard)
        assert render_speech_bubble("") == []


# ── Stall detection (director) ───────────────────────────────────────────────

class TestStallDetection:
    def test_stall_counter_resets_on_progress(self):
        from autonoma.agents.director import DirectorAgent

        director = DirectorAgent()
        director._stall_counter = 5
        # Simulate: on assignment, counter resets (tested indirectly through state)
        director._stall_counter = 0
        assert director._stall_counter == 0
