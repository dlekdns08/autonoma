"""Verify cutscene trigger tap respects per-session owner scope."""

from __future__ import annotations

import pytest

from autonoma import context as autonoma_context
from autonoma.cutscenes import (
    Cutscene,
    CutsceneStep,
    CutsceneStepKind,
    cutscene_store,
)
from autonoma.cutscenes.model import CutsceneTrigger
from autonoma.event_bus import bus
from autonoma.routers.cutscenes import _on_bus_event


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the cutscene store at a tmp dir + clean owner resolver
    between tests so global state can't leak."""
    new_root = (tmp_path / "cutscenes").resolve()
    monkeypatch.setattr(cutscene_store, "_root", new_root)
    autonoma_context.set_session_owner_resolver(None)
    autonoma_context.current_session_id.set(None)
    yield
    autonoma_context.set_session_owner_resolver(None)


@pytest.mark.asyncio
async def test_only_owners_cutscene_fires(monkeypatch):
    own_a = Cutscene(
        owner_user_id="userA",
        name="A's victory",
        steps=[
            CutsceneStep(
                at_ms=0,
                kind=CutsceneStepKind.SFX,
                payload={"sfx_name": "complete"},
            ),
        ],
        trigger=CutsceneTrigger(kind="project_complete"),
    )
    own_b = Cutscene(
        owner_user_id="userB",
        name="B's victory",
        steps=[
            CutsceneStep(
                at_ms=0,
                kind=CutsceneStepKind.SFX,
                payload={"sfx_name": "complete"},
            ),
        ],
        trigger=CutsceneTrigger(kind="project_complete"),
    )
    cutscene_store.save(own_a)
    cutscene_store.save(own_b)

    fired_names: list[str] = []

    async def capture(**data):
        if "name" in data:
            fired_names.append(data["name"])

    bus.on("cutscene.started", capture)
    try:
        # Pretend the active session belongs to userA.
        autonoma_context.set_session_owner_resolver(lambda sid: "userA")
        autonoma_context.current_session_id.set(42)
        await _on_bus_event("project.completed", {})
        # Yield so the inner asyncio.create_task gets a chance to run
        # the cutscene fan-out.
        import asyncio
        await asyncio.sleep(0.05)
    finally:
        bus.off("cutscene.started", capture)

    assert "A's victory" in fired_names
    assert "B's victory" not in fired_names


@pytest.mark.asyncio
async def test_no_resolver_means_all_cutscenes_still_fire():
    """Backwards compat: if the owner resolver isn't installed (e.g.
    tests, headless tools), the tap falls back to the previous
    behaviour and considers every cutscene."""
    cs = Cutscene(
        owner_user_id="userA",
        name="all-fire",
        steps=[
            CutsceneStep(
                at_ms=0,
                kind=CutsceneStepKind.SFX,
                payload={"sfx_name": "complete"},
            ),
        ],
        trigger=CutsceneTrigger(kind="project_complete"),
    )
    cutscene_store.save(cs)

    fired: list[str] = []

    async def capture(**data):
        if "name" in data:
            fired.append(data["name"])

    bus.on("cutscene.started", capture)
    try:
        # No resolver → active_owner stays None → no filter applied.
        autonoma_context.set_session_owner_resolver(None)
        await _on_bus_event("project.completed", {})
        import asyncio
        await asyncio.sleep(0.05)
    finally:
        bus.off("cutscene.started", capture)

    assert "all-fire" in fired
