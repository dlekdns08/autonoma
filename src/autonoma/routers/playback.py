"""Timeline playback — feature #11.

Thin wrapper around the existing checkpoint table. The admin UI
subscribes to this to render a scrubber over past sessions:

  * ``GET /api/playback/{session_id}/frames`` — list every saved
    checkpoint for a session, newest first, with a short summary for
    each (round, wall time, state size, number of active agents).
  * ``GET /api/playback/{session_id}/frame/{round}`` — return the
    full deserialized state dict at that round. The UI reconstructs
    the scene from that payload.

Actual "rewind and run from here" requires
``AgentSwarm.start_from_checkpoint()`` which doesn't exist yet —
that hook is kept explicit in the resume endpoint so this playback
route stays read-only and safe.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import desc, select

from autonoma.auth import User, require_active_user
from autonoma.db.engine import get_engine
from autonoma.db.schema import session_checkpoint

logger = logging.getLogger(__name__)

router = APIRouter(tags=["playback"])


def _decode_state(raw: Any) -> dict[str, Any]:
    """Parse the ``state_json`` column.

    Historically this was a gzipped bytes blob; the current schema stores
    plain TEXT. The decoder accepts both so we can't regress when reading
    old rows from upgraded deployments.
    """
    if isinstance(raw, (bytes, bytearray, memoryview)):
        try:
            import gzip
            text = gzip.decompress(bytes(raw)).decode("utf-8")
        except OSError:
            text = bytes(raw).decode("utf-8", errors="replace")
    else:
        text = str(raw or "")
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"raw": text}


@router.get("/api/playback/{session_id}/frames")
async def playback_frames(
    session_id: int,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    session_checkpoint.c.round_number,
                    session_checkpoint.c.created_at,
                    session_checkpoint.c.state_json,
                )
                .where(session_checkpoint.c.session_id == session_id)
                .order_by(desc(session_checkpoint.c.round_number))
            )
        ).all()
    frames: list[dict[str, Any]] = []
    for r in rows:
        m = r._mapping
        raw = m["state_json"]
        size = (
            len(raw.encode("utf-8")) if isinstance(raw, str)
            else len(bytes(raw)) if raw is not None else 0
        )
        frames.append({
            "round": int(m["round_number"]),
            "at": str(m["created_at"]),
            "size_bytes": size,
        })
    return {"session_id": session_id, "frames": frames}


@router.get("/api/playback/{session_id}/frame/{round_number}")
async def playback_frame(
    session_id: int,
    round_number: int,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(session_checkpoint.c.state_json)
                .where(session_checkpoint.c.session_id == session_id)
                .where(session_checkpoint.c.round_number == round_number)
            )
        ).first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "frame_not_found", "message": "해당 라운드의 체크포인트가 없습니다."},
        )
    state = _decode_state(row._mapping["state_json"])
    if not state:
        state = {}
    return {"session_id": session_id, "round": round_number, "state": state}
