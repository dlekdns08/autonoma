"""Database access for mocap clips and bindings.

Thin SQLAlchemy Core wrappers, mirroring the style of ``db.users`` and
``db.harness_policies``. All functions are async and borrow a connection
from the shared engine.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine
from autonoma.db.schema import mocap_bindings, mocap_clips
from autonoma.mocap.validator import ValidatedClip


@dataclass(slots=True)
class ClipSummary:
    id: str
    owner_user_id: str
    name: str
    source_vrm: str
    duration_s: float
    fps: int
    frame_count: int
    size_bytes: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "name": self.name,
            "source_vrm": self.source_vrm,
            "duration_s": self.duration_s,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class Binding:
    vrm_file: str
    trigger_kind: str
    trigger_value: str
    clip_id: str
    updated_by: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "vrm_file": self.vrm_file,
            "trigger_kind": self.trigger_kind,
            "trigger_value": self.trigger_value,
            "clip_id": self.clip_id,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


def _row_to_summary(row: Any) -> ClipSummary:
    m = row._mapping
    return ClipSummary(
        id=m["id"],
        owner_user_id=m["owner_user_id"],
        name=m["name"],
        source_vrm=m["source_vrm"],
        duration_s=float(m["duration_s"]),
        fps=int(m["fps"]),
        frame_count=int(m["frame_count"]),
        size_bytes=int(m["size_bytes"]),
        created_at=str(m["created_at"]),
        updated_at=str(m["updated_at"]),
    )


def _row_to_binding(row: Any) -> Binding:
    m = row._mapping
    return Binding(
        vrm_file=m["vrm_file"],
        trigger_kind=m["trigger_kind"],
        trigger_value=m["trigger_value"],
        clip_id=m["clip_id"],
        updated_by=m["updated_by"],
        updated_at=str(m["updated_at"]),
    )


# ── clips ──────────────────────────────────────────────────────────────


async def create_clip(
    *, owner_user_id: str, validated: ValidatedClip
) -> ClipSummary:
    clip_id = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(mocap_clips).values(
                id=clip_id,
                owner_user_id=owner_user_id,
                name=validated.name,
                source_vrm=validated.source_vrm,
                duration_s=validated.duration_s,
                fps=validated.fps,
                frame_count=validated.frame_count,
                payload_gz=validated.payload_gz,
                size_bytes=validated.size_bytes,
            )
        )
        row = (
            await conn.execute(
                select(mocap_clips).where(mocap_clips.c.id == clip_id)
            )
        ).first()
    assert row is not None
    return _row_to_summary(row)


async def list_clips_for_user(user_id: str) -> list[ClipSummary]:
    """Return the user's clips + any clip referenced by a binding.

    Bindings are global, so a viewer needs to be able to fetch a clip's
    payload regardless of who uploaded it — but the library listing
    itself scopes to owner + bound clips so users don't accidentally
    drown in every clip ever uploaded.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        # Owner's own clips.
        owner_rows = (
            await conn.execute(
                select(mocap_clips).where(mocap_clips.c.owner_user_id == user_id)
            )
        ).all()

        # Clips referenced by any binding — surfaces shared clips the
        # user might want to rebind.
        bound_rows = (
            await conn.execute(
                select(mocap_clips).where(
                    mocap_clips.c.id.in_(
                        select(mocap_bindings.c.clip_id).distinct()
                    )
                )
            )
        ).all()

    seen: dict[str, ClipSummary] = {}
    for row in [*owner_rows, *bound_rows]:
        summary = _row_to_summary(row)
        seen[summary.id] = summary
    # Newest first.
    return sorted(seen.values(), key=lambda c: c.created_at, reverse=True)


async def get_clip_summary(clip_id: str) -> ClipSummary | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(mocap_clips).where(mocap_clips.c.id == clip_id))
        ).first()
    return _row_to_summary(row) if row else None


