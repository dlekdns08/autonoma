"""Generated-gesture store, swarm hook, and the /api/gestures router."""

from __future__ import annotations

import base64
import gzip
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from autonoma.auth import hash_password
from autonoma.db.users import create_user
from autonoma.gestures import service
from autonoma.gestures.choreographer import CharacterContext

pytestmark = pytest.mark.usefixtures("fresh_db")


@pytest.fixture(autouse=True)
async def _init_db(fresh_db) -> None:
    from autonoma.db.engine import init_db

    await init_db()


@pytest.fixture(autouse=True)
def _clear_inflight() -> None:
    service._inflight.clear()


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event: str, **data: Any) -> None:
        self.events.append((event, data))


@pytest.fixture
def fake_bus(monkeypatch: pytest.MonkeyPatch) -> FakeBus:
    b = FakeBus()
    monkeypatch.setattr(service, "bus", b)
    return b


@pytest.mark.asyncio
async def test_ensure_gesture_composes_once_and_caches(fake_bus: FakeBus) -> None:
    ctx = CharacterContext(name="Ada", role="coder")
    g1 = await service.ensure_gesture(
        agent_name="Ada", kind="state", value="celebrating", ctx=ctx, client=None
    )
    g2 = await service.ensure_gesture(
        agent_name="Ada", kind="state", value="celebrating", ctx=ctx, client=None
    )
    assert g1.id == g2.id
    assert g1.source == "fallback"
    assert g1.clip_ref == f"gen:{g1.id}"
    assert [n for n, _ in fake_bus.events] == ["gesture.generated"]
    assert fake_bus.events[0][1]["clip_ref"] == g1.clip_ref

    rows = await service.list_for_agent("Ada")
    assert [r.id for r in rows] == [g1.id]


@pytest.mark.asyncio
async def test_force_regenerate_retires_previous(fake_bus: FakeBus) -> None:
    ctx = CharacterContext(name="Ada")
    g1 = await service.ensure_gesture(
        agent_name="Ada", kind="emote", value="♪", ctx=ctx, client=None
    )
    g2 = await service.ensure_gesture(
        agent_name="Ada", kind="emote", value="♪", ctx=ctx, client=None, force=True
    )
    assert g1.id != g2.id
    active = await service.get_active("Ada", "emote", "♪")
    assert active is not None and active.id == g2.id
    old = await service.get(g1.id)
    assert old is not None and old.status == "retired"
    assert len(await service.list_for_agent("Ada")) == 1
    assert len(await service.list_for_agent("Ada", include_retired=True)) == 2


@pytest.mark.asyncio
async def test_payload_is_playable_clip(fake_bus: FakeBus) -> None:
    g = await service.ensure_gesture(
        agent_name="Ada",
        kind="state",
        value="thinking",
        ctx=CharacterContext(name="Ada"),
        client=None,
    )
    result = await service.get_payload(g.id)
    assert result is not None
    _, b64 = result
    clip = json.loads(gzip.decompress(base64.b64decode(b64)))
    assert clip["version"] == 1
    assert clip["id"] == g.clip_ref
    assert clip["frameCount"] == g.frame_count
    assert clip["bones"]


@pytest.mark.asyncio
async def test_votes_retire_unpopular_gestures(
    fake_bus: FakeBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.settings, "gesture_retire_net_votes", -2)
    g = await service.ensure_gesture(
        agent_name="Ada", kind="state", value="error", ctx=CharacterContext(name="Ada"), client=None
    )
    g = await service.vote(g.id, up=True)
    assert g is not None and g.votes_up == 1 and g.status == "active"
    for _ in range(3):
        g = await service.vote(g.id, up=False)
    assert g is not None and g.votes_down == 3 and g.status == "retired"
    assert any(n == "gesture.retired" for n, _ in fake_bus.events)
    assert await service.get_active("Ada", "state", "error") is None
    assert await service.vote("nope", up=True) is None


