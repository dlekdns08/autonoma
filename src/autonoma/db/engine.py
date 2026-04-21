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

from sqlalchemy import event, text
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


MIGRATIONS: list[Migration] = [
    (1, _migration_001_baseline),
    (2, _migration_002_users),
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


async def init_db() -> None:
    """Create tables and apply pending migrations. Call once on startup.

    Safe to invoke repeatedly — every migration is guarded by the version
    counter, and tables are created with checkfirst=True.
    """
    global _initialized
    # Side-effect import: registers the ``users`` table on the shared
    # ``metadata`` before the baseline migration runs. Done here (rather
    # than at module top) to avoid a circular import — ``db.users``
    # depends on ``db.engine``.
    from autonoma.db import users as _users_module  # noqa: F401

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
        _initialized = True
