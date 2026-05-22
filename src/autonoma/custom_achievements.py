"""Custom Achievement DSL MVP.

Lets admins define new badges at runtime via a JSON DSL, without
shipping a new release of the catalog in :mod:`autonoma.world`.

DSL shape (start small)
───────────────────────

::

    {
      "id": "boss_slayer_3",
      "title": "Boss Slayer III",
      "tier": "gold",
      "xp_reward": 50,
      "trigger": {
        "event": "boss.defeated",
        "count": 3,
        "scope": "lifetime",
        "where": {"ok": true}
      }
    }

Supported ``event`` values for MVP::

    - boss.defeated
    - quest.completed
    - sandbox.run_finished

``scope`` is ``"lifetime"`` (counter persists across runs) or
``"session"`` (counter resets per swarm session; we key the counter by
``session:<session_id>`` if the event payload has a ``session_id``, else
fall back to lifetime semantics).

``where`` is an optional dict of equality filters on the event payload —
the increment only fires when every key matches.

Storage
───────
- ``custom_achievements`` — DSL definitions (one row per id).
- ``custom_achievement_progress`` — per-character running counters.

Evaluation
──────────
:func:`install` subscribes to the three supported events on the shared
event bus once at module import time. Each event handler resolves the
character_uuid(s) involved via this priority order:

  1. ``data["character_uuids"]`` — explicit list of participants when
     the emit site knows who took part (e.g. the agents who dealt
     damage on a ``boss.defeated``).
  2. ``data["agent"]`` — single agent name, resolved against the
     ``characters`` table for the most-recent alive row with that
     name (``sandbox.run_finished``, legacy ``quest.completed``).
  3. Collective fan-out across every alive character — backwards-
     compat fallback for callers that haven't been migrated.

It then bumps each matching definition's counter, and awards via the
existing :func:`autonoma.achievements_db.record_achievement` path when
the threshold is crossed.

Catalog integration
───────────────────
We register active custom definitions into :data:`autonoma.world.ACHIEVEMENTS`
so the per-agent and recent badge endpoints render the title /
description / xp_reward without needing a second lookup table on the
frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from autonoma.achievements_db import record_achievement
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import (
    characters,
    custom_achievement_progress,
    custom_achievements,
)
from autonoma.event_bus import bus
from autonoma.world import ACHIEVEMENTS

logger = logging.getLogger(__name__)


SUPPORTED_EVENTS = frozenset({"boss.defeated", "quest.completed", "sandbox.run_finished"})
SUPPORTED_TIERS = frozenset({"bronze", "silver", "gold", "platinum", ""})
SUPPORTED_SCOPES = frozenset({"lifetime", "session"})


# ── DSL validation ──────────────────────────────────────────────────────


class DSLValidationError(ValueError):
    """Raised when a definition payload fails DSL validation."""


@dataclass
class AchievementDef:
    """Parsed + validated DSL row."""

    id: str
    title: str
    description: str
    tier: str
    xp_reward: int
    event: str
    count: int
    scope: str
    where: dict[str, Any]

    def to_catalog_entry(self) -> dict[str, Any]:
        """Shape matching :data:`autonoma.world.ACHIEVEMENTS` entries.

        We don't bother with the AchievementTier enum here — the
        achievements_db lookup uses ``getattr(tier, "value", tier)`` so a
        plain string round-trips fine.
        """
        return {
            "title": self.title,
            "description": self.description,
            "tier": self.tier,
            "xp_reward": self.xp_reward,
            "custom": True,
        }

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tier": self.tier,
            "xp_reward": self.xp_reward,
            "trigger": {
                "event": self.event,
                "count": self.count,
                "scope": self.scope,
                "where": dict(self.where),
            },
        }


def validate_definition(raw: Any) -> AchievementDef:
    """Validate + parse a DSL payload into an :class:`AchievementDef`.

    Designed to be defensive — we don't trust any field. Returns the
    parsed structure or raises :class:`DSLValidationError` with a
    human-readable message the router can echo back as a 400.
    """
    if not isinstance(raw, dict):
        raise DSLValidationError("definition must be a JSON object")

    def _req_str(key: str, max_len: int = 128) -> str:
        v = raw.get(key)
        if not isinstance(v, str) or not v.strip():
            raise DSLValidationError(f"'{key}' must be a non-empty string")
        v = v.strip()
        if len(v) > max_len:
            raise DSLValidationError(f"'{key}' too long (>{max_len} chars)")
        return v

    ach_id = _req_str("id", max_len=64)
    # Restrict id charset so it round-trips cleanly through URLs.
    if not all(c.isalnum() or c in "._-" for c in ach_id):
        raise DSLValidationError("'id' may contain only letters, digits, '.', '_', '-'")

    title = _req_str("title", max_len=128)
    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise DSLValidationError("'description' must be a string")
    if len(description) > 512:
        raise DSLValidationError("'description' too long (>512 chars)")

    tier = (raw.get("tier") or "").strip()
    if tier and tier not in SUPPORTED_TIERS:
        raise DSLValidationError(f"'tier' must be one of {sorted(SUPPORTED_TIERS)}")

    xp_reward_raw = raw.get("xp_reward", 0)
    try:
        xp_reward = int(xp_reward_raw)
    except (TypeError, ValueError) as exc:
        raise DSLValidationError("'xp_reward' must be an integer") from exc
    if xp_reward < 0 or xp_reward > 100_000:
        raise DSLValidationError("'xp_reward' out of range (0..100000)")

    trig = raw.get("trigger")
    if not isinstance(trig, dict):
        raise DSLValidationError("'trigger' must be an object")

    event = (trig.get("event") or "").strip()
    if event not in SUPPORTED_EVENTS:
        raise DSLValidationError(f"'trigger.event' must be one of {sorted(SUPPORTED_EVENTS)}")

    count_raw = trig.get("count", 1)
    try:
        count = int(count_raw)
    except (TypeError, ValueError) as exc:
        raise DSLValidationError("'trigger.count' must be an integer") from exc
    if count < 1 or count > 1_000_000:
        raise DSLValidationError("'trigger.count' out of range (1..1000000)")

    scope = (trig.get("scope") or "lifetime").strip()
    if scope not in SUPPORTED_SCOPES:
        raise DSLValidationError(f"'trigger.scope' must be one of {sorted(SUPPORTED_SCOPES)}")

    where_raw = trig.get("where", {})
    if where_raw is None:
        where_raw = {}
    if not isinstance(where_raw, dict):
        raise DSLValidationError("'trigger.where' must be an object")
    # Only allow scalar equality filters; reject nested objects so the
    # evaluation predicate stays trivially correct.
    for k, v in where_raw.items():
        if not isinstance(k, str):
            raise DSLValidationError("'trigger.where' keys must be strings")
        if isinstance(v, (dict, list)):
            raise DSLValidationError(f"'trigger.where.{k}' must be a scalar")

    return AchievementDef(
        id=ach_id,
        title=title,
        description=description,
        tier=tier,
        xp_reward=xp_reward,
        event=event,
        count=count,
        scope=scope,
        where=dict(where_raw),
    )


# ── In-memory active-definition cache ────────────────────────────────────
# The event-bus hot path needs to be cheap, so we keep a process-local
# cache of enabled definitions grouped by event name. Mutations to the
# DB (create / patch / delete) call :func:`refresh_cache` to rebuild it.

_active_by_event: dict[str, list[AchievementDef]] = {}
_cache_lock = asyncio.Lock()


async def refresh_cache() -> None:
    """Reload the active-definition cache from the DB.

    Also re-registers active definitions into the global
    :data:`autonoma.world.ACHIEVEMENTS` catalog so the existing badge
    endpoints render title/description/xp without further plumbing.
    """
    async with _cache_lock:
        await init_db()
        engine = get_engine()
        async with engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(
                            custom_achievements.c.id,
                            custom_achievements.c.definition_json,
                            custom_achievements.c.enabled,
                        )
                    )
                )
                .mappings()
                .all()
            )

        new_index: dict[str, list[AchievementDef]] = {ev: [] for ev in SUPPORTED_EVENTS}
        # Track ids we register into ACHIEVEMENTS so we can purge stale
        # custom entries on each refresh without nuking the built-in
        # catalog.
        registered_ids: set[str] = set()

        for row in rows:
            try:
                raw = json.loads(row["definition_json"])
                defn = validate_definition(raw)
            except (DSLValidationError, json.JSONDecodeError) as exc:
                # Invalid rows are skipped, not propagated — the admin
                # already saw the failure when they created it, and
                # corrupting the cache helps nobody.
                logger.warning(
                    "[custom_ach] skipping invalid row id=%s: %s",
                    row.get("id"),
                    exc,
                )
                continue

            # ALWAYS register into catalog so already-earned badges
            # still render their metadata even after a disable.
            ACHIEVEMENTS[defn.id] = defn.to_catalog_entry()
            registered_ids.add(defn.id)

            if int(row["enabled"]) == 1:
                new_index[defn.event].append(defn)

        # Prune custom entries from ACHIEVEMENTS that no longer exist.
        # Heuristic: any catalog entry tagged ``custom: True`` whose id
        # we didn't just register is stale.
        stale = [
            k
            for k, v in ACHIEVEMENTS.items()
            if isinstance(v, dict) and v.get("custom") and k not in registered_ids
        ]
        for k in stale:
            ACHIEVEMENTS.pop(k, None)

        _active_by_event.clear()
        _active_by_event.update(new_index)


# ── CRUD helpers ─────────────────────────────────────────────────────────


async def list_definitions() -> list[dict[str, Any]]:
    """Return every definition + enabled flag, newest first."""
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(custom_achievements).order_by(custom_achievements.c.created_at.desc())
                )
            )
            .mappings()
            .all()
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "id": r["id"],
            "enabled": bool(int(r["enabled"])),
            "created_at": str(r["created_at"]),
            "created_by": r["created_by"],
        }
        try:
            item["definition"] = json.loads(r["definition_json"])
        except json.JSONDecodeError:
            item["definition"] = None
            item["error"] = "definition_json_invalid"
        out.append(item)
    return out


async def create_definition(raw: Any, *, created_by: str | None = None) -> AchievementDef:
    """Insert a new definition row. Returns the parsed def on success."""
    defn = validate_definition(raw)
    await init_db()
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                insert(custom_achievements).values(
                    id=defn.id,
                    definition_json=json.dumps(defn.to_wire()),
                    created_by=created_by,
                    enabled=1,
                )
            )
    except IntegrityError as exc:
        raise DSLValidationError(f"achievement id '{defn.id}' already exists") from exc
    await refresh_cache()
    return defn


async def set_enabled(achievement_id: str, enabled: bool) -> bool:
    """Flip the enabled flag on a definition. Returns True if a row was updated."""
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            update(custom_achievements)
            .where(custom_achievements.c.id == achievement_id)
            .values(enabled=1 if enabled else 0)
        )
    ok = result.rowcount > 0
    if ok:
        await refresh_cache()
    return ok


async def delete_definition(achievement_id: str) -> bool:
    """Hard-delete a definition. Returns True if the row existed."""
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        # Wipe progress counters first to avoid an ON DELETE CASCADE that
        # could in theory leave orphaned earned_achievements rows. The
        # ``earned_achievements`` history we leave alone — disabled or
        # deleted custom defs should keep showing on the badges that
        # were already legitimately earned.
        await conn.execute(
            delete(custom_achievement_progress).where(
                custom_achievement_progress.c.achievement_id == achievement_id
            )
        )
        result = await conn.execute(
            delete(custom_achievements).where(custom_achievements.c.id == achievement_id)
        )
    ok = result.rowcount > 0
    if ok:
        await refresh_cache()
    return ok


# ── Counter bookkeeping ──────────────────────────────────────────────────


async def _bump_and_maybe_award(
    defn: AchievementDef,
    character_uuid: str,
    *,
    session_id: int | None,
) -> bool:
    """Increment the counter for (defn, character) and award if threshold met.

    Returns True if a brand-new award was minted on this call.
    """
    scope_key = "lifetime"
    if defn.scope == "session":
        scope_key = f"session:{session_id}" if session_id is not None else "lifetime"

    await init_db()
    engine = get_engine()
    new_count: int
    async with engine.begin() as conn:
        # Try the cheap UPDATE first; insert only if no row exists.
        # ``returning`` isn't portable to SQLite < 3.35 in async drivers
        # so we re-SELECT after the write — cheap because the row is
        # uniquely keyed by (achievement_id, character_uuid, scope_key).
        result = await conn.execute(
            update(custom_achievement_progress)
            .where(custom_achievement_progress.c.achievement_id == defn.id)
            .where(custom_achievement_progress.c.character_uuid == character_uuid)
            .where(custom_achievement_progress.c.scope_key == scope_key)
            .values(count=custom_achievement_progress.c.count + 1)
        )
        if result.rowcount == 0:
            try:
                await conn.execute(
                    insert(custom_achievement_progress).values(
                        achievement_id=defn.id,
                        character_uuid=character_uuid,
                        scope_key=scope_key,
                        count=1,
                    )
                )
            except IntegrityError:
                # Lost a race; fall back to update.
                await conn.execute(
                    update(custom_achievement_progress)
                    .where(custom_achievement_progress.c.achievement_id == defn.id)
                    .where(custom_achievement_progress.c.character_uuid == character_uuid)
                    .where(custom_achievement_progress.c.scope_key == scope_key)
                    .values(count=custom_achievement_progress.c.count + 1)
                )

        row = (
            await conn.execute(
                select(custom_achievement_progress.c.count)
                .where(custom_achievement_progress.c.achievement_id == defn.id)
                .where(custom_achievement_progress.c.character_uuid == character_uuid)
                .where(custom_achievement_progress.c.scope_key == scope_key)
            )
        ).first()
        new_count = int(row[0]) if row else 0

    if new_count < defn.count:
        return False

    # Threshold met — record the award. ``record_achievement`` is
    # idempotent thanks to the unique constraint on
    # (character_uuid, achievement_id), so the noisy "every event after
    # threshold" path is harmless and silent.
    return await record_achievement(
        character_uuid=character_uuid,
        achievement_id=defn.id,
        tier=defn.tier,
    )


async def _resolve_target_uuids(event: str, data: dict[str, Any]) -> list[str]:
    """Map an event payload to the character_uuid(s) it should affect.

    Resolution order, in priority:

    1. ``data["character_uuids"]`` — preferred. Emit sites that know
       which characters participated (e.g. boss.defeated tracking the
       agents who dealt damage) include this field. We trust it
       verbatim and skip every fallback below.
    2. ``data["agent"]`` (single name) — resolve to the most-recent
       alive character with that name. Used by sandbox.run_finished and
       by the legacy ``QuestBoard.check_completion`` quest.completed
       path, which only knows the agent's display name.
    3. Collective fan-out — every alive character. Backwards-compat
       fallback for callers that haven't been migrated yet.
    """
    # 1. Preferred: explicit list of character uuids on the payload.
    raw_uuids = data.get("character_uuids")
    if raw_uuids:
        # Accept any iterable of strings; dedup while preserving order.
        seen: set[str] = set()
        out: list[str] = []
        try:
            iterator = list(raw_uuids)
        except TypeError:
            iterator = []
        for uid in iterator:
            if not isinstance(uid, str):
                continue
            uid = uid.strip()
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
        if out:
            return out

    # 2. Single-agent-name event payloads (sandbox + legacy quest_board).
    if event in ("sandbox.run_finished", "quest.completed"):
        name = (data.get("agent") or "").strip()
        if name:
            await init_db()
            engine = get_engine()
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        select(characters.c.character_uuid)
                        .where(characters.c.name == name)
                        .where(characters.c.is_alive == 1)
                        .order_by(characters.c.last_seen_at.desc())
                        .limit(1)
                    )
                ).first()
            if row:
                return [row[0]]
        # sandbox.run_finished without a usable name → nothing to bump.
        if event == "sandbox.run_finished":
            return []
        # quest.completed without ``agent`` falls through to the
        # collective fan-out so the live-quest path (no per-character
        # info) still works.

    # 3. Collective events — every alive character is a participant.
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(characters.c.character_uuid).where(characters.c.is_alive == 1)
            )
        ).fetchall()
    return [r[0] for r in rows]


def _where_matches(defn: AchievementDef, data: dict[str, Any]) -> bool:
    """All keys in ``defn.where`` must equal the corresponding event field."""
    for k, expected in defn.where.items():
        if data.get(k) != expected:
            return False
    return True


# ── Event-bus glue ───────────────────────────────────────────────────────


async def _on_event(event_name: str, **data: Any) -> None:
    """Single handler used by every supported event subscription."""
    defs = list(_active_by_event.get(event_name, ()))
    if not defs:
        return

    session_id = data.get("session_id")
    try:
        session_id_int = int(session_id) if session_id is not None else None
    except (TypeError, ValueError):
        session_id_int = None

    # Resolve target characters once per event, not once per def.
    try:
        targets = await _resolve_target_uuids(event_name, data)
    except Exception as exc:
        logger.warning("[custom_ach] target resolution failed: %s", exc)
        return
    if not targets:
        return

    for defn in defs:
        if not _where_matches(defn, data):
            continue
        for character_uuid in targets:
            try:
                await _bump_and_maybe_award(defn, character_uuid, session_id=session_id_int)
            except Exception as exc:
                logger.warning(
                    "[custom_ach] bump failed defn=%s char=%s: %s",
                    defn.id,
                    character_uuid,
                    exc,
                )


# Per-event thin wrappers so the bus signature (``**data``) is honoured.


async def _on_boss_defeated(**data: Any) -> None:
    await _on_event("boss.defeated", **data)


async def _on_quest_completed(**data: Any) -> None:
    await _on_event("quest.completed", **data)


async def _on_sandbox_run_finished(**data: Any) -> None:
    await _on_event("sandbox.run_finished", **data)


_installed = False


def install() -> None:
    """Subscribe handlers to the event bus. Safe to call multiple times."""
    global _installed
    if _installed:
        return
    bus.on("boss.defeated", _on_boss_defeated)
    bus.on("quest.completed", _on_quest_completed)
    bus.on("sandbox.run_finished", _on_sandbox_run_finished)
    _installed = True


# Install on import so the router-include in ``api.py`` is the single
# wiring point. Cache load happens on first DB-touch (lazy) — we
# deliberately don't kick off an async refresh at import because that
# would require a running loop, which CI / sync tests don't guarantee.
install()


__all__ = [
    "AchievementDef",
    "DSLValidationError",
    "validate_definition",
    "list_definitions",
    "create_definition",
    "set_enabled",
    "delete_definition",
    "refresh_cache",
    "install",
]


# ── Inline smoke test ──────────────────────────────────────────────────
#
# Run with::
#
#     python -m autonoma.custom_achievements
#
# Exercises the two important branches of the evaluator after the
# per-character payload migration:
#   (a) explicit ``character_uuids`` scopes the counter to those UUIDs,
#   (b) absent ``character_uuids`` falls back to the legacy fan-out
#       across every alive character.
#
# Self-contained: spins up a fresh on-disk SQLite under a tempdir, seeds
# two ``characters`` rows, and inspects ``custom_achievement_progress``
# directly to verify which UUIDs got incremented.


async def _smoke_test() -> None:  # pragma: no cover — manual harness only
    import tempfile
    import uuid as _uuid
    from pathlib import Path

    from autonoma import config as _config
    from autonoma.db import engine as _engine_mod

    # Isolated DB so we never touch the real data dir.
    tmpdir = Path(tempfile.mkdtemp(prefix="custom_ach_smoke_"))
    _config.settings.data_dir = tmpdir
    _config.settings.db_filename = "smoke.db"
    _engine_mod._engine = None
    _engine_mod._initialized = False

    await init_db()
    engine = get_engine()

    uuid_a = str(_uuid.uuid4())
    uuid_b = str(_uuid.uuid4())

    # Seed two alive characters so the legacy fan-out has somewhere to
    # land.
    async with engine.begin() as conn:
        for uid, name in ((uuid_a, "Alpha"), (uuid_b, "Beta")):
            await conn.execute(
                insert(characters).values(
                    character_uuid=uid,
                    seed_hash="smoke",
                    name=name,
                    role="tester",
                    species="cat",
                    species_emoji="🐱",
                    catchphrase="",
                    rarity="common",
                    is_alive=1,
                )
            )

    # Install the boss_slayer_1 definition (threshold=1, lifetime).
    defn = await create_definition(
        {
            "id": "boss_slayer_1",
            "title": "Boss Slayer I",
            "tier": "bronze",
            "xp_reward": 10,
            "trigger": {
                "event": "boss.defeated",
                "count": 1,
                "scope": "lifetime",
                "where": {},
            },
        },
        # NULL because we don't seed a ``users`` row in the smoke
        # harness and ``custom_achievements.created_by`` has a FK to
        # ``users.id`` (nullable).
        created_by=None,
    )
    assert defn.id == "boss_slayer_1"
    await refresh_cache()

    async def _progress_count(uid: str) -> int:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(custom_achievement_progress.c.count)
                    .where(custom_achievement_progress.c.achievement_id == "boss_slayer_1")
                    .where(custom_achievement_progress.c.character_uuid == uid)
                )
            ).first()
        return int(row[0]) if row else 0

    # ── Case 1: explicit character_uuids scopes the bump. ──────────────
    await bus.emit(
        "boss.defeated",
        name="Smoke Boss",
        xp_reward=10,
        character_uuids=[uuid_a],
    )
    # The handler is fire-and-forget under gather, but bus.emit awaits
    # every handler before returning, so the bump is already durable.
    a_after = await _progress_count(uuid_a)
    b_after = await _progress_count(uuid_b)
    assert a_after == 1, f"expected uuid_a count=1, got {a_after}"
    assert b_after == 0, f"expected uuid_b count=0 (scoped event), got {b_after}"
    print(
        f"[ok] scoped emit: uuid_a={a_after} uuid_b={b_after}",
    )

    # ── Case 2: legacy emit (no character_uuids) fans out. ─────────────
    await bus.emit(
        "boss.defeated",
        name="Legacy Boss",
        xp_reward=10,
    )
    a_after2 = await _progress_count(uuid_a)
    b_after2 = await _progress_count(uuid_b)
    assert a_after2 == 2, f"expected uuid_a count=2 after legacy, got {a_after2}"
    assert b_after2 == 1, f"expected uuid_b count=1 after legacy fan-out, got {b_after2}"
    print(
        f"[ok] legacy fan-out: uuid_a={a_after2} uuid_b={b_after2}",
    )

    # ── Case 3: empty character_uuids falls through to fan-out. ────────
    await bus.emit(
        "boss.defeated",
        name="Empty List Boss",
        xp_reward=10,
        character_uuids=[],
    )
    a_after3 = await _progress_count(uuid_a)
    b_after3 = await _progress_count(uuid_b)
    assert a_after3 == 3, f"expected uuid_a count=3 (empty=>fanout), got {a_after3}"
    assert b_after3 == 2, f"expected uuid_b count=2 (empty=>fanout), got {b_after3}"
    print(
        f"[ok] empty list → fan-out: uuid_a={a_after3} uuid_b={b_after3}",
    )

    print("custom_achievements smoke test: PASS")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_smoke_test())
