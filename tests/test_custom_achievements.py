"""Tests for the Custom Achievement DSL feature.

Covers four behaviour groups that the smoke harness in
``custom_achievements.__main__`` only exercises informally:

* DSL validation rejects malformed payloads.
* Lifecycle: create → list → disable → re-enable mirrors expected
  counter behaviour.
* Scoping: explicit ``character_uuids``, ``agent`` resolver, and the
  alive-character fan-out fallback all route bumps to the right rows.
* Threshold reach awards the badge via the shared ``earned_achievements``
  table (so the existing per-character badge endpoints render it without
  further plumbing).

DB isolation comes from ``fresh_db`` (tests/conftest.py).
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import insert, select

from autonoma import custom_achievements as ca_mod
from autonoma.custom_achievements import (
    DSLValidationError,
    create_definition,
    list_definitions,
    set_enabled,
    validate_definition,
)
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import (
    characters,
    custom_achievement_progress,
    earned_achievements,
)
from autonoma.event_bus import bus


@pytest.fixture(autouse=True)
def _reinstall_bus_handlers() -> None:
    """The conftest's autouse ``_reset`` clears every bus subscriber,
    but ``custom_achievements.install()`` is idempotent under
    ``_installed=True`` — so without re-subscribing, our emits land in
    a bus that no longer has a handler. Reset the flag and re-install
    before each test."""
    ca_mod._installed = False
    ca_mod.install()


@pytest.fixture(autouse=True)
def _restore_achievements_catalog() -> None:
    """``refresh_cache`` mutates the global ``world.ACHIEVEMENTS`` dict
    in-place to register custom-DSL entries. Those entries lack the
    ``check`` callable that the built-in catalog uses, which makes
    ``world.check_achievements`` raise KeyError downstream in unrelated
    tests. Snapshot + restore so cross-file pollution is impossible."""
    from autonoma.world import ACHIEVEMENTS

    snapshot = {k: v for k, v in ACHIEVEMENTS.items()}
    yield
    ACHIEVEMENTS.clear()
    ACHIEVEMENTS.update(snapshot)
    # Also drop the per-event def cache so the next test's
    # refresh_cache doesn't try to bump rows from a defunct DB.
    ca_mod._active_by_event.clear()


# ── helpers ───────────────────────────────────────────────────────────


async def _seed_character(name: str = "Alpha") -> str:
    """Insert one alive character row and return its uuid."""
    await init_db()
    engine = get_engine()
    uid = str(_uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            insert(characters).values(
                character_uuid=uid,
                seed_hash="seed",
                name=name,
                role="tester",
                species="cat",
                species_emoji="🐱",
                catchphrase="meow",
                rarity="common",
                is_alive=1,
            )
        )
    return uid


async def _progress_count(achievement_id: str, character_uuid: str) -> int:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(custom_achievement_progress.c.count)
                .where(custom_achievement_progress.c.achievement_id == achievement_id)
                .where(custom_achievement_progress.c.character_uuid == character_uuid)
            )
        ).first()
    return int(row[0]) if row else 0


def _ok_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "boss_slayer_test",
        "title": "Test Slayer",
        "tier": "bronze",
        "xp_reward": 10,
        "trigger": {
            "event": "boss.defeated",
            "count": 1,
            "scope": "lifetime",
            "where": {},
        },
    }
    base.update(overrides)
    return base


# ── DSL validation ────────────────────────────────────────────────────


def test_validate_rejects_missing_required_keys() -> None:
    # No id, title, or trigger ⇒ rejected.
    with pytest.raises(DSLValidationError):
        validate_definition({})
    # Missing trigger is fatal even if id+title are present.
    with pytest.raises(DSLValidationError):
        validate_definition({"id": "x", "title": "X"})
    # Missing id.
    payload = _ok_payload()
    payload.pop("id")
    with pytest.raises(DSLValidationError):
        validate_definition(payload)
    # Missing title.
    payload = _ok_payload()
    payload.pop("title")
    with pytest.raises(DSLValidationError):
        validate_definition(payload)


def test_validate_rejects_unknown_event_name() -> None:
    payload = _ok_payload()
    payload["trigger"] = {
        "event": "boss.kissed",  # not in SUPPORTED_EVENTS
        "count": 1,
        "scope": "lifetime",
    }
    with pytest.raises(DSLValidationError):
        validate_definition(payload)


def test_validate_rejects_non_positive_threshold() -> None:
    payload = _ok_payload()
    payload["trigger"] = {
        "event": "boss.defeated",
        "count": 0,
        "scope": "lifetime",
    }
    with pytest.raises(DSLValidationError):
        validate_definition(payload)
    payload["trigger"] = {
        "event": "boss.defeated",
        "count": -3,
        "scope": "lifetime",
    }
    with pytest.raises(DSLValidationError):
        validate_definition(payload)


def test_validate_rejects_unknown_scope() -> None:
    payload = _ok_payload()
    payload["trigger"] = {
        "event": "boss.defeated",
        "count": 1,
        "scope": "weekly",  # not a SUPPORTED_SCOPE
    }
    with pytest.raises(DSLValidationError):
        validate_definition(payload)


# ── Lifecycle: create / list / enable toggle ──────────────────────────


async def test_create_lifecycle_and_enable_toggle(fresh_db) -> None:
    """Disable suppresses bumps; re-enabling resumes them."""
    uid = await _seed_character("Alpha")

    defn = await create_definition(
        _ok_payload(
            id="lifecycle_ach",
            title="Lifecycle",
            trigger={
                "event": "boss.defeated",
                "count": 5,  # well above the bump count so threshold doesn't fire
                "scope": "lifetime",
                "where": {},
            },
        ),
        created_by=None,
    )
    assert defn.id == "lifecycle_ach"

    # list_definitions should contain our row.
    items = await list_definitions()
    ids = {i["id"] for i in items}
    assert "lifecycle_ach" in ids

    # Baseline: emit, counter should bump.
    await bus.emit("boss.defeated", character_uuids=[uid])
    assert await _progress_count("lifecycle_ach", uid) == 1

    # Disable the def. Subsequent emits MUST NOT bump.
    assert await set_enabled("lifecycle_ach", False) is True
    await bus.emit("boss.defeated", character_uuids=[uid])
    assert await _progress_count("lifecycle_ach", uid) == 1, (
        "disabled definition should not advance the counter"
    )

    # Re-enable. Bumps resume.
    assert await set_enabled("lifecycle_ach", True) is True
    await bus.emit("boss.defeated", character_uuids=[uid])
    assert await _progress_count("lifecycle_ach", uid) == 2


# ── Scoping ───────────────────────────────────────────────────────────


async def test_scoping_explicit_character_uuids(fresh_db) -> None:
    uid_a = await _seed_character("Alpha")
    uid_b = await _seed_character("Beta")

    await create_definition(
        _ok_payload(
            id="scoped_ach",
            title="Scoped",
            trigger={
                "event": "boss.defeated",
                "count": 5,
                "scope": "lifetime",
                "where": {},
            },
        )
    )

    # Explicit character_uuids ⇒ only those uuids advance.
    await bus.emit("boss.defeated", character_uuids=[uid_a])

    assert await _progress_count("scoped_ach", uid_a) == 1
    assert await _progress_count("scoped_ach", uid_b) == 0, (
        "explicit scoping should NOT fan out to other alive characters"
    )


async def test_scoping_alive_fanout_when_no_character_uuids(fresh_db) -> None:
    uid_a = await _seed_character("Alpha")
    uid_b = await _seed_character("Beta")

    await create_definition(
        _ok_payload(
            id="fanout_ach",
            title="Fanout",
            trigger={
                "event": "boss.defeated",
                "count": 5,
                "scope": "lifetime",
                "where": {},
            },
        )
    )

    # No character_uuids on the payload — fall back to alive fan-out.
    await bus.emit("boss.defeated", name="Legacy Boss")

    assert await _progress_count("fanout_ach", uid_a) == 1
    assert await _progress_count("fanout_ach", uid_b) == 1


async def test_scoping_agent_name_resolves_to_character_uuid(fresh_db) -> None:
    """Tier-2 resolver: ``agent=<name>`` resolves to that character's uuid."""
    uid_a = await _seed_character("Alpha")
    uid_b = await _seed_character("Beta")

    await create_definition(
        _ok_payload(
            id="sandbox_ach",
            title="Sandbox",
            trigger={
                "event": "sandbox.run_finished",
                "count": 5,
                "scope": "lifetime",
                "where": {},
            },
        )
    )

    # Emit a sandbox.run_finished with only the agent name. The resolver
    # should pick uid_a (the alive row whose name matches), and skip the
    # fan-out path that would also bump uid_b.
    await bus.emit("sandbox.run_finished", agent="Alpha")

    assert await _progress_count("sandbox_ach", uid_a) == 1
    assert await _progress_count("sandbox_ach", uid_b) == 0


