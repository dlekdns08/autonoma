"""Character memoir compaction — Feature #3 (2026-05 pack).

The ``agent_journal`` table grows monotonically across runs. Stuffing the
full diary into every system prompt is wasteful (tokens) and eventually
infeasible (context window). This module periodically asks an LLM to
fold the unread tail of the journal into a short memoir paragraph and
stores it in two places:

* ``character_memoirs`` (append-only version trail — useful for the UI
  to show how a character's self-narrative grew across runs).
* ``characters.memoir_text`` / ``memoir_version`` (latest-version
  mirror so the system-prompt builder can read the memoir without
  joining tables).

Public surface:

* :func:`get_latest_memoir` — fast read of the mirrored fields.
* :func:`list_memoir_versions` — full version trail for a character.
* :func:`should_compact` — predicate the scheduler polls each round.
* :func:`compact_memoir` — synthesise + persist v+1 (or skip).

The LLM client is duck-typed: any object with an awaitable ``complete``
method that accepts ``(prompt: str)`` and returns a string is fine.
This keeps the module decoupled from ``autonoma.llm`` so tests can swap
in a canned-response stub without standing up a real provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import asc, desc, insert, select, update

from autonoma.config import settings
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import agent_journal, character_memoirs, characters
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ── Public types ──────────────────────────────────────────────────────


@dataclass
class MemoirRecord:
    """One persisted memoir version. Returned by the public API.

    ``journal_id_start`` / ``journal_id_end`` mark the inclusive range of
    ``agent_journal.id`` rows that fed this memoir; the next compaction
    starts from ``journal_id_end + 1``.
    """

    character_uuid: str
    version: int
    text: str
    journal_id_start: int
    journal_id_end: int
    token_estimate: int
    created_at: datetime | None = None


class _LLMLike(Protocol):
    """Minimal duck-typed LLM interface used by :func:`compact_memoir`.

    We avoid importing ``BaseLLMClient`` here so test stubs can be plain
    objects. Any awaitable returning ``str`` is fine.
    """

    async def complete(self, prompt: str) -> str:  # pragma: no cover - protocol
        ...


# ── Helpers ───────────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough char-to-token estimate. 4 chars/token is the standard
    napkin-math heuristic that the rest of the codebase uses too."""
    return len(text) // 4


async def _last_memoir_row(conn, character_uuid: str) -> dict[str, Any] | None:
    """Return the highest-version memoir row for ``character_uuid`` or
    ``None`` if no memoir exists yet."""
    row = (
        await conn.execute(
            select(character_memoirs)
            .where(character_memoirs.c.character_uuid == character_uuid)
            .order_by(desc(character_memoirs.c.version))
            .limit(1)
        )
    ).mappings().first()
    return dict(row) if row else None


async def _journal_tail(
    conn, character_uuid: str, after_id: int
) -> list[dict[str, Any]]:
    """Fetch every journal row for ``character_uuid`` with ``id > after_id``,
    oldest first. Empty list when there's nothing new."""
    rows = (
        await conn.execute(
            select(agent_journal)
            .where(agent_journal.c.character_uuid == character_uuid)
            .where(agent_journal.c.id > after_id)
            .order_by(asc(agent_journal.c.id))
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _character_row(conn, character_uuid: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            select(characters).where(characters.c.character_uuid == character_uuid)
        )
    ).mappings().first()
    return dict(row) if row else None


def _row_to_record(row: dict[str, Any]) -> MemoirRecord:
    return MemoirRecord(
        character_uuid=row["character_uuid"],
        version=int(row["version"]),
        text=row["text"] or "",
        journal_id_start=int(row.get("journal_id_start") or 0),
        journal_id_end=int(row.get("journal_id_end") or 0),
        token_estimate=int(row.get("token_estimate") or 0),
        created_at=row.get("created_at"),
    )


# ── Public API ────────────────────────────────────────────────────────


async def get_latest_memoir(character_uuid: str) -> tuple[str, int]:
    """Return ``(memoir_text, memoir_version)`` from the mirrored
    ``characters`` row. ``("", 0)`` when the character has never been
    compacted (or doesn't exist — same shape so callers don't need a
    branch)."""
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    characters.c.memoir_text,
                    characters.c.memoir_version,
                ).where(characters.c.character_uuid == character_uuid)
            )
        ).first()
    if row is None:
        return "", 0
    return (row[0] or "", int(row[1] or 0))


async def list_memoir_versions(character_uuid: str) -> list[MemoirRecord]:
    """Return every memoir row for the character, oldest version first.

    Useful for the agent-profile UI which shows how the memoir grew
    across compactions.
    """
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(character_memoirs)
                .where(character_memoirs.c.character_uuid == character_uuid)
                .order_by(asc(character_memoirs.c.version))
            )
        ).mappings().all()
    return [_row_to_record(dict(r)) for r in rows]


async def should_compact(character_uuid: str) -> bool:
    """True when the journal grew past
    ``settings.memoir_compact_min_journal_chars`` since the last memoir.

    Returns False if there's nothing new, the threshold is unmet, or the
    feature is disabled (``min_journal_chars <= 0``).
    """
    threshold = int(settings.memoir_compact_min_journal_chars or 0)
    if threshold <= 0:
        return False

    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        last = await _last_memoir_row(conn, character_uuid)
        after_id = int(last["journal_id_end"]) if last else 0
        rows = await _journal_tail(conn, character_uuid, after_id)

    if not rows:
        return False
    new_chars = sum(len(r.get("text") or "") for r in rows)
    return new_chars >= threshold


