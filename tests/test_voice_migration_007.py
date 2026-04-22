"""Migration 007 tests — BLOB → filesystem voice-audio storage.

Covers the two critical backwards-compat cases:

1. **New write**: ``create_profile`` stores audio on disk, the DB row
   has ``ref_audio_path`` set and ``ref_audio`` NULL, and reads return
   identical bytes.
2. **Legacy row**: a row written the old way (BLOB only, path NULL) is
   still readable by ``get_profile_audio`` — the store falls back to
   the inline bytes.
3. **Delete cleans up disk**: after ``delete_profile`` the on-disk
   file is gone.

``fresh_db`` lives in ``tests/conftest.py`` — each test gets a scratch
SQLite under ``tmp_path``, and ``settings.data_dir`` points at the same
``tmp_path`` so the voice_refs/ directory is isolated per test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, select, text


async def _wav_bytes(n: int = 256) -> bytes:
    """Minimal RIFF/WAVE header + padding so the sniff accepts it."""
    return b"RIFF" + (n - 8).to_bytes(4, "little") + b"WAVE" + b"\x00" * (n - 12)


async def test_new_profile_writes_to_disk_and_reads_back(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    from autonoma.db.engine import init_db, get_engine
    from autonoma.db.schema import voice_profiles
    from autonoma.voice import store

    # Redirect the FS storage under tmp_path so the test leaves nothing behind.
    monkeypatch.setattr(settings, "data_dir", fresh_db)
    await init_db()

    # Need a user row for the FK.
    from autonoma.db.users import create_user
    user = await create_user(username="u", password_hash="h")

    payload = await _wav_bytes(512)
    summary = await store.create_profile(
        owner_user_id=user.id,
        name="test",
        ref_text="hello",
        ref_audio=payload,
        ref_audio_mime="audio/wav",
        duration_s=1.0,
    )

    # DB row has path set, BLOB column NULL.
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    voice_profiles.c.ref_audio,
                    voice_profiles.c.ref_audio_path,
                ).where(voice_profiles.c.id == summary.id)
            )
        ).first()
    assert row is not None
    assert row._mapping["ref_audio"] is None
    assert row._mapping["ref_audio_path"] == f"{summary.id}.wav"

    # Round-trip bytes via both accessors.
    via_audio = await store.get_profile_audio(summary.id)
    assert via_audio is not None
    assert via_audio[0] == payload

    via_profile = await store.get_profile(summary.id)
    assert via_profile is not None
    assert via_profile.ref_audio == payload

    # File exists on disk where we expect it.
    disk_path = fresh_db / "voice_refs" / f"{summary.id}.wav"
    assert disk_path.exists()
    assert disk_path.read_bytes() == payload


async def test_legacy_blob_row_still_readable(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows written before migration 007 still work via the BLOB fallback."""
    from autonoma.config import settings
    from autonoma.db.engine import init_db, get_engine
    from autonoma.db.schema import voice_profiles
    from autonoma.voice import store

    monkeypatch.setattr(settings, "data_dir", fresh_db)
    await init_db()
    from autonoma.db.users import create_user
    user = await create_user(username="legacy", password_hash="h")

    legacy_payload = b"legacy-blob-bytes"
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(voice_profiles).values(
                id="legacy-1",
                owner_user_id=user.id,
                name="old",
                ref_text="hi",
                ref_audio=legacy_payload,
                ref_audio_path=None,
                ref_audio_mime="audio/wav",
                duration_s=0.0,
                size_bytes=len(legacy_payload),
            )
        )

    got = await store.get_profile_audio("legacy-1")
    assert got is not None
    assert got[0] == legacy_payload


async def test_delete_profile_removes_disk_file(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autonoma.config import settings
    from autonoma.db.engine import init_db
    from autonoma.voice import store

    monkeypatch.setattr(settings, "data_dir", fresh_db)
    await init_db()
    from autonoma.db.users import create_user
    user = await create_user(username="d", password_hash="h")

    summary = await store.create_profile(
        owner_user_id=user.id,
        name="to-delete",
        ref_text="hi",
        ref_audio=await _wav_bytes(128),
        ref_audio_mime="audio/wav",
        duration_s=0.5,
    )
    disk_path = fresh_db / "voice_refs" / f"{summary.id}.wav"
    assert disk_path.exists()

    ok = await store.delete_profile(summary.id)
    assert ok
    assert not disk_path.exists()
