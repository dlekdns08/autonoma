"""Tests for the retirement / ghost lifecycle module.

Covers the three behaviours that matter for Wave 1:

  * ``is_retirement_eligible`` honours the configured thresholds (and
    the master ``retirement_enabled`` flag).
  * ``retire_character`` flips ``retired_at`` exactly once and emits a
    ``character.retired`` event.
  * ``record_ghost_appearance`` writes the row + validates ``kind``.
  * ``list_active_ghosts`` only returns retired characters and folds in
    the latest appearance kind.
  * ``summon_ghost_for_round`` returns ``None`` with no retired chars,
    and a record when retired chars exist + RNG forces a hit.

Wave 2 (swarm / API wiring) is out of scope — these tests stay at the
DB + pure-logic layer where ``fresh_db`` gives us deterministic state.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest


async def _insert_character(
    *,
    name: str,
    role: str = "coder",
    rarity: str = "common",
    runs_survived: int = 0,
    level: int = 1,
    is_alive: int = 1,
    retired_at: datetime | None = None,
    memoir_text: str = "",
) -> str:
    """Helper: insert one ``characters`` row, return its uuid.

    Goes through SQLAlchemy Core directly (not the registry) so the
    test owns the exact field values without bumping unrelated
    counters via the registry's lifecycle pathway.
    """
    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.schema import characters

    await init_db()
    engine = get_engine()
    cu = str(uuid.uuid4())
    seed = uuid.uuid4().hex  # any 32-char value is fine for these tests
    async with engine.begin() as conn:
        await conn.execute(
            characters.insert().values(
                character_uuid=cu,
                seed_hash=seed,
                name=name,
                role=role,
                species="fox",
                species_emoji="🦊",
                catchphrase="",
                rarity=rarity,
                level=level,
                total_xp_earned=level * 100,
                runs_survived=runs_survived,
                runs_died=0,
                tasks_completed_lifetime=0,
                files_created_lifetime=0,
                stats_json=json.dumps({}),
                traits_json=json.dumps([]),
                last_mood="",
                voice_id="",
                is_alive=is_alive,
                retired_at=retired_at,
                memoir_text=memoir_text,
                memoir_version=1 if memoir_text else 0,
            )
        )
    return cu


# ── eligibility ───────────────────────────────────────────────────────


def test_eligibility_honours_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.config import settings
    from autonoma.world.retirement import is_retirement_eligible

    monkeypatch.setattr(settings, "retirement_enabled", True)
    monkeypatch.setattr(settings, "retirement_min_runs", 12)
    monkeypatch.setattr(settings, "retirement_min_level", 8)

    # Both below → no.
    assert is_retirement_eligible(runs_survived=5, level=4) is False
    # Runs ok, level low → no.
    assert is_retirement_eligible(runs_survived=20, level=4) is False
    # Level ok, runs low → no.
    assert is_retirement_eligible(runs_survived=5, level=10) is False
    # Both pass → yes.
    assert is_retirement_eligible(runs_survived=12, level=8) is True
    # Way over → still yes.
    assert is_retirement_eligible(runs_survived=99, level=99) is True


def test_eligibility_disabled_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autonoma.config import settings
    from autonoma.world.retirement import is_retirement_eligible

    monkeypatch.setattr(settings, "retirement_enabled", False)
    monkeypatch.setattr(settings, "retirement_min_runs", 1)
    monkeypatch.setattr(settings, "retirement_min_level", 1)

    # Even an obvious pass returns False when the master flag is off.
    assert is_retirement_eligible(runs_survived=999, level=999) is False


# ── retire_character ──────────────────────────────────────────────────


async def test_retire_character_updates_row_and_emits(
    fresh_db: Path,
) -> None:
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import characters
    from autonoma.event_bus import bus
    from autonoma.world.retirement import retire_character

    cu = await _insert_character(name="Vela", level=10, runs_survived=15)

    seen: list[dict] = []

    async def _capture(**data) -> None:
        seen.append(data)

    bus.on("character.retired", _capture)

    ok = await retire_character(cu, project_uuid=None)
    assert ok is True

    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(characters.c.retired_at, characters.c.is_alive).where(
                    characters.c.character_uuid == cu
                )
            )
        ).first()
    assert row is not None
    assert row.retired_at is not None
    assert row.is_alive == 0

    # Bus event fired with the right payload.
    assert len(seen) == 1
    assert seen[0]["character_uuid"] == cu
    assert "retired_at" in seen[0]

    # Idempotent: a second call returns False and does not re-fire.
    ok2 = await retire_character(cu)
    assert ok2 is False
    assert len(seen) == 1


# ── record_ghost_appearance ───────────────────────────────────────────


async def test_record_ghost_appearance_inserts_row(fresh_db: Path) -> None:
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import ghost_appearances
    from autonoma.world.retirement import record_ghost_appearance

    cu = await _insert_character(
        name="Mira",
        retired_at=datetime.now(UTC),
        is_alive=0,
        memoir_text="lived bravely",
    )

    new_id = await record_ghost_appearance(
        character_uuid=cu,
        kind="advice",
        round_number=7,
        text="trust the green test",
        witnessed_by="alice",
    )
    assert new_id > 0

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(ghost_appearances).where(ghost_appearances.c.character_uuid == cu)
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["kind"] == "advice"
    assert rows[0]["round_number"] == 7
    assert rows[0]["witnessed_by"] == "alice"
    assert rows[0]["text"] == "trust the green test"


async def test_record_ghost_appearance_rejects_bad_kind(fresh_db: Path) -> None:
    from autonoma.world.retirement import record_ghost_appearance

    cu = await _insert_character(name="Lo", retired_at=datetime.now(UTC))
    with pytest.raises(ValueError):
        await record_ghost_appearance(
            character_uuid=cu,
            kind="haunt",  # not in GHOST_KINDS
            round_number=1,
            text="boo",
        )


# ── list_active_ghosts ────────────────────────────────────────────────


async def test_list_active_ghosts_returns_only_retired(fresh_db: Path) -> None:
    from autonoma.world.retirement import list_active_ghosts

    # Two retired, one still active. Ordering by retired_at DESC means
    # ``later`` should appear first.
    earlier = datetime(2025, 1, 1, tzinfo=UTC)
    later = datetime(2026, 1, 1, tzinfo=UTC)
    await _insert_character(name="OldOne", retired_at=earlier, is_alive=0)
    cu_later = await _insert_character(
        name="LatestRetiree",
        retired_at=later,
        is_alive=0,
        memoir_text="retired with honour",
    )
    await _insert_character(name="StillActive", retired_at=None, is_alive=1)

    ghosts = await list_active_ghosts(limit=10)
    names = [g.name for g in ghosts]
    assert "StillActive" not in names
    assert names[0] == "LatestRetiree"
    assert {"OldOne", "LatestRetiree"} <= set(names)

    # Default kind is "dream" when no appearances recorded yet.
    latest = next(g for g in ghosts if g.character_uuid == cu_later)
    assert latest.kind == "dream"
    assert latest.memoir_text == "retired with honour"


async def test_list_active_ghosts_folds_in_latest_kind(fresh_db: Path) -> None:
    from autonoma.world.retirement import (
        list_active_ghosts,
        record_ghost_appearance,
    )

    cu = await _insert_character(
        name="EchoSan",
        retired_at=datetime(2026, 2, 1, tzinfo=UTC),
        is_alive=0,
    )
    await record_ghost_appearance(cu, kind="dream", round_number=1, text="...")
    await record_ghost_appearance(cu, kind="cameo", round_number=2, text="...")
    # Latest insert wins — "advice".
    await record_ghost_appearance(cu, kind="advice", round_number=3, text="...")

    ghosts = await list_active_ghosts()
    me = next(g for g in ghosts if g.character_uuid == cu)
    assert me.kind == "advice"


# ── summon_ghost_for_round ────────────────────────────────────────────


async def test_summon_returns_none_when_no_retired(fresh_db: Path) -> None:
    from autonoma.world.retirement import summon_ghost_for_round

    # Plenty of *living* characters, zero retired.
    await _insert_character(name="Alive1", is_alive=1)
    await _insert_character(name="Alive2", is_alive=1)

    # RNG that always says "fire the cameo" — the absence of retired
    # rows must still produce None.
    class _AlwaysFireRng(random.Random):
        def random(self) -> float:  # type: ignore[override]
            return 0.0

    rng = _AlwaysFireRng(0)
    out = await summon_ghost_for_round(
        round_number=5,
        current_agents=[],
        rng=rng,
    )
    assert out is None


async def test_summon_returns_ghost_when_retired_exist(fresh_db: Path) -> None:
    from autonoma.world.retirement import GhostRecord, summon_ghost_for_round

    await _insert_character(
        name="Onyx",
        rarity="legendary",
        retired_at=datetime(2026, 3, 1, tzinfo=UTC),
        is_alive=0,
        memoir_text="legend.",
    )
    await _insert_character(
        name="Pebble",
        rarity="common",
        retired_at=datetime(2026, 3, 2, tzinfo=UTC),
        is_alive=0,
    )

    # Force the cameo to fire by stubbing ``random()`` to 0.0; rng.choices
    # falls through to its real implementation, weighted toward the
    # legendary, but either pick is a valid GhostRecord for the assertion.
    class _ForceFireRng(random.Random):
        def __init__(self) -> None:
            super().__init__(42)
            self._did_fire = False

        def random(self) -> float:  # type: ignore[override]
            if not self._did_fire:
                self._did_fire = True
                return 0.0
            return super().random()

    rng = _ForceFireRng()
    out = await summon_ghost_for_round(
        round_number=9,
        current_agents=["someoneActive"],
        rng=rng,
    )
    assert isinstance(out, GhostRecord)
    assert out.name in {"Onyx", "Pebble"}
    assert out.kind in {"dream", "advice", "cameo"}


async def test_summon_skips_when_dice_says_no(fresh_db: Path) -> None:
    from autonoma.world.retirement import summon_ghost_for_round

    await _insert_character(
        name="Ghosty",
        retired_at=datetime(2026, 3, 5, tzinfo=UTC),
        is_alive=0,
    )

    class _NeverFireRng(random.Random):
        def random(self) -> float:  # type: ignore[override]
            return 0.99  # > GHOST_SUMMON_PROBABILITY (0.05)

    rng = _NeverFireRng(0)
    out = await summon_ghost_for_round(
        round_number=3,
        current_agents=[],
        rng=rng,
    )
    assert out is None


async def test_summon_excludes_currently_active_names(fresh_db: Path) -> None:
    from autonoma.world.retirement import summon_ghost_for_round

    # Only retired character has the same name as a live agent.
    await _insert_character(
        name="DoubleBooked",
        retired_at=datetime(2026, 3, 10, tzinfo=UTC),
        is_alive=0,
    )

    class _ForceFireRng(random.Random):
        def __init__(self) -> None:
            super().__init__(7)
            self._fired = False

        def random(self) -> float:  # type: ignore[override]
            if not self._fired:
                self._fired = True
                return 0.0
            return super().random()

    rng = _ForceFireRng()
    out = await summon_ghost_for_round(
        round_number=4,
        current_agents=["DoubleBooked"],
        rng=rng,
    )
    # Their living self is on stage — the ghost stays away.
    assert out is None
