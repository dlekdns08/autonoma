"""Tests for the persistent character registry.

We use a per-test temp dir so each test gets a clean SQLite file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from autonoma.config import settings
from autonoma.db import dispose_engine
from autonoma.db.registry import CharacterRegistry, seed_hash_for


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the DB at a throwaway directory for every test."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_filename", "test.db")
    # engine is cached in module state; dispose between tests
    asyncio.get_event_loop().run_until_complete(dispose_engine())
    yield
    asyncio.get_event_loop().run_until_complete(dispose_engine())


COMMON_BONES = dict(
    bones_species="fox",
    bones_species_emoji="🦊",
    bones_catchphrase="Kon kon~",
    bones_rarity="common",
    bones_stats={"debugging": 5, "patience": 7, "chaos": 3, "wisdom": 6, "speed": 4},
    bones_traits=["diligent", "friendly"],
)

LEGENDARY_BONES = {**COMMON_BONES, "bones_rarity": "legendary"}


async def test_disabled_registry_is_noop():
    reg = CharacterRegistry(enabled=False)
    assert not reg.enabled
    # No DB access happens.
    live = await reg.hydrate(role="coder", name="Zara", **COMMON_BONES)
    assert live.is_new
    assert live.name == "Zara"
    # finish_project with no project_uuid should no-op cleanly.
    await reg.finish_project(
        status="completed", exit_reason="ok", rounds_used=0, final_answer="",
        survivors=[], deaths=[], wills=[], relationships=[], famous=[],
    )


async def test_new_character_is_flagged_is_new():
    reg = CharacterRegistry(enabled=True)
    await reg.begin_project("p", "d", "g", 10)
    live = await reg.hydrate(role="coder", name="Zara", **COMMON_BONES)
    assert live.is_new
    assert live.level == 1
    assert live.total_xp_earned == 0


async def test_lifetime_stats_persist_across_runs():
    """A surviving common character keeps its uuid, level, and lifetime counts."""
    reg1 = CharacterRegistry(enabled=True)
    await reg1.begin_project("run1", "", "", 10)
    live = await reg1.hydrate(role="coder", name="Zara", **COMMON_BONES)
    uuid1 = live.character_uuid

    # simulate a successful run
    live.level = 4
    live.total_xp_earned = 210
    live.tasks_completed_lifetime = 7
    live.files_created_lifetime = 3
    await reg1.finish_project(
        status="completed", exit_reason="ok", rounds_used=12,
        final_answer="done", survivors=[uuid1],
        deaths=[], wills=[], relationships=[], famous=[],
    )

    reg2 = CharacterRegistry(enabled=True)
    await reg2.begin_project("run2", "", "", 10)
    live2 = await reg2.hydrate(role="coder", name="Zara", **COMMON_BONES)
    assert live2.character_uuid == uuid1, "same seed/name should hit the same row"
    assert not live2.is_new
    assert live2.level == 4
    assert live2.total_xp_earned == 210
    assert live2.tasks_completed_lifetime == 7
    assert live2.runs_survived == 1


async def test_dead_common_spawns_fresh_generation():
    """Common death: new run creates a NEW uuid; old graveyard row stays."""
    reg1 = CharacterRegistry(enabled=True)
    await reg1.begin_project("r1", "", "", 10)
    live = await reg1.hydrate(role="coder", name="Pico", **COMMON_BONES)
    uuid_dead = live.character_uuid
    await reg1.finish_project(
        status="incomplete", exit_reason="stopped", rounds_used=5,
        final_answer="", survivors=[],
        deaths=[{
            "character_uuid": uuid_dead, "round": 3, "cause": "errors",
            "epitaph": "Fell chasing a bug.",
        }],
        wills=[{"character_uuid": uuid_dead, "text": "Tell them I tried."}],
        relationships=[], famous=[],
    )

    reg2 = CharacterRegistry(enabled=True)
    await reg2.begin_project("r2", "", "", 10)
    live2 = await reg2.hydrate(role="coder", name="Pico", **COMMON_BONES)
    assert live2.is_new
    assert live2.character_uuid != uuid_dead, \
        "dead common should leave the old uuid intact and create a new one"
    assert live2.level == 1


async def test_dead_legendary_is_revived():
    """Legendary death: same uuid, resurrected, level preserved."""
    reg1 = CharacterRegistry(enabled=True)
    await reg1.begin_project("r1", "", "", 10)
    live = await reg1.hydrate(role="hero", name="Valkyrie", **LEGENDARY_BONES)
    uuid_legend = live.character_uuid
    live.level = 8
    live.total_xp_earned = 900
    await reg1.finish_project(
        status="incomplete", exit_reason="stopped", rounds_used=5,
        final_answer="", survivors=[],  # Valkyrie died
        deaths=[{
            "character_uuid": uuid_legend, "round": 5, "cause": "boss",
            "epitaph": "Fought the dragon to the last.",
        }],
        wills=[{"character_uuid": uuid_legend, "text": "I'll be back."}],
        relationships=[], famous=[],
    )

    reg2 = CharacterRegistry(enabled=True)
    await reg2.begin_project("r2", "", "", 10)
    live2 = await reg2.hydrate(role="hero", name="Valkyrie", **LEGENDARY_BONES)
    assert live2.character_uuid == uuid_legend, "legend keeps the same uuid"
    assert live2.is_alive
    assert not live2.is_new
    assert live2.level == 8, "legend keeps their level"
    assert live2.total_xp_earned == 900
    # the prior will should surface so the narrator can reference it
    assert "I'll be back." in live2.past_wills


async def test_seed_hash_is_deterministic():
    a = seed_hash_for("coder", "Zara")
    b = seed_hash_for("coder", "Zara")
    c = seed_hash_for("coder", "Zia")
    assert a == b
    assert a != c


async def test_relationship_persistence():
    reg1 = CharacterRegistry(enabled=True)
    await reg1.begin_project("r1", "", "", 10)
    a = await reg1.hydrate(role="coder", name="Ally", **COMMON_BONES)
    b = await reg1.hydrate(role="reviewer", name="Ben", **COMMON_BONES)
    await reg1.finish_project(
        status="completed", exit_reason="ok", rounds_used=10,
        final_answer="", survivors=[a.character_uuid, b.character_uuid],
        deaths=[], wills=[],
        relationships=[{
            "from_uuid": a.character_uuid,
            "to_uuid": b.character_uuid,
            "trust": 0.85, "familiarity": 12, "shared_tasks": 4, "conflicts": 0,
            "sentiment": "positive", "last_interaction": "shipped together",
        }],
        famous=[],
    )

    # Re-open the DB and verify the edge survived.
    from sqlalchemy import select
    from autonoma.db.engine import get_engine
    from autonoma.db.schema import relationships as rtbl

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(select(rtbl))).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["trust"] == pytest.approx(0.85)
    assert row["familiarity"] == 12
    assert row["sentiment"] == "positive"
