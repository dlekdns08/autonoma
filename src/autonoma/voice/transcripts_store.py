"""DB layer for the ``voice_transcripts`` table — feature #1.

Mirrors the style of ``voice/store.py`` (small focused module) but for
the user→agent direction of the voice round-trip. Two operations are
all the studio + audit dashboard actually need:

  * ``record(...)`` — best-effort INSERT after a successful transcribe.
    Best-effort because we never want a transient DB error to take
    down a transcribe response that the user already heard.
  * ``list_recent(...)`` — paginated reads, optionally scoped to one
    user or one swarm session.

Partials are *not* logged — they're throwaway intermediates that
converge to the final, and storing them would 30× the row count for
no audit value.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, insert, select

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import voice_transcripts

logger = logging.getLogger(__name__)


@dataclass
class TranscriptRow:
    id: str
    user_id: str
    session_id: int | None
    stage: str
    text: str
    language: str
    duration_ms: int
    model: str
    route_action: str
    route_target: str | None
    created_at: datetime


def _row_to_dataclass(row: Any) -> TranscriptRow:
    return TranscriptRow(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        stage=row.stage,
        text=row.text,
        language=row.language or "",
        duration_ms=row.duration_ms or 0,
        model=row.model or "",
        route_action=row.route_action or "",
        route_target=row.route_target,
        created_at=row.created_at,
    )


async def record(
    *,
    user_id: str,
    text: str,
    stage: str,
    language: str = "",
    duration_ms: int = 0,
    model: str = "",
    route_action: str = "",
    route_target: str | None = None,
    session_id: int | None = None,
) -> str | None:
    """Insert one row. Returns the row id or ``None`` on failure.

    Failures are swallowed and logged — the caller is in a hot path
    (mid-WebSocket or mid-HTTP-response) and we'd rather lose the audit
    row than fail a user-visible transcribe.
    """
    if not text.strip():
        return None
    await init_db()
    row_id = str(uuid.uuid4())
    # SQLite stores varchar with no real length cap, but we cap on the
    # write side to match the column definition (4096) so a runaway
    # giant transcript can't bloat the row.
    capped = text[:4096]
    try:
        async with get_engine().begin() as conn:
            await conn.execute(
                insert(voice_transcripts).values(
                    id=row_id,
                    user_id=user_id,
                    session_id=session_id,
                    stage=stage,
                    text=capped,
                    language=language or "",
                    duration_ms=int(duration_ms or 0),
                    model=model or "",
                    route_action=route_action or "",
                    route_target=route_target,
                )
            )
        return row_id
    except Exception as exc:
        # Don't let an audit failure cascade. The exception is logged
        # so an operator can still spot misconfigured DB credentials.
        logger.warning(f"[voice/transcripts] record failed: {exc}")
        return None


async def list_recent(
    *,
    user_id: str | None = None,
    session_id: int | None = None,
    limit: int = 50,
) -> list[TranscriptRow]:
    """Return up to ``limit`` most-recent transcripts, newest first.

    ``user_id`` filters to the requesting account so a non-admin user
    can only see their own utterances. The /voice studio page passes
    the current user's id; an admin dashboard can pass ``None``.
    """
    await init_db()
    cap = max(1, min(limit, 500))
    stmt = select(voice_transcripts).order_by(desc(voice_transcripts.c.created_at)).limit(cap)
    if user_id is not None:
        stmt = stmt.where(voice_transcripts.c.user_id == user_id)
    if session_id is not None:
        stmt = stmt.where(voice_transcripts.c.session_id == session_id)
    async with get_engine().begin() as conn:
        result = await conn.execute(stmt)
        rows = result.fetchall()
    return [_row_to_dataclass(r) for r in rows]
