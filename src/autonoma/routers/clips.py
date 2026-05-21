"""Highlight clip generator — FastAPI router (MVP).

Endpoints
─────────
::

    POST /api/clips                 — multipart upload of a recorded blob
                                       (file + session_id + duration_ms + title?)
                                       → returns ``{id, url}``
    GET  /api/clips/{id}            — stream the blob back as a file
    GET  /api/clips?session_id=     — list every clip recorded for a session

Storage model
─────────────
The browser captures a rolling 30s buffer of the watch page's canvas
via ``MediaRecorder`` (see ``web/src/hooks/useRollingRecorder.ts``). On
upload we trust the declared mime + duration — server-side ffprobe is
out of scope for the MVP. Bytes land on disk under
``{data_dir}/clips/{id}.{ext}``; the DB row only carries metadata + the
absolute file path so backups can move the data dir freely.

Why a dedicated router?  ``/api/highlights/*`` already exists, but that
surface is for *event-detection* highlight candidates (timestamps the
auto-clipper might cut from a server-side recording, see
``autonoma.highlights``). The clip generator is a separate concern: the
browser does the encoding, the server is just a CDN-shaped sink + share
URL issuer.

Auth model: ``require_active_user`` on every endpoint. Uploads must
target a session the caller owns (or be admin) — same convention as
``quests.py``. Reads are open to any authed viewer so a clip URL is
shareable across a stream's audience without copying ownership.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi import status as http_status
from fastapi.responses import FileResponse
from sqlalchemy import desc, insert, select

from autonoma._session_owner import assert_session_owner_or_admin
from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import clips as clips_table

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clips"])


# ── Constraints ───────────────────────────────────────────────────────
# Cap upload size so a runaway recorder can't fill disk. 30s of canvas
# capture at modest bitrates (~2.5 Mbit/s) lands around 9 MB; 64 MB
# headroom covers HD/longer clips while still being a sane MVP ceiling.
MAX_CLIP_BYTES: int = 64 * 1024 * 1024

# Mime → file extension map. We trust the declared content-type but
# normalise the on-disk extension so a browser that emits an obscure
# codec subtype still lands on a recognisable filename. MP4 lands on
# ``.mp4`` regardless of the codec param (e.g. "video/mp4;codecs=...")
# so OS players can sniff it correctly.
_MIME_TO_EXT: dict[str, str] = {
    "video/webm": "webm",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/x-matroska": "mkv",
}

# Pure prefix matcher used when the browser includes codec params in
# ``Content-Type`` (most do: ``video/webm;codecs=vp9,opus``). Keeps the
# extension resolution stable without forcing a strict equality match.
_MIME_PREFIX_TO_EXT: list[tuple[str, str]] = [
    ("video/webm", "webm"),
    ("video/mp4", "mp4"),
    ("video/quicktime", "mov"),
    ("video/x-matroska", "mkv"),
]

_TITLE_MAX_LEN = 128


def _err(status: int, code: str, message: str) -> HTTPException:
    """Mirrors the structured-error helper used by the rest of the routers."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _resolve_extension(mime: str) -> str:
    """Map a declared mime to an on-disk extension.

    Defaults to ``webm`` because that's what every Chromium-based browser
    emits and our recorder hook prefers it. Unknown mimes still get a
    valid extension (``bin``) so we don't refuse to persist a weird
    container — playback will fail naturally in the browser instead.
    """
    m = mime.lower().strip()
    if m in _MIME_TO_EXT:
        return _MIME_TO_EXT[m]
    for prefix, ext in _MIME_PREFIX_TO_EXT:
        if m.startswith(prefix):
            return ext
    return "webm"


