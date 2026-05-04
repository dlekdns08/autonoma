"""Live quest designer — feature #14.

Host/viewers *propose* short quest cards during a live session, viewers
*vote* on them, and the highest-voted card can be *activated* by the
operator for one round. While active, the swarm receives the quest as a
buff/objective broadcast; once the round is done the card is *completed*.

State machine
─────────────
    proposed ──► active ──► completed
        │
        └──► skipped       (terminal, no activation)

Persistence is in the ``live_quests`` table created by migration 012.
This module owns the read/write paths plus the bus emits that broadcast
state transitions to the rest of the system:

  * ``quest.proposed``  on every successful propose
  * ``quest.activated`` when ``activate_top_quest`` flips a row to active
  * ``quest.completed`` when ``complete_quest`` finalizes a round

Each event payload carries ``session_id`` + ``quest_id``; activation /
completion also include the relevant ``round_number`` so subscribers
can correlate the buff window with their own per-round state.

The router (see ``autonoma.routers.quests``) handles the dedup-per-
viewer rule for votes; this module deliberately stays "fire and forget"
about who voted so that other callers (CLI tools, headless schedulers,
batch importers) can drive the state machine without having to fake a
cookie session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, insert, select, update

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import live_quests
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

# Mirrors the column width on ``live_quests.text``. Exposed as a module
# constant so the router can reuse it for input validation without
# duplicating the magic number.
MAX_TEXT_LEN: int = 256

# Lifecycle states. Anything not in this set is rejected at the DB
# layer because the column is fixed-width VARCHAR(16); enumerating them
# here keeps callers honest.
STATUS_PROPOSED: str = "proposed"
STATUS_ACTIVE: str = "active"
STATUS_COMPLETED: str = "completed"
STATUS_SKIPPED: str = "skipped"


# ── Public dataclass ──────────────────────────────────────────────────


@dataclass
class QuestRecord:
    """Plain-data view of a ``live_quests`` row.

    All fields mirror the column names so callers can dump this straight
    into a JSON response. ``activated_round`` and ``completed_round`` are
    NULL until the corresponding lifecycle hook runs.
    """

    id: int
    session_id: int
    text: str
    votes: int
    status: str
    created_at: datetime | None
    activated_round: int | None
    completed_round: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "text": self.text,
            "votes": self.votes,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "activated_round": self.activated_round,
            "completed_round": self.completed_round,
        }


# ── Errors ────────────────────────────────────────────────────────────


class QuestError(ValueError):
    """Base error for quest validation failures.

    Subclasses get a stable ``code`` attribute the router can map to a
    structured ``{"code": ..., "message": ...}`` HTTP detail body.
    """

    code: str = "quest_error"


class QuestTextEmpty(QuestError):
    code = "quest_text_empty"


class QuestTextTooLong(QuestError):
    code = "quest_text_too_long"


class QuestNotFound(QuestError):
    code = "quest_not_found"


# ── Internal helpers ──────────────────────────────────────────────────


def _row_to_record(row: Any) -> QuestRecord:
    m = row._mapping if hasattr(row, "_mapping") else row
    return QuestRecord(
        id=int(m["id"]),
        session_id=int(m["session_id"]),
        text=str(m["text"]),
        votes=int(m["votes"]),
        status=str(m["status"]),
        created_at=m["created_at"],
        activated_round=(
            int(m["activated_round"]) if m["activated_round"] is not None else None
        ),
        completed_round=(
            int(m["completed_round"]) if m["completed_round"] is not None else None
        ),
    )


async def _fetch_one(quest_id: int) -> QuestRecord | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(live_quests).where(live_quests.c.id == quest_id)
            )
        ).first()
    return _row_to_record(row) if row else None


# ── Public API ────────────────────────────────────────────────────────


async def propose_quest(session_id: int, text: str) -> int:
    """Insert a fresh quest row in the ``proposed`` state.

    Returns the new row id. Raises :class:`QuestTextEmpty` on blank input
    and :class:`QuestTextTooLong` on text > :data:`MAX_TEXT_LEN`. The
    caller is expected to have done the cookie-auth check already; this
    function is intentionally permissive about who proposes so a future
    batch importer or CLI tool can reuse it without fabricating a
    session.

    On success emits ``quest.proposed`` with ``session_id`` + ``quest_id``
    so live subscribers (the design board UI, vote tally) can react
    without polling.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise QuestTextEmpty("quest text must not be empty")
    if len(cleaned) > MAX_TEXT_LEN:
        raise QuestTextTooLong(
            f"quest text exceeds {MAX_TEXT_LEN} characters (got {len(cleaned)})"
        )

    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            insert(live_quests).values(
                session_id=int(session_id),
                text=cleaned,
                votes=0,
                status=STATUS_PROPOSED,
            )
        )
        # ``inserted_primary_key`` is the most portable way to recover
        # the autoincrement id across SQLite/Postgres backends.
        quest_id = int(result.inserted_primary_key[0])

    await bus.emit(
        "quest.proposed",
        session_id=int(session_id),
        quest_id=quest_id,
        text=cleaned,
    )
    return quest_id


