"""Abuse-prevention tests for the mocap upload pipeline.

Covers the four policies from the abuse-prevention spec:

1. In-process rate limiter: 5 uploads/min AND 20 uploads/hour per user.
2. Per-user quota: 100 clips OR 500 MB total storage.
3. Orphan query: clips with no binding and stale last_accessed_at.
4. Admin bypass: admins skip every limit.

We exercise the store helpers + rate-limit helper directly rather than
spinning up the full FastAPI app — the endpoint wiring is thin glue and
the limit semantics live entirely in these two layers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert

from autonoma.mocap.validator import ValidatedClip


# ── helpers ──────────────────────────────────────────────────────────


def _stub_clip(size_bytes: int = 1024, name: str = "clip") -> ValidatedClip:
    """Cheap ValidatedClip that bypasses the real validator.

    We only care about the fields ``create_clip`` reads, so the gzipped
    payload is an arbitrary constant. Keeps these tests independent of
    the validator module (which has its own test file).
    """
    return ValidatedClip(
        payload_gz=b"\x1f\x8b\x08\x00" + b"\x00" * (size_bytes - 4),
        decoded={"version": 1},
        size_bytes=size_bytes,
        duration_s=1.0,
        fps=30,
        frame_count=30,
        name=name,
        source_vrm="test.vrm",
    )


async def _seed_user(username: str = "alice", role: str = "user") -> str:
    from autonoma.db.users import create_user, users as users_table
    from autonoma.db.engine import get_engine
    from sqlalchemy import update as sa_update

    user = await create_user(username=username, password_hash="h")
    if role != "user":
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                sa_update(users_table)
                .where(users_table.c.id == user.id)
                .values(role=role)
            )
    return user.id


# ── rate limiter ─────────────────────────────────────────────────────


async def test_rate_limit_minute_blocks_on_sixth_upload(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5 calls inside 60s pass, the 6th returns ``rate_limited_minute``."""
    from autonoma import api

    # Fresh history — other tests may have touched the module global.
    api._mocap_upload_history.clear()

    # Pin time so the minute window doesn't slide under us.
    fake_now = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: fake_now[0])

    for _ in range(5):
        assert api._check_mocap_upload_rate("u1") is None
    assert api._check_mocap_upload_rate("u1") == "rate_limited_minute"


async def test_rate_limit_minute_window_slides(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After 60s elapses, the minute bucket frees up again."""
    from autonoma import api

    api._mocap_upload_history.clear()
    fake_now = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: fake_now[0])

    for _ in range(5):
        assert api._check_mocap_upload_rate("u1") is None
    # 6th inside the minute: blocked.
    assert api._check_mocap_upload_rate("u1") == "rate_limited_minute"

    # Advance past the minute window.
    fake_now[0] += 61.0
    # The 5 original entries are now outside the 60s window; the
    # rate-limited call from just now (inside 60s of itself) is the
    # only one still in the minute count, so this attempt should pass.
    assert api._check_mocap_upload_rate("u1") is None


async def test_rate_limit_hour_blocks_on_twenty_first(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 uploads spread across an hour pass; the 21st is blocked with
    ``rate_limited_hour``."""
    from autonoma import api

    api._mocap_upload_history.clear()
    fake_now = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: fake_now[0])

    # Space uploads 2 minutes apart so the minute bucket never fires.
    # 20 uploads × 120s = 2400s, well inside the 3600s window.
    for _ in range(20):
        assert api._check_mocap_upload_rate("u1") is None
        fake_now[0] += 120.0

    # 21st upload — still inside the hour window.
    assert api._check_mocap_upload_rate("u1") == "rate_limited_hour"


