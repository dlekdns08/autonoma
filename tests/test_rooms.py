"""Room/viewer model tests.

We hit the helpers directly instead of standing up a real WebSocket
because the contract worth pinning is in the data layer: room + short
code + viewer fan-out. WS auth/lifecycle is exercised manually and
already covered by smoke runs of the live server.
"""

from __future__ import annotations

import pytest

from autonoma.api import (
    RoomState,
    SessionState,
    _create_room_for,
    _generate_short_code,
    _rooms,
    _short_codes,
    _sessions,
    _viewers_in_room,
)


@pytest.fixture(autouse=True)
def _isolate_room_registry() -> None:
    """The api module keeps these registries process-global. Each test
    deserves a clean slate so an earlier test's leftovers can't make a
    later one accidentally pass."""
    _sessions.clear()
    _rooms.clear()
    _short_codes.clear()
    yield
    _sessions.clear()
    _rooms.clear()
    _short_codes.clear()


def _make_session(session_id: int, name: str = "") -> SessionState:
    """Build a SessionState without a real ws — none of these tests
    actually send anything, they just exercise the registry logic."""
    s = SessionState(ws=None, session_id=session_id)  # type: ignore[arg-type]
    s.room_id = session_id
    s.display_name = name
    _sessions[session_id] = s
    return s


def test_short_code_is_uppercase_alnum_and_safe_alphabet() -> None:
    """Codes have to read aloud cleanly — no 0/1/I/O confusables."""
    code = _generate_short_code()
    assert len(code) == 6
    forbidden = set("01IO")
    assert not any(c in forbidden for c in code)
    assert code == code.upper()


def test_short_code_is_unique_under_load() -> None:
    """Allocating many codes back-to-back must not collide; the
    generator retries internally rather than handing out a duplicate."""
    seen = set()
    for _ in range(200):
        code = _generate_short_code()
        # Reserve it so the generator's "skip if already taken" path
        # actually has something to skip past.
        _short_codes[code] = -1
        assert code not in seen
        seen.add(code)


def test_create_room_routes_session_into_it() -> None:
    """After ``_create_room_for(s)`` the session points at a fresh
    room, the short code is registered, and the proxy properties on the
    session hit the room object."""
    host = _make_session(100)
    room = _create_room_for(host)

    assert room.room_id == host.session_id
    assert room.owner_session_id == host.session_id
    assert host.room_id == room.room_id
    assert _short_codes[room.short_code] == room.room_id
    # Proxy properties read through to the room.
    room.swarm = "fake-swarm"
    assert host.swarm == "fake-swarm"


def test_viewers_in_room_groups_by_room_id() -> None:
    """Viewers who joined a host's room must be returned together; a
    stranger sitting in a private room of their own must not."""
    host = _make_session(1)
    room = _create_room_for(host)

    viewer = _make_session(2)
    viewer.room_id = room.room_id

    stranger = _make_session(3)  # stays in its own private room

    members = {s.session_id for s in _viewers_in_room(room.room_id)}
    assert members == {host.session_id, viewer.session_id}
    assert stranger.session_id not in members


def test_session_proxy_properties_handle_missing_room() -> None:
    """A session that points at a room which has been torn down (host
    disconnected) should silently report None instead of crashing."""
    orphan = _make_session(99)
    orphan.room_id = 12345  # never registered
    assert orphan.swarm is None
    assert orphan.project is None
    assert orphan.task is None