async def compact_memoir(
    character_uuid: str,
    llm_client: _LLMLike,
    force: bool = False,
) -> MemoirRecord | None:
    """Run an LLM compaction pass.

    Steps:
      1. Pull every ``agent_journal`` row newer than the last memoir's
         ``journal_id_end``. No new rows → return None.
      2. If the new chunk is below
         ``settings.memoir_compact_min_journal_chars`` and ``force`` is
         False, return None (dedup — caller can poll cheaply).
      3. Build a prompt mixing the prior memoir (if any) with the new
         entries. Ask the LLM for a 3-4 sentence first-person update.
      4. Insert into ``character_memoirs`` with version = previous + 1.
      5. Mirror text + version back onto ``characters``.
      6. Emit ``character.memoir_compacted`` on the bus.

    Returns the freshly persisted :class:`MemoirRecord`, or ``None`` if
    the run was skipped (no new entries / threshold unmet).
    """
    await init_db()
    engine = get_engine()

    async with engine.connect() as conn:
        char_row = await _character_row(conn, character_uuid)
        if char_row is None:
            logger.info(
                "memoir.compact: character %s not found, skipping",
                character_uuid,
            )
            return None

        last = await _last_memoir_row(conn, character_uuid)
        last_end = int(last["journal_id_end"]) if last else 0
        prev_text = (last or {}).get("text") or char_row.get("memoir_text") or ""
        prev_version = int(last["version"]) if last else int(char_row.get("memoir_version") or 0)

        rows = await _journal_tail(conn, character_uuid, last_end)

    if not rows:
        return None

    new_chars = sum(len(r.get("text") or "") for r in rows)
    threshold = int(settings.memoir_compact_min_journal_chars or 0)
    if not force and threshold > 0 and new_chars < threshold:
        return None

    prompt = _build_prompt(
        name=char_row.get("name") or "the character",
        role=char_row.get("role") or "agent",
        prev_memoir=prev_text,
        rows=rows,
    )

    try:
        memoir_text = (await llm_client.complete(prompt)).strip()
    except Exception:
        logger.exception(
            "memoir.compact: LLM call failed for %s", character_uuid
        )
        raise

    if not memoir_text:
        # Treat empty output as a soft failure — leave the previous
        # version in place so we don't replace useful text with "".
        logger.warning(
            "memoir.compact: LLM returned empty memoir for %s, skipping",
            character_uuid,
        )
        return None

    new_version = prev_version + 1
    journal_id_start = int(rows[0]["id"])
    journal_id_end = int(rows[-1]["id"])
    token_estimate = _estimate_tokens(memoir_text)

    async with engine.begin() as conn:
        result = await conn.execute(
            insert(character_memoirs).values(
                character_uuid=character_uuid,
                version=new_version,
                text=memoir_text,
                journal_id_start=journal_id_start,
                journal_id_end=journal_id_end,
                token_estimate=token_estimate,
            )
        )
        await conn.execute(
            update(characters)
            .where(characters.c.character_uuid == character_uuid)
            .values(
                memoir_text=memoir_text,
                memoir_version=new_version,
            )
        )
        # Refetch the row so ``created_at`` reflects the server default.
        new_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
        if new_id is not None:
            inserted = (
                await conn.execute(
                    select(character_memoirs).where(character_memoirs.c.id == new_id)
                )
            ).mappings().first()
        else:
            inserted = None

    if inserted is not None:
        record = _row_to_record(dict(inserted))
    else:
        record = MemoirRecord(
            character_uuid=character_uuid,
            version=new_version,
            text=memoir_text,
            journal_id_start=journal_id_start,
            journal_id_end=journal_id_end,
            token_estimate=token_estimate,
            created_at=None,
        )

    await bus.emit(
        "character.memoir_compacted",
        character_uuid=character_uuid,
        version=new_version,
        length=len(memoir_text),
    )
    logger.info(
        "memoir.compact: character %s → v%d (%d chars, %d source rows)",
        character_uuid, new_version, len(memoir_text), len(rows),
    )
    return record


# ── Prompt construction ───────────────────────────────────────────────


def _build_prompt(
    *,
    name: str,
    role: str,
    prev_memoir: str,
    rows: list[dict[str, Any]],
) -> str:
    """Compose the LLM prompt.

    Layout matches the spec:

        Below are recent diary entries by {name}, a {role}. Write a 3-4
        sentence memoir update in first person, blending these into
        earlier memoir if any. Earlier memoir: {prev}. New entries:
        {entries}
    """
    prev_block = prev_memoir.strip() or "(none)"
    entries_block = "\n".join(_format_row(r) for r in rows)
    return (
        f"Below are recent diary entries by {name}, a {role}. "
        f"Write a 3-4 sentence memoir update in first person, blending "
        f"these into earlier memoir if any. "
        f"Earlier memoir: {prev_block}. "
        f"New entries: {entries_block}"
    )


def _format_row(row: dict[str, Any]) -> str:
    """Render one journal row as a short prefixed line for the prompt."""
    rnd = row.get("round_number") or 0
    mood = (row.get("mood") or "").strip()
    kind = (row.get("kind") or "diary").strip()
    text = (row.get("text") or "").strip()
    parts = [f"R{rnd}", kind]
    if mood:
        parts.append(mood)
    return f"[{' '.join(parts)}] {text}"
