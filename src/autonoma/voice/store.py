"""Database access for voice_profiles and voice_bindings.

Same style as ``autonoma.mocap.store``: thin async SQLAlchemy Core
wrappers. Ref audio bytes live on the filesystem under
``{data_dir}/voice_refs/`` (see ``autonoma.voice.fs``); the DB row only
stores the basename in ``ref_audio_path``. Rows written before migration
007 still have bytes inline in the legacy ``ref_audio`` column — the
read path falls back to it transparently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine
from autonoma.db.schema import voice_bindings, voice_profiles
from autonoma.voice import fs as voice_fs


@dataclass(slots=True)
class ProfileSummary:
    """Metadata-only view. Omits the raw audio bytes so listing endpoints
    don't return megabytes of payload per row."""

    id: str
    owner_user_id: str
    name: str
    ref_text: str
    ref_audio_mime: str
    duration_s: float
    size_bytes: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "name": self.name,
            "ref_text": self.ref_text,
            "ref_audio_mime": self.ref_audio_mime,
            "duration_s": self.duration_s,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class Profile:
    """Full row including ref audio bytes. Used by the synth path and
    the audio-serving endpoint only."""

    summary: ProfileSummary
    ref_audio: bytes

    @property
    def id(self) -> str:
        return self.summary.id

    @property
    def ref_text(self) -> str:
        return self.summary.ref_text

    @property
    def ref_audio_mime(self) -> str:
        return self.summary.ref_audio_mime


@dataclass(slots=True)
class Binding:
    vrm_file: str
    profile_id: str
    updated_by: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "vrm_file": self.vrm_file,
            "profile_id": self.profile_id,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


def _row_to_summary(row: Any) -> ProfileSummary:
    m = row._mapping
    return ProfileSummary(
        id=m["id"],
        owner_user_id=m["owner_user_id"],
        name=m["name"],
        ref_text=m["ref_text"] or "",
        ref_audio_mime=m["ref_audio_mime"] or "audio/wav",
        duration_s=float(m["duration_s"] or 0.0),
        size_bytes=int(m["size_bytes"] or 0),
        created_at=str(m["created_at"]),
        updated_at=str(m["updated_at"]),
    )


def _row_to_binding(row: Any) -> Binding:
    m = row._mapping
    return Binding(
        vrm_file=m["vrm_file"],
        profile_id=m["profile_id"],
        updated_by=m["updated_by"],
        updated_at=str(m["updated_at"]),
    )


# ── profiles ───────────────────────────────────────────────────────────


async def create_profile(
    *,
    owner_user_id: str,
    name: str,
    ref_text: str,
    ref_audio: bytes,
    ref_audio_mime: str,
    duration_s: float,
) -> ProfileSummary:
    profile_id = str(uuid.uuid4())
    # Persist the audio to disk before the DB insert so a successful
    # insert always references a real file. If the insert fails
    # afterward we clean up the orphan — cheap since files live under
    # a known dir.
    basename = voice_fs.write_ref_audio(profile_id, ref_audio, ref_audio_mime)
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                insert(voice_profiles).values(
                    id=profile_id,
                    owner_user_id=owner_user_id,
                    name=name,
                    ref_text=ref_text,
                    ref_audio=None,
                    ref_audio_path=basename,
                    ref_audio_mime=ref_audio_mime,
                    duration_s=duration_s,
                    size_bytes=len(ref_audio),
                )
            )
            row = (
                await conn.execute(
                    select(voice_profiles).where(voice_profiles.c.id == profile_id)
                )
            ).first()
    except Exception:
        voice_fs.delete_ref_audio(basename)
        raise
    if row is None:
        raise RuntimeError(
            f"create_profile: failed to read back inserted profile id={profile_id}"
        )
    return _row_to_summary(row)


async def list_profile_summaries() -> list[ProfileSummary]:
    """All profiles, metadata only. Bindings are global so every active
    user can see the available voice pool."""
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    voice_profiles.c.id,
                    voice_profiles.c.owner_user_id,
                    voice_profiles.c.name,
                    voice_profiles.c.ref_text,
                    voice_profiles.c.ref_audio_mime,
                    voice_profiles.c.duration_s,
                    voice_profiles.c.size_bytes,
                    voice_profiles.c.created_at,
                    voice_profiles.c.updated_at,
                ).order_by(voice_profiles.c.created_at.desc())
            )
        ).all()
    return [_row_to_summary(r) for r in rows]


