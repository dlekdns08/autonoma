"""Cross-session character leaderboard — FastAPI router.

Endpoint
────────
::

    GET /api/leaderboard/characters?metric=xp|runs_survived|achievements&limit=50

Returns the top ``limit`` characters across all sessions sorted by the
chosen metric. The ``characters`` table is the source of truth for the
``xp`` and ``runs_survived`` metrics; the ``achievement_count`` metric
joins ``earned_achievements`` and counts per ``character_uuid``.

Response shape::

    {
      "metric": "...",
      "count": N,
      "rows": [
        {
          "uuid": "...",
          "name": "...",
          "species_emoji": "...",
          "role": "...",
          "level": int,
          "xp": int,
          "runs_survived": int,
          "achievement_count": int,
        },
        ...
      ]
    }

Routing-only logic lives here; the persistence layer is the existing
``characters`` / ``earned_achievements`` tables — no new tables are added.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select

from autonoma.auth import User, require_active_user
from autonoma.db.engine import get_engine
from autonoma.db.schema import characters, earned_achievements

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leaderboard"])


Metric = Literal["xp", "runs_survived", "achievements"]


@router.get("/api/leaderboard/characters")
async def leaderboard_characters(
    metric: Metric = Query(
        "xp",
        description="Sort key: xp | runs_survived | achievements",
    ),
    limit: int = Query(50, ge=1, le=200),
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the top ``limit`` characters sorted by ``metric``.

    Achievement counts are computed via a LEFT OUTER JOIN + GROUP BY so
    characters with zero badges still appear (their count is just 0).
    Ties are broken by ``last_seen_at`` desc so freshly active
    characters surface above ancient idols with identical numbers.
    """
    engine = get_engine()

    # Sub-aggregate achievement counts per character so we can sort and
    # surface the value on every row regardless of the chosen metric.
    ach_count = (
        select(
            earned_achievements.c.character_uuid.label("uuid"),
            func.count().label("achievement_count"),
        )
        .group_by(earned_achievements.c.character_uuid)
        .subquery()
    )

    ach_count_expr = func.coalesce(ach_count.c.achievement_count, 0)

    if metric == "runs_survived":
        order_col = desc(characters.c.runs_survived)
    elif metric == "achievements":
        order_col = desc(ach_count_expr)
    else:
        # Default: xp. Use total_xp_earned (lifetime) for the cross-
        # session ranking — that's the metric viewers see on the
        # character profile.
        order_col = desc(characters.c.total_xp_earned)

    stmt = (
        select(
            characters.c.character_uuid,
            characters.c.name,
            characters.c.species_emoji,
            characters.c.role,
            characters.c.level,
            characters.c.total_xp_earned,
            characters.c.runs_survived,
            ach_count_expr.label("achievement_count"),
        )
        .select_from(
            characters.outerjoin(
                ach_count,
                characters.c.character_uuid == ach_count.c.uuid,
            )
        )
        .order_by(order_col, desc(characters.c.last_seen_at))
        .limit(limit)
    )

    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        raw_rows = result.all()

    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        m = r._mapping
        rows.append(
            {
                "uuid": m["character_uuid"],
                "name": m["name"],
                "species_emoji": m["species_emoji"],
                "role": m["role"],
                "level": int(m["level"] or 0),
                "xp": int(m["total_xp_earned"] or 0),
                "runs_survived": int(m["runs_survived"] or 0),
                "achievement_count": int(m["achievement_count"] or 0),
            }
        )

    return {
        "metric": metric,
        "count": len(rows),
        "rows": rows,
    }