def _clips_dir() -> Path:
    """Resolve and ensure ``{data_dir}/clips`` exists.

    Computed lazily so test runs that override ``data_dir`` see the
    override; the directory is created on first use rather than module
    import so importing this router stays side-effect free.
    """
    d = settings.data_dir / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _row_to_dict(row: Any) -> dict[str, Any]:
    m = row._mapping if hasattr(row, "_mapping") else row
    created = m["created_at"]
    if isinstance(created, datetime):
        created_iso = created.isoformat()
    else:
        created_iso = str(created) if created is not None else None
    return {
        "id": str(m["id"]),
        "session_id": int(m["session_id"]),
        "owner_id": str(m["owner_id"]),
        "title": str(m["title"]),
        "duration_ms": int(m["duration_ms"]),
        "mime": str(m["mime"]),
        "url": f"/api/clips/{m['id']}",
        "created_at": created_iso,
    }


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/api/clips", status_code=http_status.HTTP_201_CREATED)
async def create_clip(
    file: UploadFile = File(...),
    session_id: int = Form(...),
    duration_ms: int = Form(...),
    title: str = Form(""),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Persist a browser-recorded clip blob.

    The caller's MediaRecorder slices its rolling 30s buffer and POSTs
    the resulting blob here. We stash the bytes on disk and record the
    metadata row; the response carries the share URL the UI surfaces in
    a toast.
    """
    # Coerce + validate before we touch the disk or the DB.
    try:
        sid = int(session_id)
        dur = max(0, int(duration_ms))
    except (TypeError, ValueError):
        raise _err(400, "invalid_input", "session_id and duration_ms must be integers.")

    cleaned_title = (title or "").strip()[:_TITLE_MAX_LEN]
    # Strip control characters; titles are surfaced in HTML so we keep
    # them simple. Allow letters/digits/punct/whitespace.
    cleaned_title = re.sub(r"[\x00-\x1f\x7f]", "", cleaned_title)

    assert_session_owner_or_admin(sid, user)

    # Read upfront so we can enforce the size cap before we commit to a
    # path on disk. MediaRecorder blobs are small enough (< 64 MB) that
    # buffering in memory is fine for the MVP — streaming uploads can
    # come later if we ever loosen the cap.
    data = await file.read()
    if not data:
        raise _err(400, "empty_clip", "clip body is empty.")
    if len(data) > MAX_CLIP_BYTES:
        raise _err(
            413,
            "clip_too_large",
            f"clip exceeds the {MAX_CLIP_BYTES // (1024 * 1024)} MB ceiling.",
        )

    declared_mime = (file.content_type or "video/webm").strip()
    ext = _resolve_extension(declared_mime)

    await init_db()
    clip_id = str(uuid.uuid4())
    target = _clips_dir() / f"{clip_id}.{ext}"
    try:
        target.write_bytes(data)
    except OSError as exc:
        logger.exception("[clips] write failed for %s: %s", clip_id, exc)
        raise _err(500, "write_failed", "could not write clip to disk.")

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                insert(clips_table).values(
                    id=clip_id,
                    session_id=sid,
                    owner_id=user.id,
                    title=cleaned_title,
                    duration_ms=dur,
                    mime=declared_mime,
                    file_path=str(target),
                )
            )
    except Exception as exc:
        # Roll back the on-disk write so we don't leak orphan files
        # when the DB insert fails (e.g. uniqueness collision on the
        # uuid — astronomically unlikely but not impossible).
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning("[clips] orphan cleanup failed for %s", target)
        logger.exception("[clips] DB insert failed for %s: %s", clip_id, exc)
        raise _err(500, "db_failed", "could not record clip metadata.")

    logger.info(
        "[clips] saved id=%s session=%s owner=%s bytes=%d mime=%s dur_ms=%d",
        clip_id,
        sid,
        user.id,
        len(data),
        declared_mime,
        dur,
    )
    return {
        "status": "ok",
        "id": clip_id,
        "url": f"/api/clips/{clip_id}",
    }


@router.get("/api/clips/{clip_id}")
async def get_clip(
    clip_id: str,
    _user: User = Depends(require_active_user),
) -> FileResponse:
    """Stream the blob bytes back.

    Returns a ``FileResponse`` so FastAPI handles range requests, MIME
    negotiation, and content-length for us — important for the
    ``<video>`` tag's seek-to-position UX. Auth is "any active user"
    so a shared clip URL works for anyone in the stream's audience.
    """
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(clips_table).where(clips_table.c.id == clip_id))
        ).first()
    if row is None:
        raise _err(404, "clip_not_found", "no clip with that id.")

    m = row._mapping
    path = Path(str(m["file_path"]))
    if not path.exists():
        # DB row outlived the file (manual cleanup, restored backup
        # without the data dir). Surface as 404 — the share URL is
        # effectively dead.
        logger.warning("[clips] file missing for clip id=%s path=%s", clip_id, path)
        raise _err(404, "clip_not_found", "clip file no longer on disk.")

    mime = str(m["mime"]) or "video/webm"
    # Pick a friendly download filename — defaults to the title when
    # one was set, otherwise the clip id.
    title = str(m["title"]).strip()
    base_name = title if title else clip_id
    base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name)[:64] or clip_id
    return FileResponse(
        path=path,
        media_type=mime,
        filename=f"{base_name}{path.suffix}",
    )


@router.get("/api/clips")
async def list_clips(
    session_id: int = Query(..., description="live session id"),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """List every clip recorded against ``session_id``.

    Mirrors ``GET /api/quests``: owner-or-admin scope, newest-first
    ordering by ``created_at``. The list endpoint exists primarily so
    a future clip drawer on the watch page can render the back-catalog
    without round-tripping each id individually.
    """
    assert_session_owner_or_admin(int(session_id), user)
    await init_db()
    engine = get_engine()
    stmt = (
        select(clips_table)
        .where(clips_table.c.session_id == int(session_id))
        .order_by(desc(clips_table.c.created_at), desc(clips_table.c.id))
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return {
        "session_id": int(session_id),
        "count": len(rows),
        "clips": [_row_to_dict(r) for r in rows],
    }