async def vote_quest(quest_id: int) -> int:
    """Atomically increment ``votes`` for an existing quest.

    Returns the new total. Returns ``0`` if the row doesn't exist; we
    don't raise because a missing row at vote time is almost always a
    benign race (the operator skipped/activated a different quest in the
    same tick) and the router already checks dedup before calling.

    Implementation note: the SQL is a single ``UPDATE ... SET votes =
    votes + 1`` so concurrent increments stay consistent without an
    application-level lock. The follow-up ``SELECT`` is in the same
    transaction so the returned total is the post-increment value.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        update_result = await conn.execute(
            update(live_quests)
            .where(live_quests.c.id == int(quest_id))
            .values(votes=live_quests.c.votes + 1)
        )
        if update_result.rowcount == 0:
            return 0
        row = (
            await conn.execute(
                select(live_quests.c.votes).where(live_quests.c.id == int(quest_id))
            )
        ).first()
    if row is None:
        return 0
    return int(row._mapping["votes"])


async def list_quests(
    session_id: int, status: str | None = None
) -> list[QuestRecord]:
    """Return every quest for ``session_id``, newest highest-voted first.

    Sort order is ``votes DESC, created_at DESC`` so the design board UI
    can render the leaderboard without re-sorting client-side. When
    ``status`` is given the list is filtered to that single state; passing
    ``None`` returns every row regardless of state (useful for audit /
    history views).
    """
    await init_db()
    engine = get_engine()
    stmt = (
        select(live_quests)
        .where(live_quests.c.session_id == int(session_id))
        .order_by(desc(live_quests.c.votes), desc(live_quests.c.created_at))
    )
    if status is not None:
        stmt = stmt.where(live_quests.c.status == status)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [_row_to_record(r) for r in rows]


async def activate_top_quest(
    session_id: int, round_number: int
) -> QuestRecord | None:
    """Promote the highest-voted ``proposed`` quest in this session.

    Returns the activated record, or ``None`` if no proposed quests are
    available (in which case the caller can skip the round's quest slot).

    Tie-break: ``votes DESC, created_at ASC`` — among equally-popular
    proposals we activate the one that was suggested first, rewarding
    early ideation. ``activated_round`` is set on the row so the
    completion path can correlate buff windows even after the active
    flag flips off.

    On success emits ``quest.activated`` with the full payload so the
    coordinator can inject the buff into the swarm context for this
    round.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        # Lock-free pick: smallest id among the top-voted proposed rows.
        row = (
            await conn.execute(
                select(live_quests)
                .where(live_quests.c.session_id == int(session_id))
                .where(live_quests.c.status == STATUS_PROPOSED)
                .order_by(
                    desc(live_quests.c.votes),
                    live_quests.c.created_at.asc(),
                    live_quests.c.id.asc(),
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None

        quest_id = int(row._mapping["id"])
        await conn.execute(
            update(live_quests)
            .where(live_quests.c.id == quest_id)
            # Guard against a concurrent activator flipping the same
            # row first — if the status moved off ``proposed`` between
            # our select and this update we simply skip; the caller
            # treats ``rowcount == 0`` as "another worker won".
            .where(live_quests.c.status == STATUS_PROPOSED)
            .values(status=STATUS_ACTIVE, activated_round=int(round_number))
        )

    record = await _fetch_one(quest_id)
    if record is None or record.status != STATUS_ACTIVE:
        # Concurrent activator beat us — nothing to broadcast.
        return None

    await bus.emit(
        "quest.activated",
        session_id=int(session_id),
        quest_id=quest_id,
        text=record.text,
        round_number=int(round_number),
        votes=record.votes,
    )
    logger.info(
        "[quests] activated quest_id=%d session_id=%d round=%d (votes=%d)",
        quest_id, session_id, round_number, record.votes,
    )
    return record


async def complete_quest(quest_id: int, round_number: int) -> bool:
    """Mark ``quest_id`` as completed at ``round_number``.

    Returns ``True`` on a successful flip, ``False`` if the row doesn't
    exist (the operator complete-pressed a stale id from a closed
    session). Idempotent on repeated calls — re-completing an already-
    completed quest still returns ``True`` and re-emits the bus event so
    multi-listener consumers all receive the closing tick.

    Emits ``quest.completed`` with ``session_id``, ``quest_id``, and
    ``round_number``.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            update(live_quests)
            .where(live_quests.c.id == int(quest_id))
            .values(status=STATUS_COMPLETED, completed_round=int(round_number))
        )
        if result.rowcount == 0:
            return False
        row = (
            await conn.execute(
                select(live_quests.c.session_id, live_quests.c.text)
                .where(live_quests.c.id == int(quest_id))
            )
        ).first()
    if row is None:
        return False

    await bus.emit(
        "quest.completed",
        session_id=int(row._mapping["session_id"]),
        quest_id=int(quest_id),
        text=str(row._mapping["text"]),
        round_number=int(round_number),
    )
    logger.info(
        "[quests] completed quest_id=%d round=%d", quest_id, round_number,
    )
    return True


async def get_quest(quest_id: int) -> QuestRecord | None:
    """Convenience accessor — used by the router for 404 checks.

    Kept thin (single SELECT) so callers can chain it cheaply without
    pulling the whole ``list_quests`` payload.
    """
    return await _fetch_one(quest_id)
