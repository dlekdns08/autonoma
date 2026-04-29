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


async def _migration_006_voice(conn) -> None:
    """Create voice_profiles and voice_bindings tables.

    Powers the /voice admin page: OmniVoice reference-audio profiles +
    per-VRM voice bindings. Same ``create_all(checkfirst=True)`` pattern
    as the mocap migration.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_007_voice_fs_storage(conn) -> None:
    """Move voice reference audio from in-column BLOBs to filesystem.

    Online migration — the app keeps serving while this runs:
      1. Add the nullable ``ref_audio_path`` column.
      2. Relax the NOT NULL constraint on ``ref_audio`` so new FS-backed
         rows can leave it empty.
      3. Leave existing BLOB rows as-is; the store reads them as a
         fallback when ``ref_audio_path`` is NULL.

    SQLite can't DROP NOT NULL via plain ALTER TABLE, so the column
    relax is done by rebuilding the table on older SQLite. We probe for
    the state first so re-runs on already-migrated DBs are no-ops.
    """
    # Step 1: add ref_audio_path if it doesn't already exist.
    result = await conn.execute(text("PRAGMA table_info(voice_profiles)"))
    cols = {row[1]: row for row in result.fetchall()}  # name → full row

    if "ref_audio_path" not in cols:
        await conn.execute(
            text("ALTER TABLE voice_profiles ADD COLUMN ref_audio_path VARCHAR(128)")
        )

    # Step 2: relax ref_audio NOT NULL. SQLite's PRAGMA row layout is
    # (cid, name, type, notnull, dflt_value, pk). We only rebuild when
    # needed — rebuilding an empty or already-relaxed table is wasteful.
    ref_audio_row = cols.get("ref_audio")
    if ref_audio_row is not None and ref_audio_row[3]:  # notnull==1
        # Rebuild the table with ref_audio nullable. Keep column order
        # compatible with the ORM so inspection matches.
        await conn.execute(text("""
            CREATE TABLE voice_profiles__new (
                id VARCHAR(36) PRIMARY KEY,
                owner_user_id VARCHAR(36) NOT NULL
                    REFERENCES users(id) ON DELETE RESTRICT,
                name VARCHAR(128) NOT NULL,
                ref_text TEXT NOT NULL DEFAULT '',
                ref_audio BLOB,
                ref_audio_path VARCHAR(128),
                ref_audio_mime VARCHAR(32) NOT NULL DEFAULT 'audio/wav',
                duration_s FLOAT NOT NULL DEFAULT 0.0,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text("""
            INSERT INTO voice_profiles__new (
                id, owner_user_id, name, ref_text, ref_audio,
                ref_audio_path, ref_audio_mime, duration_s, size_bytes,
                created_at, updated_at
            )
            SELECT
                id, owner_user_id, name, ref_text, ref_audio,
                ref_audio_path, ref_audio_mime, duration_s, size_bytes,
                created_at, updated_at
            FROM voice_profiles
        """))
        await conn.execute(text("DROP TABLE voice_profiles"))
        await conn.execute(text(
            "ALTER TABLE voice_profiles__new RENAME TO voice_profiles"
        ))
        # voice_bindings has a FK into voice_profiles — the rebuild
        # preserves the PK names so existing bindings stay valid.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_voice_profiles_owner_user_id "
            "ON voice_profiles(owner_user_id)"
        ))


async def _migration_008_agent_journal(conn) -> None:
    """Create agent_journal table (feature #5 — persistent agent identity).

    Keyed by character_uuid so revived characters see their own history.
    Uses ``create_all(checkfirst=True)`` for idempotency.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_009_personas(conn) -> None:
    """Create personas table (feature #6 — persona marketplace).

    A persona is an exportable bundle: bones, voice profile id, VRM
    preference, and a free-form ``prompt_style`` string. Kept in a
    separate table from ``characters`` because personas are templates
    shared between sessions; characters are instances that live in runs.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


async def _migration_010_mocap_last_accessed(conn) -> None:
    """Add ``last_accessed_at`` to ``mocap_clips`` for orphan tracking.

    Orphan sweeps need a "last observed playback" timestamp to
    distinguish abandoned clips from cold-but-still-useful uploads.
    ``create_all(checkfirst=True)`` skips live tables so the column
    won't be added for existing deployments — do an explicit
    PRAGMA-probed ALTER here.

    SQLite restriction: ``ALTER TABLE ADD COLUMN`` can't use a
    non-constant default like CURRENT_TIMESTAMP, so we:
      1. Add the column nullable with no default.
      2. Backfill existing rows to ``created_at`` (good-enough proxy
         for "last observed playback" — the clip hasn't been played
         since upload).
      3. Leave the column nullable at the table level; the ORM-side
         ``NOT NULL + server_default`` still applies to every new
         insert via SQLAlchemy, and ``_verify_schema`` compares by
         name so a nullable live column is fine.

    New rows go through ``insert(mocap_clips)`` with the server_default
    on the column, so they always land with a timestamp.
    """
    result = await conn.execute(text("PRAGMA table_info(mocap_clips)"))
    cols = {row[1] for row in result.fetchall()}
    if "last_accessed_at" not in cols:
        # Step 1: add the column nullable (SQLite limitation around
        # non-constant defaults on ALTER). Explicit NULL default.
        await conn.execute(
            text("ALTER TABLE mocap_clips ADD COLUMN last_accessed_at DATETIME")
        )
        # Step 2: backfill. ``COALESCE(created_at, CURRENT_TIMESTAMP)``
        # handles the (impossible but defensive) case where created_at
        # is NULL for some legacy row.
        await conn.execute(
            text(
                "UPDATE mocap_clips SET last_accessed_at = "
                "COALESCE(created_at, CURRENT_TIMESTAMP) "
                "WHERE last_accessed_at IS NULL"
            )
        )


async def _migration_011_voice_transcripts(conn) -> None:
    """Create the ``voice_transcripts`` table.

    Append-only audit log of completed ASR transcriptions, used by the
    /voice studio history panel and the future audit dashboard.
    Standard ``create_all(checkfirst=True)`` since the table is new.
    """
    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))


MIGRATIONS: list[Migration] = [
    (1, _migration_001_baseline),
    (2, _migration_002_users),
    (3, _migration_003_harness_policies),
    (4, _migration_004_feature_tables),
    (5, _migration_005_mocap),
    (6, _migration_006_voice),
    (7, _migration_007_voice_fs_storage),
    (8, _migration_008_agent_journal),
    (9, _migration_009_personas),
    (10, _migration_010_mocap_last_accessed),
    (11, _migration_011_voice_transcripts),
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
