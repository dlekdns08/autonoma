"""Tests for the viewer chat-command bridge (``!cheer Alex`` etc.).

These exercise the extracted ``_handle_viewer_command`` helper directly
— driving the full WebSocket lifecycle is overkill for what's a small
parser + bus-emit dispatcher. The chat fan-out (``viewer.chat``)
upstream of the bridge is the WS handler's responsibility and is not
under test here; we focus on:

* Known verbs (``cheer`` / ``cookie`` / ``boo``) resolve to the right
  icon and fire ``agent.emote`` with ``ttl_ms=2500``.
* Unknown verbs do not emit.
* Throttle: a second command inside the 4-second window drops
  silently.
"""

from __future__ import annotations

from typing import Any

import pytest

from autonoma import api as api_mod
from autonoma.api import _handle_viewer_command
from autonoma.event_bus import bus


# ── stub swarm + room helpers ──────────────────────────────────────────


class _StubSwarm:
    def __init__(self, agents: list[str]) -> None:
        self.agents = {name: object() for name in agents}


@pytest.fixture(autouse=True)
def _install_fake_room() -> Any:
    """Drop a single room (id=100) with a stub swarm containing
    ``Alex``. Reset on teardown so other tests aren't affected."""
    rooms_snap = dict(api_mod._rooms)
    api_mod._rooms.clear()
    room = api_mod.RoomState(
        room_id=100,
        owner_session_id=100,
        short_code="ABC123",
    )
    room.swarm = _StubSwarm(["Alex", "Bobby"])
    api_mod._rooms[100] = room
    yield
    api_mod._rooms.clear()
    api_mod._rooms.update(rooms_snap)


@pytest.fixture
def emote_sink() -> list[dict[str, Any]]:
    """Subscribe to ``agent.emote`` and stash payloads."""
    captured: list[dict[str, Any]] = []

    async def _on(**data: Any) -> None:
        captured.append(data)

    bus.on("agent.emote", _on)
    return captured


# ── Known verbs ────────────────────────────────────────────────────────


async def test_cheer_emits_sparkle_emote(
    emote_sink: list[dict[str, Any]],
) -> None:
    emitted, last_at = await _handle_viewer_command(
        "!cheer Alex",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=10.0,
    )
    assert emitted is True
    assert last_at == 10.0
    assert len(emote_sink) == 1
    payload = emote_sink[0]
    assert payload == {"agent": "Alex", "icon": "✨", "ttl_ms": 2500}


async def test_cookie_and_boo_emit_correct_icons(
    emote_sink: list[dict[str, Any]],
) -> None:
    await _handle_viewer_command(
        "!cookie Alex",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=1.0,
    )
    await _handle_viewer_command(
        "!boo Alex",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=2.0,
    )
    icons = [p["icon"] for p in emote_sink]
    assert "🍪" in icons
    assert "👎" in icons


# ── Unknown verb ──────────────────────────────────────────────────────


async def test_unknown_verb_is_silent_no_emote(
    emote_sink: list[dict[str, Any]],
) -> None:
    emitted, last_at = await _handle_viewer_command(
        "!sing Alex",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=10.0,
    )
    assert emitted is False
    # Throttle state is NOT advanced when nothing fired.
    assert last_at == 0.0
    assert emote_sink == []


async def test_unknown_agent_is_silent_no_emote(
    emote_sink: list[dict[str, Any]],
) -> None:
    emitted, last_at = await _handle_viewer_command(
        "!cheer Nobody",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=10.0,
    )
    assert emitted is False
    assert last_at == 0.0
    assert emote_sink == []


# ── Throttle ──────────────────────────────────────────────────────────


async def test_back_to_back_within_window_drops_second(
    emote_sink: list[dict[str, Any]],
) -> None:
    # First fires at t=10.
    emitted1, last1 = await _handle_viewer_command(
        "!cheer Alex",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=10.0,
    )
    # Second fires at t=12 (2s later, well within the 4s window).
    emitted2, last2 = await _handle_viewer_command(
        "!cheer Alex",
        session_id=100,
        room_id=100,
        last_command_at=last1,
        now=12.0,
    )
    assert emitted1 is True
    assert emitted2 is False
    # Throttle preserves the earlier timestamp so subsequent calls
    # measure from the actual emit, not the throttled attempt.
    assert last2 == 10.0
    # Only one emote fired.
    assert len(emote_sink) == 1


async def test_after_window_second_fires(
    emote_sink: list[dict[str, Any]],
) -> None:
    emitted1, last1 = await _handle_viewer_command(
        "!cheer Alex",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=10.0,
    )
    # Past the 4s window — should fire.
    emitted2, last2 = await _handle_viewer_command(
        "!cheer Alex",
        session_id=100,
        room_id=100,
        last_command_at=last1,
        now=15.0,
    )
    assert emitted1 is True
    assert emitted2 is True
    assert last2 == 15.0
    assert len(emote_sink) == 2


# ── Defensive: non-command text is ignored ────────────────────────────


async def test_non_command_text_returns_false(
    emote_sink: list[dict[str, Any]],
) -> None:
    emitted, last_at = await _handle_viewer_command(
        "hello world",
        session_id=100,
        room_id=100,
        last_command_at=0.0,
        now=10.0,
    )
    assert emitted is False
    assert last_at == 0.0
    assert emote_sink == []
