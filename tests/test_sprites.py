"""Tests for kawaii sprite rendering."""

from autonoma.models import AgentState
from autonoma.tui.sprites import get_sprite, render_nametag, render_speech_bubble


def test_get_sprite_idle():
    lines = get_sprite(AgentState.IDLE, "🤖")
    assert len(lines) == 4  # Kawaii sprites are 4 lines tall
    assert "🤖" in lines[0]


def test_get_sprite_working_frames():
    lines0 = get_sprite(AgentState.WORKING, "⚡", frame=0)
    lines2 = get_sprite(AgentState.WORKING, "⚡", frame=2)
    assert len(lines0) == 4
    assert len(lines2) == 4


def test_get_sprite_celebrating():
    lines = get_sprite(AgentState.CELEBRATING, "🎉", frame=0)
    assert len(lines) == 4


def test_speech_bubble():
    bubble = render_speech_bubble("Hello world!")
    assert len(bubble) >= 3
    assert "Hello world!" in " ".join(bubble)
    # Kawaii bubble uses . and ~ borders
    assert "." in bubble[0]
    assert "~" in bubble[0]


def test_speech_bubble_empty():
    assert render_speech_bubble("") == []


def test_speech_bubble_long_text():
    bubble = render_speech_bubble("This is a much longer text that should wrap across multiple lines in the bubble")
    assert len(bubble) > 4  # Should have multiple content lines


def test_speech_bubble_with_mood():
    bubble = render_speech_bubble("Hello!", mood="happy")
    joined = " ".join(bubble)
    assert "(^w^)" in joined  # Mood emoticon added


def test_nametag():
    tag = render_nametag("Coder", "cyan")
    assert "Coder" in tag
    assert "cyan" in tag
    assert "~" in tag  # Kawaii brackets
