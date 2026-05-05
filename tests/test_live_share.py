"""Tests for the live-share router (`/api/live-share/*`).

The directory feed reads ``autonoma.api._rooms`` directly. To test it
without spinning up the full WebSocket lifecycle we craft minimal
``RoomState`` / ``SessionState`` instances by hand and drop them into
the module-level dicts. The ``_clear`` fixture wipes everything before
and after each test so cross-test contamination is impossible.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.fixture(autouse=True)
def _clear_room_state() -> Any:
    """Wipe ``_rooms`` / ``_short_codes`` / ``_sessions`` before+after.

    Live-share reads from these globals; previous tests may have
    populated them via WebSocket connect/disconnect handlers, and
    the autouse ``_reset`` only clears the bus.
    """
    from autonoma import api as _api

    rooms_before = dict(_api._rooms)
    codes_before = dict(_api._short_codes)
    sessions_before = dict(_api._sessions)
    _api._rooms.clear()
    _api._short_codes.clear()
    _api._sessions.clear()
    yield
    _api._rooms.clear()
    _api._short_codes.clear()
    _api._sessions.clear()
    _api._rooms.update(rooms_before)
    _api._short_codes.update(codes_before)
    _api._sessions.update(sessions_before)


def _add_fake_room(
    *,
    room_id: int,
    code: str,
    is_public: bool = True,
    title: str = "",
    description: str = "",
    owner_user_id: str = "",
    owner_display: str = "",
    started_at_offset: float = -60.0,
) -> None:
    """Inject a fully-formed RoomState + matching owner SessionState."""
    from autonoma import api as _api

    room = _api.RoomState(
        room_id=room_id,
        owner_session_id=room_id,
        short_code=code,
        is_public=is_public,
        public_title=title,
        public_description=description,
        started_at=time.time() + started_at_offset,
    )
    _api._rooms[room_id] = room
    _api._short_codes[code] = room_id

    # Fake owner session — we only need the fields _build_card reads.
    sess = _api.SessionState(
        ws=_api._HeadlessWebSocket(),  # type: ignore[arg-type]
        session_id=room_id,
        owner_user_id=owner_user_id,
        room_id=room_id,
        display_name=owner_display,
    )
    _api._sessions[room_id] = sess


# ── Public reads ──────────────────────────────────────────────────────


async def test_directory_lists_only_public(client: AsyncClient) -> None:
    _add_fake_room(room_id=1, code="ABCD", is_public=True, title="Public room")
    _add_fake_room(room_id=2, code="WXYZ", is_public=False, title="Private")

    r = await client.get("/api/live-share/sessions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    codes = {s["room_code"] for s in body["sessions"]}
    assert codes == {"ABCD"}


async def test_directory_sort_busiest_then_freshest(client: AsyncClient) -> None:
    _add_fake_room(room_id=1, code="OLD", started_at_offset=-3600.0)
    _add_fake_room(room_id=2, code="NEW", started_at_offset=-30.0)

    r = await client.get("/api/live-share/sessions")
    body = r.json()
    # Both have viewer_count=0 (just the owner), so freshness wins.
    assert [s["room_code"] for s in body["sessions"]] == ["NEW", "OLD"]


async def test_session_by_code_returns_card(client: AsyncClient) -> None:
    _add_fake_room(
        room_id=1,
        code="GO42",
        title="My Live Show",
        description="Watch the swarm cook",
    )

    r = await client.get("/api/live-share/sessions/GO42")
    assert r.status_code == 200
    s = r.json()["session"]
    assert s["room_code"] == "GO42"
    assert s["title"] == "My Live Show"
    assert s["description"] == "Watch the swarm cook"
    assert s["is_public"] is True


async def test_session_by_code_404_for_unknown(client: AsyncClient) -> None:
    r = await client.get("/api/live-share/sessions/NOPE")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "room_not_found"


async def test_session_by_code_404_for_private(client: AsyncClient) -> None:
    """Private rooms 404 to the same shape as unknown — no enumeration."""
    _add_fake_room(room_id=1, code="PRIV", is_public=False)

    r = await client.get("/api/live-share/sessions/PRIV")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "room_not_found"


# ── Owner-gated visibility flip ───────────────────────────────────────


async def _signup_and_get_user_id(client: AsyncClient, name: str) -> str:
    from autonoma.db.users import get_user_by_username, update_user_status

    r = await client.post(
        "/api/auth/signup",
        json={"username": name, "password": "password123"},
    )
    assert r.status_code == 201, r.text
    user = await get_user_by_username(name)
    assert user is not None
    await update_user_status(user.id, "active")
    r = await client.post(
        "/api/auth/login",
        json={"username": name, "password": "password123"},
    )
    assert r.status_code == 200
    return user.id


async def test_visibility_404_when_no_room(client: AsyncClient) -> None:
    await _signup_and_get_user_id(client, "alice_share")
    r = await client.post(
        "/api/live-share/visibility",
        json={"public": True, "title": "x"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "no_owned_room"


async def test_visibility_flips_owner_room(client: AsyncClient) -> None:
    uid = await _signup_and_get_user_id(client, "bob_share")
    _add_fake_room(
        room_id=42,
        code="BOB1",
        is_public=False,
        owner_user_id=uid,
        owner_display="Bob",
    )

    r = await client.post(
        "/api/live-share/visibility",
        json={"public": True, "title": "Bob's stream", "description": "fun"},
    )
    assert r.status_code == 200, r.text
    s = r.json()["session"]
    assert s["is_public"] is True
    assert s["title"] == "Bob's stream"
    assert s["description"] == "fun"

    # Sanity: it now appears in the public directory.
    listing = (await client.get("/api/live-share/sessions")).json()
    assert any(x["room_code"] == "BOB1" for x in listing["sessions"])


async def test_visibility_flip_off_clears_metadata(client: AsyncClient) -> None:
    uid = await _signup_and_get_user_id(client, "carol_share")
    _add_fake_room(
        room_id=99,
        code="CAR9",
        is_public=True,
        title="seeded",
        description="desc",
        owner_user_id=uid,
    )

    r = await client.post("/api/live-share/visibility", json={"public": False})
    assert r.status_code == 200
    s = r.json()["session"]
    assert s["is_public"] is False
    assert s["description"] == ""

    # The card's ``title`` falls back to "Live swarm" for display, but
    # the underlying RoomState fields should be wiped.
    from autonoma import api as _api

    assert _api._rooms[99].public_title == ""
    assert _api._rooms[99].public_description == ""

    # And it's gone from the directory.
    listing = (await client.get("/api/live-share/sessions")).json()
    assert all(x["room_code"] != "CAR9" for x in listing["sessions"])


async def test_concurrent_visibility_flips_safe(
    client: AsyncClient,
) -> None:
    """Each user's flip must touch ONLY their own room.

    The AsyncClient session-cookie state is not safely shareable across
    concurrent coroutines, so we serialise the two POSTs but assert that
    neither user's request leaks into the other's room. Alice flipping
    her room public must NOT flip Bob's, and vice versa.
    """
    uid_alice = await _signup_and_get_user_id(client, "alice_concurrent")
    _add_fake_room(
        room_id=101,
        code="ALICE",
        is_public=False,
        owner_user_id=uid_alice,
        owner_display="Alice",
    )

    # Alice flips her room public.
    r = await client.post(
        "/api/live-share/visibility",
        json={"public": True, "title": "Alice live"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["session"]["room_code"] == "ALICE"

    from autonoma import api as _api

    assert _api._rooms[101].is_public is True
    assert _api._rooms[101].public_title == "Alice live"

    # Now log out alice and switch to bob in the same client.
    await client.post("/api/auth/logout")

    uid_bob = await _signup_and_get_user_id(client, "bob_concurrent")
    _add_fake_room(
        room_id=202,
        code="BOBBB",
        is_public=False,
        owner_user_id=uid_bob,
        owner_display="Bob",
    )

    # Sanity: Bob's room is still private — Alice's flip did NOT touch it.
    assert _api._rooms[202].is_public is False
    assert _api._rooms[202].public_title == ""

    # Bob flips his own room.
    r = await client.post(
        "/api/live-share/visibility",
        json={"public": True, "title": "Bob live"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["session"]["room_code"] == "BOBBB"

    # Both rooms are now public, each carries its own metadata.
    assert _api._rooms[101].is_public is True
    assert _api._rooms[101].public_title == "Alice live"
    assert _api._rooms[202].is_public is True
    assert _api._rooms[202].public_title == "Bob live"

    # Bob flipping his back to private must not affect Alice's.
    r = await client.post("/api/live-share/visibility", json={"public": False})
    assert r.status_code == 200
    assert _api._rooms[202].is_public is False
    assert _api._rooms[101].is_public is True, "Bob's private flip leaked into Alice's room"


async def test_visibility_does_not_touch_other_users_rooms(
    client: AsyncClient,
) -> None:
    """Alice can't flip Bob's room visibility just because the code is known."""
    uid_alice = await _signup_and_get_user_id(client, "alice_share2")
    _add_fake_room(
        room_id=10,
        code="BOB99",
        is_public=False,
        owner_user_id="bob-other-uid",  # NOT alice
    )
    # Alice has no own room → 404 (we never let her flip Bob's).
    r = await client.post("/api/live-share/visibility", json={"public": True})
    assert r.status_code == 404
    # Bob's room is still private.
    from autonoma import api as _api

    assert _api._rooms[10].is_public is False
    # uid_alice is checked for in tests as "the right user authenticated".
    assert uid_alice  # silence unused-var warning