async def get_profile_summary(profile_id: str) -> ProfileSummary | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    voice_profiles.c.id,
                    voice_profiles.c.owner_user_id,
                    voice_profiles.c.name,
                    voice_profiles.c.ref_text,
                    voice_profiles.c.ref_audio_mime,
                    voice_profiles.c.duration_s,
                    voice_profiles.c.size_bytes,
                    voice_profiles.c.created_at,
                    voice_profiles.c.updated_at,
                ).where(voice_profiles.c.id == profile_id)
            )
        ).first()
    return _row_to_summary(row) if row else None


def _resolve_audio_bytes(row_mapping: Any) -> bytes:
    """Get the ref audio bytes for a row, preferring FS over legacy BLOB.

    Returns ``b""`` if neither source has data — the caller decides
    whether that's an error (synth path) or just an empty response
    (audio endpoint).
    """
    basename = row_mapping["ref_audio_path"]
    if basename:
        data = voice_fs.read_ref_audio(basename)
        if data is not None:
            return data
    legacy = row_mapping["ref_audio"]
    return bytes(legacy) if legacy else b""


async def get_profile(profile_id: str) -> Profile | None:
    """Full profile including ref audio bytes. Used by the synth path
    and the audio-serving endpoint."""
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(voice_profiles).where(voice_profiles.c.id == profile_id)
            )
        ).first()
    if row is None:
        return None
    return Profile(summary=_row_to_summary(row), ref_audio=_resolve_audio_bytes(row._mapping))


async def get_profile_audio(profile_id: str) -> tuple[bytes, str] | None:
    """Just the ref audio bytes + mime. For the /audio endpoint."""
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    voice_profiles.c.ref_audio,
                    voice_profiles.c.ref_audio_path,
                    voice_profiles.c.ref_audio_mime,
                ).where(voice_profiles.c.id == profile_id)
            )
        ).first()
    if row is None:
        return None
    m = row._mapping
    data = _resolve_audio_bytes(m)
    return data, str(m["ref_audio_mime"] or "audio/wav")


async def delete_profile(profile_id: str) -> bool:
    """True on success. Raises IntegrityError if a binding references this
    profile (FK ON DELETE RESTRICT)."""
    engine = get_engine()
    async with engine.begin() as conn:
        # Capture the basename first so we can clean up disk even when
        # the DB row's FK integrity check is what succeeds last.
        basename_row = (
            await conn.execute(
                select(voice_profiles.c.ref_audio_path).where(
                    voice_profiles.c.id == profile_id
                )
            )
        ).first()
        result = await conn.execute(
            delete(voice_profiles).where(voice_profiles.c.id == profile_id)
        )
    deleted = result.rowcount > 0
    if deleted and basename_row is not None:
        basename = basename_row._mapping["ref_audio_path"]
        if basename:
            voice_fs.delete_ref_audio(basename)
    return deleted


async def profile_is_bound(profile_id: str) -> bool:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(voice_bindings.c.vrm_file)
                .where(voice_bindings.c.profile_id == profile_id)
                .limit(1)
            )
        ).first()
    return row is not None


# ── bindings ───────────────────────────────────────────────────────────


async def list_bindings() -> list[Binding]:
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(select(voice_bindings))).all()
    return [_row_to_binding(r) for r in rows]


async def get_binding(vrm_file: str) -> Binding | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(voice_bindings).where(voice_bindings.c.vrm_file == vrm_file)
            )
        ).first()
    return _row_to_binding(row) if row else None


async def upsert_binding(
    *, vrm_file: str, profile_id: str, updated_by: str | None
) -> Binding:
    engine = get_engine()
    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(voice_bindings).where(
                    voice_bindings.c.vrm_file == vrm_file
                )
            )
        ).first()
        if existing is None:
            await conn.execute(
                insert(voice_bindings).values(
                    vrm_file=vrm_file,
                    profile_id=profile_id,
                    updated_by=updated_by,
                )
            )
        else:
            await conn.execute(
                update(voice_bindings)
                .where(voice_bindings.c.vrm_file == vrm_file)
                .values(profile_id=profile_id, updated_by=updated_by)
            )
        row = (
            await conn.execute(
                select(voice_bindings).where(
                    voice_bindings.c.vrm_file == vrm_file
                )
            )
        ).first()
    if row is None:
        raise RuntimeError(
            f"upsert_binding: failed to read back voice binding vrm_file={vrm_file}"
        )
    return _row_to_binding(row)


async def delete_binding(vrm_file: str) -> bool:
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(voice_bindings).where(voice_bindings.c.vrm_file == vrm_file)
        )
    return result.rowcount > 0


__all__ = [
    "Binding",
    "IntegrityError",
    "Profile",
    "ProfileSummary",
    "create_profile",
    "delete_binding",
    "delete_profile",
    "get_binding",
    "get_profile",
    "get_profile_audio",
    "get_profile_summary",
    "list_bindings",
    "list_profile_summaries",
    "profile_is_bound",
    "upsert_binding",
]
