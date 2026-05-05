"""Tests for the persistent achievements layer (Feature #12).

Covers the four behaviours that the unique-constraint + bus-emit
contract gives us:

- a single ``record_achievement`` shows up in ``list_achievements``;
- a duplicate ``record_achievement`` returns ``False`` and does *not*
  add a second row;
- ``batch_record`` collapses duplicates inside one call;
- ``list_recent_globally`` honours ``limit`` and joins on the
  character row so the ticker has names + emojis.

DB isolation comes from the shared ``fresh_db`` fixture in
``tests/conftest.py``. asyncio_mode = "auto" in pyproject.toml handles
the coroutine wrapper for free.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import insert, select

from autonoma.achievements_db import (
    EarnedAchievement,
    batch_record,
    list_achievements,
    list_recent_globally,
    record_achievement,
)
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import characters, earned_achievements
from autonoma.event_bus import bus


async def _seed_character(name: str = "Zara", role: str = "coder") -> str:
    """Insert a minimal ``characters`` row and return its uuid.

    The achievements table FKs ``characters.character_uuid``; without a
    parent row the inserts in this suite would all hit FK violations.
    """
    await init_db()
    char_uuid = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(characters).values(
                character_uuid=char_uuid,
                seed_hash="0" * 32,
                name=name,
                role=role,
                species="fox",
                species_emoji="🦊",
                catchphrase="kon kon",
                rarity="common",
                stats_json=json.dumps({}),
                traits_json=json.dumps([]),
                is_alive=1,
            )
        )
    return char_uuid


async def test_record_one_then_list(fresh_db):
    """Happy path: a fresh insert is visible in the per-character list."""
    char_uuid = await _seed_character()

    inserted = await record_achievement(char_uuid, "first_blood")
    assert inserted is True

    rows = await list_achievements(char_uuid)
    assert len(rows) == 1
    only = rows[0]
    assert isinstance(only, EarnedAchievement)
    assert only.achievement_id == "first_blood"
    # Tier resolved from the catalog because we passed tier="" implicitly.
    assert only.tier == "bronze"


async def test_double_record_is_idempotent(fresh_db):
    """Second call returns False and the unique constraint keeps the table at 1 row."""
    char_uuid = await _seed_character()

    first = await record_achievement(char_uuid, "hello_world")
    second = await record_achievement(char_uuid, "hello_world")
    assert first is True
    assert second is False

    rows = await list_achievements(char_uuid)
    assert len(rows) == 1
    assert rows[0].achievement_id == "hello_world"


async def test_batch_record_dedups(fresh_db):
    """``batch_record`` returns only the ids that were genuinely new."""
    char_uuid = await _seed_character()

    # First call: everything's new.
    new_round_one = await batch_record(char_uuid, ["first_blood", "hello_world", "chatty"])
    assert sorted(new_round_one) == sorted(["first_blood", "hello_world", "chatty"])

    # Second call with overlap: only the genuinely-new id comes back.
    new_round_two = await batch_record(char_uuid, ["first_blood", "hello_world", "oops"])
    assert new_round_two == ["oops"]

    # And no duplicate rows landed in the table.
    engine = get_engine()
    async with engine.connect() as conn:
        ids = (
            await conn.execute(
                select(earned_achievements.c.achievement_id).where(
                    earned_achievements.c.character_uuid == char_uuid
                )
            )
        ).all()
    assert sorted(r[0] for r in ids) == sorted(["first_blood", "hello_world", "chatty", "oops"])


async def test_list_recent_globally_honours_limit(fresh_db):
    """``limit`` clamps the row count and rows arrive newest first."""
    a = await _seed_character(name="Alpha")
    b = await _seed_character(name="Bravo")

    # Spread the inserts across two characters so the join in
    # list_recent_globally has something to match. The ids below all
    # exist in ACHIEVEMENTS so tier resolution works.
    await record_achievement(a, "first_blood")
    await record_achievement(a, "hello_world")
    await record_achievement(b, "chatty")
    await record_achievement(b, "oops")
    await record_achievement(a, "prolific")

    capped = await list_recent_globally(limit=3)
    assert len(capped) == 3
    # Newest-first means the last insert ("prolific" on Alpha) leads.
    assert capped[0]["achievement_id"] == "prolific"
    # Join populated the character columns.
    assert capped[0]["character_name"] == "Alpha"
    assert capped[0]["species_emoji"] == "🦊"
    # Catalog merge populated title + tier.
    assert capped[0]["title"]
    assert capped[0]["tier"] == "silver"

    full = await list_recent_globally(limit=50)
    assert len(full) == 5


async def test_record_emits_bus_event_on_new_only(fresh_db):
    """The ``character.achievement_earned`` event fires once per first-time award."""
    char_uuid = await _seed_character()

    seen: list[dict] = []

    async def _capture(**data):
        seen.append(data)

    bus.on("character.achievement_earned", _capture)

    await record_achievement(char_uuid, "first_blood")
    await record_achievement(char_uuid, "first_blood")  # duplicate, must be silent

    assert len(seen) == 1
    assert seen[0]["achievement_id"] == "first_blood"
    assert seen[0]["character_uuid"] == char_uuid
    assert seen[0]["tier"] == "bronze"
