"""Tests for ``autonoma.voice.store`` — the voice profile + binding DAL.

Covers:
- create_profile writes both DB row and the on-disk ref audio file.
- create_profile cleans up the disk file when the DB insert fails.
- list_profile_summaries orders newest-first.
- get_profile falls back to the legacy ``ref_audio`` column when the
  filesystem copy is missing (migration 007 compatibility).
- delete_profile returns False on miss, removes the disk file on hit.
- delete_profile raises IntegrityError when a binding still references
  the profile (FK ON DELETE RESTRICT).
- upsert_binding handles both insert and update paths.
- delete_binding returns False for unknown vrm_file.
- profile_is_bound reflects the binding state.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from autonoma.voice import fs as voice_fs
from autonoma.voice import store as voice_store

pytestmark = pytest.mark.usefixtures("fresh_db")


@pytest.fixture(autouse=True)
async def _ensure_db_initialized(fresh_db):
    """Tests that don't create a user first never trigger ``init_db``,
    which is what creates ``voice_profiles`` / ``voice_bindings``.
    Force the migration here so even read-only tests find the tables.

    Depends on ``fresh_db`` so the engine reset happens *before* we run
    migrations against the per-test tmp_path — otherwise this fixture
    can resolve first against a stale engine and fresh_db then wipes
    our work.
    """
    from autonoma.db.engine import init_db

    await init_db()
    yield


async def _make_user(username: str = "voice-user") -> str:
    from autonoma.db.users import create_user

    user = await create_user(
        username=username,
        password_hash="not-a-real-hash",
        role="user",
        status="active",
    )
    return user.id


WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32  # cheap fake WAV


async def _create_default_profile(name: str = "voiceA") -> voice_store.ProfileSummary:
    user_id = await _make_user(f"u-{name}")
    return await voice_store.create_profile(
        owner_user_id=user_id,
        name=name,
        ref_text="hello world",
        ref_audio=WAV_BYTES,
        ref_audio_mime="audio/wav",
        duration_s=1.5,
    )


# ── create_profile ────────────────────────────────────────────────────


async def test_create_profile_writes_db_and_filesystem():
    summary = await _create_default_profile()
    # DB row round-trips.
    fetched = await voice_store.get_profile_summary(summary.id)
    assert fetched is not None
    assert fetched.name == "voiceA"
    assert fetched.ref_text == "hello world"
    assert fetched.size_bytes == len(WAV_BYTES)
    # On-disk file exists with the deterministic basename.
    basename = voice_fs.basename_for(summary.id, "audio/wav")
    assert voice_fs.read_ref_audio(basename) == WAV_BYTES


async def test_create_profile_cleans_up_file_when_db_insert_fails():
    user_id = await _make_user("u-fail")
    fake_id = "00000000-0000-0000-0000-deadbeefdead"
    written: list[str] = []

    real_write = voice_fs.write_ref_audio

    def capture_write(profile_id: str, data: bytes, mime: str) -> str:
        # Force the predictable id so we can assert cleanup happened
        # against a known basename even though create_profile picks its
        # own uuid.
        name = real_write(profile_id, data, mime)
        written.append(name)
        return name

    with patch.object(voice_store, "insert", side_effect=RuntimeError("boom")):
        with patch.object(voice_fs, "write_ref_audio", side_effect=capture_write):
            with pytest.raises(RuntimeError, match="boom"):
                await voice_store.create_profile(
                    owner_user_id=user_id,
                    name="will-fail",
                    ref_text="t",
                    ref_audio=WAV_BYTES,
                    ref_audio_mime="audio/wav",
                    duration_s=1.0,
                )
    # Exactly one write happened, and the file is gone afterward.
    assert len(written) == 1
    assert voice_fs.read_ref_audio(written[0]) is None
    # Sanity: no profile row leaked through.
    assert await voice_store.get_profile_summary(fake_id) is None


# ── list / get ────────────────────────────────────────────────────────


async def test_list_profile_summaries_orders_newest_first():
    """``ORDER BY created_at DESC`` puts newer rows first.

    SQLite's ``CURRENT_TIMESTAMP`` is second-resolution, so two profiles
    created back-to-back tie. We backdate ``a`` by an hour to force a
    distinct timestamp and exercise the real ordering.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update as sa_update

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import voice_profiles

    a = await _create_default_profile("voiceA")
    b = await _create_default_profile("voiceB")

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_update(voice_profiles)
            .where(voice_profiles.c.id == a.id)
            .values(created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1))
        )

    rows = await voice_store.list_profile_summaries()
    ids = [r.id for r in rows]
    assert ids == [b.id, a.id], "newer profile must come first"


