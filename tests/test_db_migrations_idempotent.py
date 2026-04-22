"""Idempotency check for ``init_db`` / harness_policies migration.

``init_db`` is called on every server boot. The migration framework is
home-grown (no Alembic), so the idempotency guarantee lives entirely in
the per-migration ``checkfirst=True`` calls plus the schema_version
gate. This test calls ``init_db`` twice on the same SQLite file and
asserts the second call is a no-op: same version, no duplicate default
preset, table still readable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ``fresh_db`` is defined in tests/conftest.py — shared by every DB test.


async def _schema_version(conn) -> int:
    from sqlalchemy import text

    row = (
        await conn.execute(text("SELECT MAX(version) FROM schema_version"))
    ).first()
    return int(row[0]) if row and row[0] is not None else 0


async def test_init_db_twice_is_noop(fresh_db: Path) -> None:
    """Running init_db twice leaves the schema version at the highest
    known migration and doesn't raise."""
    from autonoma.db import engine as engine_module
    from autonoma.db.engine import MIGRATIONS, get_engine, init_db

    highest = max(target for target, _ in MIGRATIONS)

    await init_db()
    async with get_engine().begin() as conn:
        first = await _schema_version(conn)
    assert first == highest

    # The _initialized flag short-circuits repeat calls within one
    # process. Flip it so the migration runner actually re-executes —
    # simulating a fresh process starting against an existing DB.
    engine_module._initialized = False
    await init_db()
    async with get_engine().begin() as conn:
        second = await _schema_version(conn)
    assert second == highest


async def test_default_harness_preset_not_duplicated(fresh_db: Path) -> None:
    """The lifespan hook seeds a default preset on startup. Re-booting
    must not insert a second row — otherwise ``get_default_policy`` has
    ambiguous behavior."""
    from autonoma.api import app
    from autonoma.db import engine as engine_module
    from autonoma.db.harness_policies import list_policies_for_user

    # First boot.
    async with app.router.lifespan_context(app):
        pass
    # Second boot on the same DB file.
    engine_module._initialized = False
    async with app.router.lifespan_context(app):
        pass

    # list_policies_for_user(None) returns the default preset row. We
    # don't pass a user_id so the ownership filter isn't applied; the
    # helper returns defaults too.
    presets = await list_policies_for_user("nobody")
    defaults = [p for p in presets if p.is_default]
    assert len(defaults) == 1, (
        f"default preset duplicated: {[p.id for p in defaults]}"
    )


async def test_harness_policies_table_readable_after_migration(
    fresh_db: Path,
) -> None:
    """Smoke: after init_db, a SELECT against harness_policies returns
    without a ``no such table`` error even before any row exists."""
    from sqlalchemy import select

    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.harness_policies import harness_policies

    await init_db()
    async with get_engine().begin() as conn:
        result = await conn.execute(select(harness_policies))
        # The result set may or may not contain the default preset
        # depending on whether the lifespan hook ran — all we care about
        # is that the table exists and is queryable.
        _ = result.fetchall()
