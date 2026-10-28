"""DB persistence helpers used by the swarm runner and the run/checkpoint routes.

Extracted from ``autonoma.api`` (re-exported there) so the main module
stays focused on routing. Each helper opens its own transaction on the
shared async engine and swallows failures with a warning — persistence
is best-effort and must never abort a live swarm round.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── /api/files/history — Feature 9: File Version History ──────────────────


async def _insert_file_history(session_id: int, path: str, content: str, created_by: str) -> None:
    """Insert a new version row into ``file_history``.

    The ``version_number`` is computed as ``max(existing) + 1`` for the
    (session_id, path) pair, defaulting to 1 for the first version. Runs
    inside the existing async engine so no new connections are needed.
    """
    from sqlalchemy import func, insert, select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import file_history

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                select(func.max(file_history.c.version_number)).where(
                    file_history.c.session_id == session_id,
                    file_history.c.path == path,
                )
            )
            row = result.first()
            next_version = ((row[0] if row is not None else None) or 0) + 1
            await conn.execute(
                insert(file_history).values(
                    session_id=session_id,
                    path=path,
                    content=content,
                    created_by=created_by,
                    version_number=next_version,
                )
            )
    except Exception as exc:
        logger.warning("[file_history] insert failed: %s", exc)


# ── /api/runs — Feature 12: Multi-Run Comparison ────────────────────────────


async def _record_run_summary(
    *,
    session_id: int,
    goal: str,
    agent_count: int,
    task_count: int,
    tasks_done: int,
    tasks_failed: int,
    total_rounds: int,
    llm_calls: int,
    preset_id: str,
    policy_hash: str,
) -> int | None:
    """Insert a row into ``run_summary`` and return the new row id."""
    from datetime import datetime as _dt

    from sqlalchemy import insert

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import run_summary

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                insert(run_summary).values(
                    session_id=session_id,
                    goal=goal,
                    completed_at=_dt.utcnow(),
                    agent_count=agent_count,
                    task_count=task_count,
                    tasks_done=tasks_done,
                    tasks_failed=tasks_failed,
                    total_rounds=total_rounds,
                    llm_calls=llm_calls,
                    preset_id=preset_id,
                    policy_hash=policy_hash,
                )
            )
            return result.lastrowid
    except Exception as exc:
        logger.warning("[run_summary] insert failed: %s", exc)
        return None


# ── /api/session/{id}/checkpoint — Feature 30: Session Resume Foundation ────


async def _upsert_checkpoint(session_id: int, round_number: int, state_json: str) -> None:
    """Insert or replace a checkpoint row for (session_id, round_number).

    Uses a DELETE + INSERT pattern which is portable across SQLite versions
    (SQLite's ``ON CONFLICT DO UPDATE`` requires SQLAlchemy dialect awareness).
    """
    from sqlalchemy import delete, insert

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import session_checkpoint

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                delete(session_checkpoint).where(
                    session_checkpoint.c.session_id == session_id,
                    session_checkpoint.c.round_number == round_number,
                )
            )
            await conn.execute(
                insert(session_checkpoint).values(
                    session_id=session_id,
                    round_number=round_number,
                    state_json=state_json,
                )
            )
    except Exception as exc:
        logger.warning("[checkpoint] upsert failed: %s", exc)