async def test_get_profile_falls_back_to_legacy_blob():
    """Migration 007 left rows that have bytes in ``ref_audio`` but no
    ``ref_audio_path``. ``get_profile`` should still return them."""
    summary = await _create_default_profile("legacy")
    # Simulate the legacy row shape: clear ref_audio_path, push bytes
    # into ref_audio, and remove the on-disk file.
    from sqlalchemy import update as sa_update

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import voice_profiles

    legacy_bytes = b"LEGACYAUDIO"
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_update(voice_profiles)
            .where(voice_profiles.c.id == summary.id)
            .values(ref_audio=legacy_bytes, ref_audio_path=None)
        )
    voice_fs.delete_ref_audio(voice_fs.basename_for(summary.id, "audio/wav"))

    profile = await voice_store.get_profile(summary.id)
    assert profile is not None
    assert profile.ref_audio == legacy_bytes


async def test_get_profile_summary_returns_none_for_unknown_id():
    assert await voice_store.get_profile_summary("not-a-real-id") is None


# ── delete_profile ────────────────────────────────────────────────────


async def test_delete_profile_returns_false_for_missing_id():
    assert await voice_store.delete_profile("not-a-real-id") is False


async def test_delete_profile_removes_db_row_and_disk_file():
    summary = await _create_default_profile("doomed")
    basename = voice_fs.basename_for(summary.id, "audio/wav")
    assert voice_fs.read_ref_audio(basename) is not None  # precondition

    deleted = await voice_store.delete_profile(summary.id)
    assert deleted is True
    assert await voice_store.get_profile_summary(summary.id) is None
    assert voice_fs.read_ref_audio(basename) is None


async def test_delete_profile_blocked_by_binding():
    summary = await _create_default_profile("bound")
    await voice_store.upsert_binding(vrm_file="bob.vrm", profile_id=summary.id, updated_by=None)
    # FK is ON DELETE RESTRICT — engine surfaces this as IntegrityError.
    with pytest.raises(IntegrityError):
        await voice_store.delete_profile(summary.id)
    # Profile row still present.
    assert await voice_store.get_profile_summary(summary.id) is not None


# ── bindings ──────────────────────────────────────────────────────────


async def test_upsert_binding_inserts_then_updates():
    a = await _create_default_profile("voiceA")
    b = await _create_default_profile("voiceB")

    first = await voice_store.upsert_binding(vrm_file="alice.vrm", profile_id=a.id, updated_by=None)
    assert first.profile_id == a.id

    second = await voice_store.upsert_binding(
        vrm_file="alice.vrm", profile_id=b.id, updated_by=None
    )
    assert second.profile_id == b.id  # update path took effect

    # Only one row exists per vrm_file.
    bindings = await voice_store.list_bindings()
    assert [bd.vrm_file for bd in bindings] == ["alice.vrm"]


async def test_delete_binding_returns_false_for_missing():
    assert await voice_store.delete_binding("never-bound.vrm") is False


async def test_profile_is_bound_reflects_binding_state():
    summary = await _create_default_profile("freed")
    assert await voice_store.profile_is_bound(summary.id) is False

    await voice_store.upsert_binding(vrm_file="charlie.vrm", profile_id=summary.id, updated_by=None)
    assert await voice_store.profile_is_bound(summary.id) is True

    assert await voice_store.delete_binding("charlie.vrm") is True
    assert await voice_store.profile_is_bound(summary.id) is False
