"""Viewer Fantasy Draft — DB + scoring helpers (MVP).

Viewers on ``/watch/<code>`` pick a 3-agent roster pre/during a run.
Their score for the session is::

    sum(agent.total_xp_earned for agent in picks)  +  10 * sum(len(agent.stats.achievements) for agent in picks)

We compute it live from the swarm's in-memory ``AgentStats`` because:
  * XP and achievements aren't currently emitted with ``session_id`` on
    the bus, so building a durable event-log scan would require a
    separate ingest path; and
  * the swarm carries authoritative counters anyway — the router can
    snapshot them per request and the answer is correct within one
    polling tick.

The persistence side is small: a single ``viewer_drafts`` row per
(viewer_id, session_id), upserted on submit. The score is *not*
stored — it would go stale the moment another XP/achievement landed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import viewer_drafts

logger = logging.getLogger(__name__)


# Hard cap — the draft is a 3-agent fantasy roster. Keep this as a
# module constant so the router can reuse it for validation without a
# magic number drifting between layers.
PICK_COUNT: int = 3

# Score multiplier on achievements. The MVP weights an achievement
# roughly equal to ~10 XP-points of work (a level-up is 50 XP; an
# achievement is rarer + flashier so 10pts is a fair thumb).
ACHIEVEMENT_WEIGHT: int = 10


# ── Errors ────────────────────────────────────────────────────────────


class DraftError(ValueError):
    code: str = "draft_error"


class InvalidPicks(DraftError):
    code = "invalid_picks"


class UnknownAgent(DraftError):
    code = "unknown_agent"


# ── Public dataclass ──────────────────────────────────────────────────


@dataclass
class DraftRow:
    """Plain-data view of a ``viewer_drafts`` row."""

    viewer_id: str
    session_id: int
    picks: list[str]
    viewer_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "viewer_id": self.viewer_id,
            "session_id": self.session_id,
            "picks": list(self.picks),
            "viewer_name": self.viewer_name,
        }


def _row_to_draft(row: Any) -> DraftRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    raw = m["picks_json"] or "[]"
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = []
    picks = [str(x) for x in decoded if isinstance(x, str)]
    return DraftRow(
        viewer_id=str(m["viewer_id"]),
        session_id=int(m["session_id"]),
        picks=picks,
        viewer_name=str(m["viewer_name"] or ""),
    )


# ── Validation ────────────────────────────────────────────────────────


def normalize_picks(raw: Any) -> list[str]:
    """Validate and normalise a picks payload.

    The wire shape is ``{"picks": [name, name, name]}`` — exactly three
    distinct non-empty strings. Anything else raises :class:`InvalidPicks`.
    Defensive about list-ish containers (tuples are accepted) but we
    deliberately do *not* coerce non-strings: the router has already
    parsed the body via Pydantic, but other call sites (CLI, tests) may
    hand us raw dicts.
    """
    if not isinstance(raw, (list, tuple)):
        raise InvalidPicks(f"picks must be a list of {PICK_COUNT} agent names")
    picks: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise InvalidPicks("picks must be agent names (strings)")
        cleaned = entry.strip()
        if not cleaned:
            raise InvalidPicks("agent names must not be empty")
        picks.append(cleaned)
    if len(picks) != PICK_COUNT:
        raise InvalidPicks(f"exactly {PICK_COUNT} picks required (got {len(picks)})")
    if len(set(picks)) != PICK_COUNT:
        raise InvalidPicks("picks must be distinct agents")
    return picks


# ── Swarm bridge ──────────────────────────────────────────────────────


def list_session_agents(session_id: int) -> list[dict[str, str]]:
    """Snapshot the agents currently live in ``session_id``.

    Returns ``[{name, emoji, role, mood}, …]``. When the session has no
    swarm yet (start-of-run race) or the registry is unavailable
    (CI/tests) we fall back to an empty list — the router maps that to
    an empty response rather than 404 so the modal can render a
    "waiting for agents" state.
    """
    rows: list[dict[str, str]] = []
    try:
        from autonoma import api as _api
    except ImportError:
        return rows

    sess = _api._sessions.get(int(session_id)) if hasattr(_api, "_sessions") else None
    if sess is None:
        return rows
    swarm = getattr(sess, "swarm", None)
    if swarm is None:
        return rows
    try:
        items = list(swarm.agents.items())
    except Exception:
        return rows
    for name, agent in items:
        if str(name) == "Director":
            # Director is a coordinator, not a draftable hero — hide it
            # from the picker so viewers don't waste a roster slot.
            continue
        persona = getattr(agent, "persona", None)
        emoji = str(getattr(persona, "emoji", "") or "")
        role = str(getattr(persona, "role", "") or "")
        mood = getattr(agent, "mood", None)
        mood_str = (
            mood.value if mood is not None and hasattr(mood, "value") else str(mood or "")
        )
        rows.append(
            {
                "name": str(name),
                "emoji": emoji,
                "role": role,
                "mood": mood_str,
            }
        )
    return rows


def _score_picks(session_id: int, picks: list[str]) -> int:
    """Compute the live score for a roster.

    Score is ``sum(stats.total_xp_earned) + ACHIEVEMENT_WEIGHT * sum(len(stats.achievements))``
    across the three agents. Unknown agents (renamed/respawned) score
    zero rather than raising — a fantasy roster shouldn't auto-bust
    because of a midstream cast change.
    """
    try:
        from autonoma import api as _api
    except ImportError:
        return 0
    sess = _api._sessions.get(int(session_id)) if hasattr(_api, "_sessions") else None
    if sess is None:
        return 0
    swarm = getattr(sess, "swarm", None)
    if swarm is None:
        return 0
    score = 0
    try:
        agent_map = swarm.agents
    except Exception:
        return 0
    for name in picks:
        agent = agent_map.get(name) if hasattr(agent_map, "get") else None
        if agent is None:
            continue
        stats = getattr(agent, "stats", None)
        if stats is None:
            continue
        # total_xp_earned is a property that sums every level threshold
        # plus the current XP — i.e. cumulative XP for the run. That's
        # what "XP gained this session" means in MVP terms.
        try:
            xp = int(getattr(stats, "total_xp_earned", 0) or 0)
        except (TypeError, ValueError):
            xp = 0
        achievements = getattr(stats, "achievements", []) or []
        try:
            ach_count = len(achievements)
        except TypeError:
            ach_count = 0
        score += xp + ACHIEVEMENT_WEIGHT * ach_count
    return score


# ── DB ops ────────────────────────────────────────────────────────────


async def upsert_draft(
    viewer_id: str,
    session_id: int,
    picks: list[str],
    viewer_name: str = "",
) -> DraftRow:
    """Insert or replace the viewer's draft for this session.

    Validation is the caller's responsibility — ``picks`` must already
    be the normalised 3-distinct-string list. We persist as JSON to
    avoid a side table for what is functionally a fixed-arity tuple.
    """
    await init_db()
    engine = get_engine()
    payload = json.dumps(list(picks))
    async with engine.begin() as conn:
        try:
            await conn.execute(
                insert(viewer_drafts).values(
                    viewer_id=str(viewer_id),
                    session_id=int(session_id),
                    picks_json=payload,
                    viewer_name=str(viewer_name or "")[:64],
                )
            )
        except IntegrityError:
            # Re-submit path: same (viewer_id, session_id) row exists.
            # Update in place so the roster reflects the latest pick;
            # ``updated_at`` is touched by the explicit value below.
            await conn.execute(
                update(viewer_drafts)
                .where(viewer_drafts.c.viewer_id == str(viewer_id))
                .where(viewer_drafts.c.session_id == int(session_id))
                .values(picks_json=payload, viewer_name=str(viewer_name or "")[:64])
            )
    return DraftRow(
        viewer_id=str(viewer_id),
        session_id=int(session_id),
        picks=list(picks),
        viewer_name=str(viewer_name or ""),
    )


async def get_draft(viewer_id: str, session_id: int) -> DraftRow | None:
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(viewer_drafts)
                .where(viewer_drafts.c.viewer_id == str(viewer_id))
                .where(viewer_drafts.c.session_id == int(session_id))
            )
        ).first()
    return _row_to_draft(row) if row else None


async def list_drafts_for_session(session_id: int) -> list[DraftRow]:
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(viewer_drafts).where(viewer_drafts.c.session_id == int(session_id))
            )
        ).all()
    return [_row_to_draft(r) for r in rows]


async def clear_session_drafts(session_id: int) -> int:
    """Delete every draft row for ``session_id`` (used by tests / teardown).

    Returns the count of removed rows. Not wired into a router — kept
    as a helper because the MVP doesn't expose a "reset" admin action
    yet; rooms ending naturally take care of themselves because the
    score function returns 0 once the swarm is gone.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(viewer_drafts).where(viewer_drafts.c.session_id == int(session_id))
        )
    return int(result.rowcount or 0)


