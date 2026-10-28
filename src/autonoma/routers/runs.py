"""Run history + session checkpoint / resume / replay routes.

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from autonoma.auth import (
    require_admin,
)
from autonoma.db.users import (
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/runs")
async def list_runs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Paginated list of run summaries. Admin-only."""
    from sqlalchemy import func, select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import run_summary

    engine = get_engine()
    async with engine.connect() as conn:
        total_result = await conn.execute(select(func.count()).select_from(run_summary))
        total = total_result.scalar() or 0

        rows_result = await conn.execute(
            select(run_summary).order_by(run_summary.c.id.desc()).limit(limit).offset(offset)
        )
        rows = rows_result.fetchall()

    items = [
        {
            "id": r.id,
            "session_id": r.session_id,
            "goal": r.goal,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "agent_count": r.agent_count,
            "task_count": r.task_count,
            "tasks_done": r.tasks_done,
            "tasks_failed": r.tasks_failed,
            "total_rounds": r.total_rounds,
            "llm_calls": r.llm_calls,
            "preset_id": r.preset_id,
            "policy_hash": r.policy_hash,
        }
        for r in rows
    ]
    return {"total": total, "limit": limit, "offset": offset, "runs": items}


def _run_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "goal": row.goal,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "agent_count": row.agent_count,
        "task_count": row.task_count,
        "tasks_done": row.tasks_done,
        "tasks_failed": row.tasks_failed,
        "total_rounds": row.total_rounds,
        "llm_calls": row.llm_calls,
        "preset_id": row.preset_id,
        "policy_hash": row.policy_hash,
    }


@router.get("/api/runs/{run_id}/compare")
async def compare_runs(
    run_id: int,
    with_: int = Query(alias="with"),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Return a side-by-side comparison of two runs with numeric deltas.
    Admin-only.
    """
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import run_summary

    engine = get_engine()
    async with engine.connect() as conn:
        result_a = await conn.execute(select(run_summary).where(run_summary.c.id == run_id))
        result_b = await conn.execute(select(run_summary).where(run_summary.c.id == with_))
        row_a = result_a.first()
        row_b = result_b.first()

    if row_a is None:
        raise HTTPException(status_code=404, detail=f"run_not_found: {run_id}")
    if row_b is None:
        raise HTTPException(status_code=404, detail=f"run_not_found: {with_}")

    dict_a = _run_row_to_dict(row_a)
    dict_b = _run_row_to_dict(row_b)

    _numeric_keys = (
        "agent_count",
        "task_count",
        "tasks_done",
        "tasks_failed",
        "total_rounds",
        "llm_calls",
    )
    delta = {
        k: dict_b[k] - dict_a[k]
        for k in _numeric_keys
        if isinstance(dict_a.get(k), int) and isinstance(dict_b.get(k), int)
    }

    return {"run_a": dict_a, "run_b": dict_b, "delta": delta}


@router.get("/api/session/{session_id}/checkpoint")
async def get_session_checkpoint(
    session_id: int,
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Return the latest checkpoint metadata for a session. Admin-only.

    Does NOT return ``state_json`` in the response to avoid transmitting
    potentially large blobs. Use the POST resume endpoint to load the state.
    """
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import session_checkpoint

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            select(
                session_checkpoint.c.id,
                session_checkpoint.c.session_id,
                session_checkpoint.c.round_number,
                session_checkpoint.c.created_at,
            )
            .where(session_checkpoint.c.session_id == session_id)
            .order_by(session_checkpoint.c.round_number.desc())
            .limit(1)
        )
        row = result.first()

    if row is None:
        raise HTTPException(status_code=404, detail="no_checkpoint_for_session")

    return {
        "checkpoint_id": row.id,
        "session_id": row.session_id,
        "round_number": row.round_number,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/api/session/{session_id}/resume")
async def resume_session(
    session_id: int,
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Load the latest checkpoint for ``session_id`` and return its state.

    Full swarm resumption from a checkpoint is not yet implemented
    (``AgentSwarm`` does not expose ``start_from_checkpoint()``). This
    endpoint returns the deserialized checkpoint data so the frontend can
    display it and prompt the user. Resumption itself requires a future
    ``AgentSwarm.start_from_checkpoint()`` hook — tracked separately.
    """
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import session_checkpoint
    from autonoma.models import ProjectState

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            select(session_checkpoint)
            .where(session_checkpoint.c.session_id == session_id)
            .order_by(session_checkpoint.c.round_number.desc())
            .limit(1)
        )
        row = result.first()

    if row is None:
        raise HTTPException(status_code=404, detail="no_checkpoint_for_session")

    try:
        state = ProjectState.from_json(row.state_json)
    except (ValueError, Exception) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"checkpoint_corrupt: {exc}",
        )

    return {
        "checkpoint_id": row.id,
        "session_id": row.session_id,
        "round_number": row.round_number,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "state": state.model_dump(mode="json"),
        "resume_supported": False,
        "resume_note": (
            "Full resume not yet implemented — "
            "AgentSwarm.start_from_checkpoint() is pending. "
            "Use this payload to inspect or re-display the checkpoint state."
        ),
    }


@router.get("/api/session/{session_id}/replay")
async def replay_session(
    session_id: int,
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 1-#1 — return every per-round checkpoint for a session.

    The frontend ``ReplayPlayer`` UI walks this payload as a timeline:
    each entry is a self-contained ``ProjectState`` snapshot at the end
    of a round, so scrubbing back to round N is a single state swap.

    Heavy by design — scaling concerns are deliberately punted: the
    average run produces ~tens of checkpoints, each ~tens of KB. If a
    run grows past that, paginate by ``round_number`` rather than
    streaming partial states.
    """
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import session_checkpoint
    from autonoma.models import ProjectState

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            select(session_checkpoint)
            .where(session_checkpoint.c.session_id == session_id)
            .order_by(session_checkpoint.c.round_number.asc())
        )
        rows = result.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="no_checkpoints_for_session")

    frames: list[dict[str, Any]] = []
    for row in rows:
        try:
            state = ProjectState.from_json(row.state_json)
            frames.append(
                {
                    "round_number": row.round_number,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "state": state.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            # Skip a corrupt checkpoint rather than failing the whole
            # replay — a single broken row shouldn't blackhole an
            # otherwise viewable run.
            logger.warning(
                "[replay] dropped corrupt checkpoint round=%s: %s",
                row.round_number,
                exc,
            )

    if not frames:
        raise HTTPException(status_code=422, detail="all_checkpoints_corrupt")

    return {
        "session_id": session_id,
        "frame_count": len(frames),
        "first_round": frames[0]["round_number"],
        "last_round": frames[-1]["round_number"],
        "frames": frames,
    }