# ── Award on threshold ────────────────────────────────────────────────


async def test_threshold_reach_writes_earned_achievements(fresh_db) -> None:
    uid = await _seed_character("Solo")

    await create_definition(
        _ok_payload(
            id="threshold_ach",
            title="Threshold",
            tier="silver",
            xp_reward=25,
            trigger={
                "event": "boss.defeated",
                "count": 2,
                "scope": "lifetime",
                "where": {},
            },
        )
    )

    # Fire twice; the second emit crosses the threshold and writes an
    # ``earned_achievements`` row.
    await bus.emit("boss.defeated", character_uuids=[uid])
    await bus.emit("boss.defeated", character_uuids=[uid])

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(earned_achievements).where(earned_achievements.c.character_uuid == uid)
            )
        ).all()
    ach_ids = [r._mapping["achievement_id"] for r in rows]
    assert "threshold_ach" in ach_ids
    # Tier is propagated from the DSL.
    row = next(r for r in rows if r._mapping["achievement_id"] == "threshold_ach")
    assert row._mapping["tier"] == "silver"


# ── Boss-attackers smoke test (ported from ``__main__`` in custom_achievements.py) ──


async def test_boss_attackers_smoke(fresh_db) -> None:
    """Mirror of the inline ``_smoke_test`` (case 1 + case 2 + case 3).

    Verifies the two evaluator paths after the per-character payload
    migration: scoped emit bumps only the listed uuids, and an emit
    *without* ``character_uuids`` (or with an empty list) falls back to
    the alive-character fan-out.
    """
    uid_a = await _seed_character("Alpha")
    uid_b = await _seed_character("Beta")

    await create_definition(
        _ok_payload(
            id="boss_slayer_1",
            title="Boss Slayer I",
            tier="bronze",
            xp_reward=10,
            trigger={
                "event": "boss.defeated",
                "count": 1,
                "scope": "lifetime",
                "where": {},
            },
        )
    )

    # Case 1: explicit character_uuids scopes the bump.
    await bus.emit("boss.defeated", name="Smoke Boss", xp_reward=10, character_uuids=[uid_a])
    assert await _progress_count("boss_slayer_1", uid_a) == 1
    # uid_b NOT advanced. Note: threshold met for uid_a (count=1), so
    # the second case below will not re-award (idempotent).
    assert await _progress_count("boss_slayer_1", uid_b) == 0

    # Case 2: legacy emit (no character_uuids) fans out to every alive
    # character.
    await bus.emit("boss.defeated", name="Legacy Boss", xp_reward=10)
    assert await _progress_count("boss_slayer_1", uid_a) == 2
    assert await _progress_count("boss_slayer_1", uid_b) == 1

    # Case 3: empty character_uuids list falls through to fan-out.
    await bus.emit("boss.defeated", name="Empty Boss", xp_reward=10, character_uuids=[])
    assert await _progress_count("boss_slayer_1", uid_a) == 3
    assert await _progress_count("boss_slayer_1", uid_b) == 2