@pytest.mark.asyncio
async def test_invalid_trigger_rejected() -> None:
    with pytest.raises(ValueError):
        await service.ensure_gesture(
            agent_name="Ada",
            kind="mood",
            value="not-a-mood",
            ctx=CharacterContext(name="Ada"),
            client=None,
        )


def test_signature_triggers_resolve_mood_emote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.settings,
        "gesture_gen_signature_triggers",
        ["state:celebrating", "emote:@mood", "bogus", "mood:not-a-mood"],
    )
    out = service.signature_triggers("excited", {"excited": "✦"})
    assert out == [("state", "celebrating"), ("emote", "✦")]
    assert service.signature_triggers("unknown", {}) == [("state", "celebrating")]


@pytest.mark.asyncio
async def test_swarm_spawn_schedules_signature_gestures(
    fake_bus: FakeBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from autonoma.agents import swarm as swarm_mod
    from autonoma.agents.base import AutonomousAgent
    from autonoma.models import AgentPersona

    monkeypatch.setattr(swarm_mod.settings, "gesture_gen_enabled", True)
    monkeypatch.setattr(swarm_mod.settings, "gesture_gen_signature_triggers", ["state:thinking"])
    agent = AutonomousAgent(
        AgentPersona(name="Zed", emoji="x", role="coder", skills=[], color="red")
    )
    from types import SimpleNamespace

    fake_swarm = SimpleNamespace(_llm_config=None)
    fake_swarm._generate_signature_gestures = (
        lambda a: swarm_mod.AgentSwarm._generate_signature_gestures(fake_swarm, a)  # type: ignore[arg-type]
    )
    swarm_mod.AgentSwarm._schedule_signature_gestures(fake_swarm, agent)  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    rows = await service.list_for_agent("Zed")
    assert [(r.trigger_kind, r.trigger_value) for r in rows] == [("state", "thinking")]


# ── router ────────────────────────────────────────────────────────────


@pytest.fixture
async def authed_client() -> AsyncIterator[AsyncClient]:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            await create_user(
                username="viewer1",
                password_hash=hash_password("secret123"),
                role="user",
                status="active",
            )
            r = await c.post(
                "/api/auth/login", json={"username": "viewer1", "password": "secret123"}
            )
            assert r.status_code == 200
            yield c


@pytest.mark.asyncio
async def test_router_generate_list_clip_vote(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.routers import gestures as router_mod

    router_mod._voted.clear()
    r = await authed_client.post(
        "/api/gestures/generate",
        json={"agent": "Ada", "kind": "state", "value": "celebrating", "role": "coder"},
    )
    assert r.status_code == 201, r.text
    g = r.json()["gesture"]
    assert g["clip_ref"].startswith("gen:")

    r = await authed_client.get("/api/gestures", params={"agent": "Ada"})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["gestures"]] == [g["id"]]

    r = await authed_client.get(f"/api/gestures/{g['id']}/clip")
    assert r.status_code == 200
    body = r.json()
    assert body["clip"]["id"] == g["clip_ref"]
    assert "payload_gz_b64" in body

    r = await authed_client.get(f"/api/gestures/{g['id']}")
    assert r.status_code == 200 and r.json()["gesture"]["score"]["segments"]

    r = await authed_client.post(f"/api/gestures/{g['id']}/vote", json={"up": True})
    assert r.status_code == 200 and r.json()["gesture"]["votes_up"] == 1
    r = await authed_client.post(f"/api/gestures/{g['id']}/vote", json={"up": True})
    assert r.status_code == 409

    r = await authed_client.post(
        "/api/gestures/generate", json={"agent": "Ada", "kind": "mood", "value": "zzz"}
    )
    assert r.status_code == 400
    r = await authed_client.get("/api/gestures/missing/clip")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_router_requires_auth() -> None:
    from autonoma.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            r = await c.get("/api/gestures", params={"agent": "Ada"})
            assert r.status_code in (401, 403)