async def get_clip_payload(clip_id: str) -> tuple[ClipSummary, str] | None:
    """Return (summary, base64-encoded gzipped payload)."""
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(mocap_clips).where(mocap_clips.c.id == clip_id))
        ).first()
    if row is None:
        return None
    summary = _row_to_summary(row)
    payload = base64.b64encode(row._mapping["payload_gz"]).decode("ascii")
    return summary, payload


async def rename_clip(clip_id: str, new_name: str) -> ClipSummary | None:
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            update(mocap_clips)
            .where(mocap_clips.c.id == clip_id)
            .values(name=new_name)
        )
        if result.rowcount == 0:
            return None
        row = (
            await conn.execute(select(mocap_clips).where(mocap_clips.c.id == clip_id))
        ).first()
    return _row_to_summary(row) if row else None


async def delete_clip(clip_id: str) -> bool:
    """``True`` on success. Raises ``IntegrityError`` if a binding still
    references the clip (FK ON DELETE RESTRICT)."""
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(mocap_clips).where(mocap_clips.c.id == clip_id)
        )
    return result.rowcount > 0


async def clip_is_bound(clip_id: str) -> bool:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(mocap_bindings.c.vrm_file).where(
                    mocap_bindings.c.clip_id == clip_id
                ).limit(1)
            )
        ).first()
    return row is not None


# ── bindings ───────────────────────────────────────────────────────────


async def list_bindings() -> list[Binding]:
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(select(mocap_bindings))).all()
    return [_row_to_binding(r) for r in rows]


async def get_binding(
    vrm_file: str, kind: str, value: str
) -> Binding | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(mocap_bindings).where(
                    and_(
                        mocap_bindings.c.vrm_file == vrm_file,
                        mocap_bindings.c.trigger_kind == kind,
                        mocap_bindings.c.trigger_value == value,
                    )
                )
            )
        ).first()
    return _row_to_binding(row) if row else None


async def upsert_binding(
    *,
    vrm_file: str,
    trigger_kind: str,
    trigger_value: str,
    clip_id: str,
    updated_by: str | None,
) -> Binding:
    engine = get_engine()
    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                select(mocap_bindings).where(
                    and_(
                        mocap_bindings.c.vrm_file == vrm_file,
                        mocap_bindings.c.trigger_kind == trigger_kind,
                        mocap_bindings.c.trigger_value == trigger_value,
                    )
                )
            )
        ).first()
        if existing is None:
            await conn.execute(
                insert(mocap_bindings).values(
                    vrm_file=vrm_file,
                    trigger_kind=trigger_kind,
                    trigger_value=trigger_value,
                    clip_id=clip_id,
                    updated_by=updated_by,
                )
            )
        else:
            await conn.execute(
                update(mocap_bindings)
                .where(
                    and_(
                        mocap_bindings.c.vrm_file == vrm_file,
                        mocap_bindings.c.trigger_kind == trigger_kind,
                        mocap_bindings.c.trigger_value == trigger_value,
                    )
                )
                .values(clip_id=clip_id, updated_by=updated_by)
            )
        row = (
            await conn.execute(
                select(mocap_bindings).where(
                    and_(
                        mocap_bindings.c.vrm_file == vrm_file,
                        mocap_bindings.c.trigger_kind == trigger_kind,
                        mocap_bindings.c.trigger_value == trigger_value,
                    )
                )
            )
        ).first()
    assert row is not None
    return _row_to_binding(row)


async def delete_binding(
    *, vrm_file: str, trigger_kind: str, trigger_value: str
) -> bool:
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(mocap_bindings).where(
                and_(
                    mocap_bindings.c.vrm_file == vrm_file,
                    mocap_bindings.c.trigger_kind == trigger_kind,
                    mocap_bindings.c.trigger_value == trigger_value,
                )
            )
        )
    return result.rowcount > 0


__all__ = [
    "Binding",
    "ClipSummary",
    "IntegrityError",
    "clip_is_bound",
    "create_clip",
    "delete_binding",
    "delete_clip",
    "get_binding",
    "get_clip_payload",
    "get_clip_summary",
    "list_bindings",
    "list_clips_for_user",
    "rename_clip",
    "upsert_binding",
]
