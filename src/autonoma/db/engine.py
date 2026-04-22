"""Async SQLAlchemy engine + lightweight migrations.

Why a hand-rolled migration loop instead of Alembic? The schema lives in
a single repo, ships with the app binary, and the data model is small
enough that we can just bump a ``schema_version`` counter and run a list
of idempotent forward-only migration callbacks. No runtime dependency on
Alembic, no .ini file, no autogenerate drift.

Concurrency/integrity knobs applied at connect time:
- WAL journal mode (readers never block writers, survives unclean exits)
- synchronous=NORMAL (WAL makes FULL unnecessary for this workload)
- foreign_keys=ON (SQLite defaults to OFF)
- busy_timeout=5000 (soft wait instead of instant SQLITE_BUSY)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import event, inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from autonoma.config import settings
from autonoma.db.schema import metadata

logger = logging.getLogger(__name__)


_engine: AsyncEngine | None = None
_init_lock = asyncio.Lock()
_initialized = False


def _sync_pragmas(dbapi_connection, _connection_record) -> None:
    """Apply per-connection PRAGMAs for safe concurrent access."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def get_engine() -> AsyncEngine:
    """Lazy-construct (once per process) the shared async engine."""
    global _engine
    if _engine is None:
        data_dir: Path = settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / settings.db_filename
        url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        _engine = create_async_engine(
            url,
            future=True,
            # aiosqlite itself serializes, but we still benefit from
            # a small pool so we can hold a read and a write at once.
            pool_pre_ping=False,
        )
        # PRAGMAs fire on the *sync* DBAPI connection aiosqlite wraps.
        event.listen(_engine.sync_engine, "connect", _sync_pragmas)
        logger.info("Character DB engine initialized at %s", db_path)
    return _engine


async def dispose_engine() -> None:
    """Release the engine. Safe to call if not initialized."""
    global _engine, _initialized
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _initialized = False


# ── Migrations (forward-only, idempotent) ─────────────────────────────

# Each entry is (target_version, async_callable(conn)). Applied in order,
# only when the current DB's schema_version < target_version. Appending a
# new migration here is the full change surface — no Alembic needed.
Migration = tuple[int, Callable[..., Awaitable[None]]]


async def _migration_001_baseline(conn) -> None:
    """Create every table in the current schema."""
    # SQLAlchemy metadata.create_all understands "if not exists" semantics
    # via checkfirst=True, so running this on an already-populated DB is
    # a no-op.
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_002_users(conn) -> None:
    """Create the ``users`` table for cookie-session auth.

    Split from the baseline so existing deployments (already at version
    1) pick up the new table instead of silently skipping it.
    ``create_all(checkfirst=True)`` iterates all tables registered on
    ``metadata`` so this is safe to run on a DB that already has them.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_003_harness_policies(conn) -> None:
    """Create the ``harness_policies`` table.

    Per-user Harness Engineering presets plus the system default preset.
    Same ``create_all(checkfirst=True)`` pattern as the earlier migrations.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_004_feature_tables(conn) -> None:
    """Create feature tables: file_history, run_summary, session_checkpoint.

    Feature 9  — file_history: per-session file version history.
    Feature 12 — run_summary: cross-run analytics.
    Feature 30 — session_checkpoint: ProjectState snapshots for resume.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_005_mocap(conn) -> None:
    """Create mocap_clips and mocap_bindings tables.

    See ``db.schema`` for the column rationale. These tables power the
    ``/mocap`` page's clip library + trigger-to-clip bindings used by
    the VTuber character playback path.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


MIGRATIONS: list[Migration] = [
    (1, _migration_001_baseline),
    (2, _migration_002_users),
    (3, _migration_003_harness_policies),
    (4, _migration_004_feature_tables),
    (5, _migration_005_mocap),
]


async def _get_schema_version(conn) -> int:
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY"
            ")"
        )
    )
    result = await conn.execute(text("SELECT MAX(version) FROM schema_version"))
    row = result.first()
    return int(row[0]) if row and row[0] is not None else 0


async def _set_schema_version(conn, version: int) -> None:
    await conn.execute(text("INSERT INTO schema_version(version) VALUES(:v)"), {"v": version})


async def _verify_schema(conn) -> None:
    """Compare live table columns to the ORM metadata; raise on drift.

    ``create_all(checkfirst=True)`` silently skips tables that already
    exist, so an added-or-renamed column on a live table will NOT
    reach the deployed DB — schema and code diverge without warning.
    We inspect each registered table after migrations run and fail
    loudly if any expected column is missing. Extra (legacy) columns
    are informational only, because dropping/renaming in SQLite needs
    a dedicated migration anyway.
    """
    def _sync_check(sync_conn) -> list[str]:
        insp = sa_inspect(sync_conn)
        errors: list[str] = []
        for table in metadata.sorted_tables:
            if not insp.has_table(table.name):
                errors.append(f"table '{table.name}' missing entirely")
                continue
            live_cols = {c["name"] for c in insp.get_columns(table.name)}
            expected_cols = {c.name for c in table.columns}
            missing = expected_cols - live_cols
            extra = live_cols - expected_cols
            if missing:
                errors.append(
                    f"table '{table.name}' missing columns {sorted(missing)} "
                    f"(create_all(checkfirst=True) silently skipped; add an "
                    f"explicit ALTER migration)"
                )
            if extra:
                logger.info(
                    "[schema] table '%s' has unknown columns %s — probably "
                    "from an older code revision, leaving in place",
                    table.name, sorted(extra),
                )
        return errors

    errors = await conn.run_sync(_sync_check)
    if errors:
        for err in errors:
            logger.error("[schema drift] %s", err)
        raise RuntimeError(
            "Database schema drift detected: "
            + "; ".join(errors)
            + ". Add an explicit ALTER migration for the affected "
            "table(s)."
        )


async def init_db() -> None:
    """Create tables and apply pending migrations. Call once on startup.

    Safe to invoke repeatedly — every migration is guarded by the version
    counter, and tables are created with checkfirst=True. After
    migrations run, :func:`_verify_schema` compares the live schema to
    the ORM metadata and raises if any expected column is missing, so
    silent skips on pre-existing tables don't leak into production.
    """
    global _initialized
    # Side-effect imports: register the ``users`` and ``harness_policies``
    # tables on the shared ``metadata`` before the baseline migration
    # runs. Done here (rather than at module top) to avoid a circular
    # import — both modules depend on ``db.engine``.
    from autonoma.db import users as _users_module  # noqa: F401
    from autonoma.db import harness_policies as _harness_policies_module  # noqa: F401

    async with _init_lock:
        if _initialized:
            return
        engine = get_engine()
        async with engine.begin() as conn:
            current = await _get_schema_version(conn)
            for target, runner in MIGRATIONS:
                if current < target:
                    logger.info("Applying DB migration %s", target)
                    await runner(conn)
                    await _set_schema_version(conn, target)
                    current = target
            await _verify_schema(conn)
        _initialized = True
