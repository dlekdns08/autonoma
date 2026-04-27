"""Happy-path coverage for ``autonoma.routers.playback``.

Playback is read-only over the ``session_checkpoint`` table. Tests
insert rows directly via SQLAlchemy then hit the HTTP endpoints.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(fresh_db) -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _login(client: AsyncClient, username: str = "playbackrouter") -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"username": username, "password": "password123"},
    )
    assert r.status_code == 201, r.text
    from autonoma.db.users import get_user_by_username, update_user_status
    user = await get_user_by_username(username)
    assert user is not None
    await update_user_status(user.id, "active")
    r = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert r.status_code == 200


async def _insert_checkpoint(session_id: int, round_number: int, payload: dict) -> None:
    from autonoma.db.engine import get_engine
    from autonoma.db.schema import session_checkpoint

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            session_checkpoint.insert().values(
                session_id=session_id,
                round_number=round_number,
                state_json=json.dumps(payload),
            )
        )


async def test_frames_for_unknown_session_returns_empty_list(
    client: AsyncClient,
) -> None:
    await _login(client)
    r = await client.get("/api/playback/424242/frames")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == 424242
    assert body["frames"] == []


async def test_frames_lists_inserted_checkpoints_newest_first(
    client: AsyncClient,
) -> None:
    await _login(client)

    session_id = 1234
    await _insert_checkpoint(session_id, 1, {"round": 1})
    await _insert_checkpoint(session_id, 3, {"round": 3})
    await _insert_checkpoint(session_id, 2, {"round": 2})
    # Different session — must NOT show up.
    await _insert_checkpoint(9999, 1, {"round": 1})

    r = await client.get(f"/api/playback/{session_id}/frames")
    assert r.status_code == 200
    body = r.json()
    rounds = [f["round"] for f in body["frames"]]
    # Sorted DESC by round_number.
    assert rounds == [3, 2, 1]
    assert all(isinstance(f["size_bytes"], int) and f["size_bytes"] > 0
               for f in body["frames"])


async def test_frame_returns_decoded_state(client: AsyncClient) -> None:
    await _login(client)

    session_id = 77
    payload = {"agents": ["alice", "bob"], "round": 5, "complete": False}
    await _insert_checkpoint(session_id, 5, payload)

    r = await client.get(f"/api/playback/{session_id}/frame/5")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == session_id
    assert body["round"] == 5
    assert body["state"] == payload


async def test_frame_404_when_round_missing(client: AsyncClient) -> None:
    await _login(client)
    await _insert_checkpoint(50, 1, {"round": 1})
    r = await client.get("/api/playback/50/frame/99")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "frame_not_found"