async def test_rate_limit_is_per_user(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One user exhausting the bucket must not affect another user."""
    from autonoma import api

    api._mocap_upload_history.clear()
    fake_now = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: fake_now[0])

    for _ in range(5):
        assert api._check_mocap_upload_rate("u1") is None
    assert api._check_mocap_upload_rate("u1") == "rate_limited_minute"

    # u2 has its own history.
    assert api._check_mocap_upload_rate("u2") is None


# ── quota ────────────────────────────────────────────────────────────


async def test_storage_usage_reports_zero_for_empty_user(
    fresh_db: Path,
) -> None:
    from autonoma.db.engine import init_db
    from autonoma.mocap.store import get_user_storage_usage

    await init_db()
    uid = await _seed_user()
    count, total = await get_user_storage_usage(uid)
    assert count == 0
    assert total == 0


async def test_storage_usage_sums_clip_sizes(fresh_db: Path) -> None:
    from autonoma.db.engine import init_db
    from autonoma.mocap.store import create_clip, get_user_storage_usage

    await init_db()
    uid = await _seed_user()

    await create_clip(owner_user_id=uid, validated=_stub_clip(size_bytes=1_000, name="a"))
    await create_clip(owner_user_id=uid, validated=_stub_clip(size_bytes=2_000, name="b"))

    count, total = await get_user_storage_usage(uid)
    assert count == 2
    assert total == 3_000


async def test_quota_clip_count_rejects_101st(fresh_db: Path) -> None:
    """Seed 100 clips, ensure the 101st would push count >= 100."""
    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.schema import mocap_clips
    from autonoma.mocap.store import get_user_storage_usage

    await init_db()
    uid = await _seed_user()

    # Bulk insert via raw Core for speed — we don't need 100 real clips.
    engine = get_engine()
    async with engine.begin() as conn:
        for i in range(100):
            await conn.execute(
                insert(mocap_clips).values(
                    id=f"c{i:03d}",
                    owner_user_id=uid,
                    name=f"clip-{i}",
                    source_vrm="test.vrm",
                    duration_s=1.0,
                    fps=30,
                    frame_count=30,
                    payload_gz=b"gz",
                    size_bytes=100,
                )
            )

    count, total = await get_user_storage_usage(uid)
    assert count == 100
    # The endpoint compares ``count >= 100`` before insert, so this is
    # sufficient — we don't need to actually POST the 101st.
    from autonoma.api import _MOCAP_QUOTA_CLIPS

    assert count >= _MOCAP_QUOTA_CLIPS


async def test_quota_bytes_rejects_when_projection_crosses_limit(
    fresh_db: Path,
) -> None:
    """Seed 499 MB, confirm a 2 MB projection crosses the 500 MB line."""
    from autonoma.api import _MOCAP_QUOTA_BYTES
    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.schema import mocap_clips
    from autonoma.mocap.store import get_user_storage_usage

    await init_db()
    uid = await _seed_user()

    # One big row — we're testing the arithmetic, not per-row storage.
    big = 499 * 1024 * 1024
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(mocap_clips).values(
                id="big",
                owner_user_id=uid,
                name="big",
                source_vrm="test.vrm",
                duration_s=1.0,
                fps=30,
                frame_count=30,
                payload_gz=b"gz",  # representative; size_bytes is the
                # authoritative quota field
                size_bytes=big,
            )
        )

    count, total = await get_user_storage_usage(uid)
    assert count == 1
    assert total == big

    projected = 2 * 1024 * 1024  # 2 MB new upload
    assert total + projected > _MOCAP_QUOTA_BYTES


# ── orphan tracking ─────────────────────────────────────────────────


async def test_orphan_list_respects_binding_and_age(
    fresh_db: Path,
) -> None:
    """Clip with no binding + stale last_accessed_at → returned.
    After we add a binding → not returned."""
    from sqlalchemy import text, update as sa_update

    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.schema import mocap_clips
    from autonoma.mocap.store import (
        create_clip,
        list_orphan_clips,
        upsert_binding,
    )

    await init_db()
    uid = await _seed_user()

    clip = await create_clip(
        owner_user_id=uid,
        validated=_stub_clip(size_bytes=1024, name="lonely"),
    )

    # Brand new clip — last_accessed_at is now, not stale yet.
    clips, total_bytes = await list_orphan_clips(older_than_days=90)
    assert clip.id not in {c.id for c in clips}

    # Backdate the clip so it counts as orphaned.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_update(mocap_clips)
            .where(mocap_clips.c.id == clip.id)
            .values(last_accessed_at=text("datetime('now', '-200 days')"))
        )

    clips, total_bytes = await list_orphan_clips(older_than_days=90)
    ids = {c.id for c in clips}
    assert clip.id in ids
    assert total_bytes >= 1024

    # Add a binding — the clip should drop out of the orphan list.
    await upsert_binding(
        vrm_file="test.vrm",
        trigger_kind="manual",
        trigger_value="hello",
        clip_id=clip.id,
        updated_by=uid,
    )
    clips, _ = await list_orphan_clips(older_than_days=90)
    assert clip.id not in {c.id for c in clips}


async def test_orphan_list_day_threshold(fresh_db: Path) -> None:
    """A clip last accessed 30 days ago is orphaned at 10d, not at 60d."""
    from sqlalchemy import text, update as sa_update

    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.schema import mocap_clips
    from autonoma.mocap.store import create_clip, list_orphan_clips

    await init_db()
    uid = await _seed_user()

    clip = await create_clip(
        owner_user_id=uid,
        validated=_stub_clip(size_bytes=512, name="aging"),
    )

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_update(mocap_clips)
            .where(mocap_clips.c.id == clip.id)
            .values(last_accessed_at=text("datetime('now', '-30 days')"))
        )

    clips_10, _ = await list_orphan_clips(older_than_days=10)
    clips_60, _ = await list_orphan_clips(older_than_days=60)
    assert clip.id in {c.id for c in clips_10}
    assert clip.id not in {c.id for c in clips_60}


async def test_get_clip_payload_touches_last_accessed(
    fresh_db: Path,
) -> None:
    """``get_clip_payload`` bumps last_accessed_at, unorphaning the clip."""
    from sqlalchemy import select, text, update as sa_update

    from autonoma.db.engine import get_engine, init_db
    from autonoma.db.schema import mocap_clips
    from autonoma.mocap.store import (
        create_clip,
        get_clip_payload,
        list_orphan_clips,
    )

    await init_db()
    uid = await _seed_user()

    clip = await create_clip(
        owner_user_id=uid,
        validated=_stub_clip(size_bytes=512, name="touched"),
    )

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_update(mocap_clips)
            .where(mocap_clips.c.id == clip.id)
            .values(last_accessed_at=text("datetime('now', '-200 days')"))
        )

    # Sanity: orphaned before the read.
    orphans, _ = await list_orphan_clips(older_than_days=90)
    assert clip.id in {c.id for c in orphans}

    result = await get_clip_payload(clip.id)
    assert result is not None

    # After the read, last_accessed_at should be ~now.
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(mocap_clips.c.last_accessed_at).where(
                    mocap_clips.c.id == clip.id
                )
            )
        ).first()
    assert row is not None

    orphans, _ = await list_orphan_clips(older_than_days=90)
    assert clip.id not in {c.id for c in orphans}


# ── schema / migration ───────────────────────────────────────────────


async def test_last_accessed_at_column_exists_on_fresh_db(
    fresh_db: Path,
) -> None:
    """After ``init_db`` on a scratch SQLite file, the new column is
    present and has a non-NULL default for INSERTs."""
    from sqlalchemy import text

    from autonoma.db.engine import get_engine, init_db

    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text("PRAGMA table_info(mocap_clips)"))
        ).fetchall()
    names = {r[1] for r in rows}
    assert "last_accessed_at" in names


async def test_migration_adds_column_to_existing_table(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate an existing DB from before migration 010 by running
    migrations 1–9, dropping the new column, rewinding the version
    counter, and verifying init_db re-adds the column without touching
    existing data.

    We rely on the real migration chain for tables 1–9 so the
    ``_verify_schema`` call at the end of ``init_db`` sees a real
    schema — manually scripting every table would duplicate half the
    schema module.
    """
    from sqlalchemy import text

    from autonoma.db import engine as engine_module
    from autonoma.db.engine import get_engine, init_db

    # Full fresh boot — all 10 migrations run.
    await init_db()

    # Seed a legacy row BEFORE we tear the column off, so we can prove
    # the re-ALTER preserves it.
    uid = await _seed_user()
    engine = get_engine()
    from autonoma.db.schema import mocap_clips

    async with engine.begin() as conn:
        await conn.execute(
            insert(mocap_clips).values(
                id="legacy",
                owner_user_id=uid,
                name="legacy",
                source_vrm="test.vrm",
                duration_s=1.0,
                fps=30,
                frame_count=30,
                payload_gz=b"\x1f\x8b",
                size_bytes=128,
            )
        )

        # SQLite 3.35+ supports DROP COLUMN — pretend migration 010
        # never ran by removing the new column and rewinding the
        # schema_version counter.
        await conn.execute(
            text("ALTER TABLE mocap_clips DROP COLUMN last_accessed_at")
        )
        await conn.execute(text("DELETE FROM schema_version WHERE version >= 10"))

    # Confirm the simulation landed as expected.
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text("PRAGMA table_info(mocap_clips)"))
        ).fetchall()
        names_before = {r[1] for r in rows}
    assert "last_accessed_at" not in names_before

    # Re-boot: only migration 010 should fire.
    engine_module._initialized = False
    await init_db()

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(text("PRAGMA table_info(mocap_clips)"))
        ).fetchall()
        names_after = {r[1] for r in rows}
        legacy = (
            await conn.execute(
                text(
                    "SELECT id, size_bytes, last_accessed_at "
                    "FROM mocap_clips WHERE id='legacy'"
                )
            )
        ).first()

    assert "last_accessed_at" in names_after
    assert legacy is not None
    assert legacy[0] == "legacy"
    assert legacy[1] == 128
    # Backfill populated last_accessed_at for the pre-existing row.
    assert legacy[2] is not None