# ── Scoreboard composition ────────────────────────────────────────────


async def scoreboard(session_id: int, my_viewer_id: str | None = None) -> dict[str, Any]:
    """Build the live scoreboard rows for ``session_id``.

    Each row is ``{viewer_name, picks, score}``; rows are sorted by
    ``score`` descending with ``viewer_name`` as a stable tiebreaker so
    a sticky UI doesn't flicker between equally-scored viewers.
    ``my_rank`` is 1-indexed (top viewer is rank 1); ``None`` if the
    caller hasn't submitted a draft yet.
    """
    drafts = await list_drafts_for_session(session_id)
    rows: list[dict[str, Any]] = []
    my_rank: int | None = None
    for d in drafts:
        score = _score_picks(session_id, d.picks)
        rows.append(
            {
                "viewer_id": d.viewer_id,
                "viewer_name": d.viewer_name or d.viewer_id[:8],
                "picks": list(d.picks),
                "score": int(score),
            }
        )
    rows.sort(key=lambda r: (-int(r["score"]), str(r["viewer_name"])))
    if my_viewer_id:
        for idx, row in enumerate(rows):
            if row["viewer_id"] == my_viewer_id:
                my_rank = idx + 1
                break
    # Strip the viewer_id from the wire payload — it's only used for
    # rank lookup. Other viewers never need to see another viewer's
    # stable id.
    public_rows = [
        {"viewer_name": r["viewer_name"], "picks": r["picks"], "score": r["score"]}
        for r in rows
    ]
    return {"rows": public_rows, "my_rank": my_rank}
