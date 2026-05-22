"""Viewer Fantasy Draft — DB + scoring helpers (MVP).

Viewers on ``/watch/<code>`` pick a 3-agent roster pre/during a run.
Their score for the session is::

    sum(agent.total_xp_earned for agent in picks)  +  10 * sum(len(agent.stats.achievements) for agent in picks)

The score is computed from *durable* storage so it survives a swarm
crash/restart:

  * Achievements come from ``earned_achievements`` (one row per
    (character_uuid, achievement_id), written by
    :mod:`autonoma.achievements_db`). Scoped to the current run by
    ``project_uuid``.
  * Per-session XP comes from ``character_run_xp``, a durable mirror
    upserted from the swarm's per-round tick (see
    ``AgentSwarm._update_world_stats``). Unlike
    ``characters.total_xp_earned`` (lifetime, flushed only at run-end)
    this row is *per-session* and updates inside the run.

A small fallback path remains: if both durable counts are 0 *and* the
swarm has the agent live with non-zero in-memory stats, we use the live
numbers so an early-session score doesn't render as 0 before the first
persistence tick lands.

The persistence side of *drafts* themselves is unchanged: one
``viewer_drafts`` row per (viewer_id, session_id), upserted on submit.
The score is computed at read time — storing it would go stale the
moment another XP/achievement landed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy import func as sa_func
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import (
    character_run_xp,
    characters,
    earned_achievements,
    project_participants,
    viewer_drafts,
)

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
        mood_str = mood.value if mood is not None and hasattr(mood, "value") else str(mood or "")
        rows.append(
            {
                "name": str(name),
                "emoji": emoji,
                "role": role,
                "mood": mood_str,
            }
        )
    return rows


def _live_swarm_for(session_id: int) -> Any | None:
    """Resolve the live swarm for ``session_id`` (or ``None`` if not loaded).

    Tolerant of the api module being absent (CI / unit tests that
    exercise ``draft.py`` directly without the FastAPI app lifecycle).
    """
    try:
        from autonoma import api as _api
    except ImportError:
        return None
    sess = _api._sessions.get(int(session_id)) if hasattr(_api, "_sessions") else None
    if sess is None:
        return None
    return getattr(sess, "swarm", None)


def _live_project_uuid(swarm: Any) -> str | None:
    """Pull the in-memory swarm's current ``project_uuid``, if any."""
    if swarm is None:
        return None
    registry = getattr(swarm, "registry", None)
    if registry is None:
        return None
    return getattr(registry, "project_uuid", None)


def _live_stats_score(swarm: Any, name: str) -> int:
    """In-memory fallback score for a single pick.

    Mirrors the old all-live computation: ``total_xp_earned + 10 * len(achievements)``.
    Returns ``0`` when the agent isn't present or has no stats.
    """
    if swarm is None:
        return 0
    try:
        agent_map = swarm.agents
    except Exception:
        return 0
    agent = agent_map.get(name) if hasattr(agent_map, "get") else None
    if agent is None:
        return 0
    stats = getattr(agent, "stats", None)
    if stats is None:
        return 0
    try:
        xp = int(getattr(stats, "total_xp_earned", 0) or 0)
    except (TypeError, ValueError):
        xp = 0
    achievements = getattr(stats, "achievements", []) or []
    try:
        ach_count = len(achievements)
    except TypeError:
        ach_count = 0
    return xp + ACHIEVEMENT_WEIGHT * ach_count


async def _resolve_character_uuids(
    session_id: int,
    names: list[str],
    swarm: Any,
    project_uuid: str | None,
) -> dict[str, str]:
    """Resolve pick *names* → ``character_uuid`` for ``session_id``.

    Strategy (first hit wins):

    1. Live swarm: ``swarm.agents[name].character_uuid`` (cheap, exact).
    2. ``project_participants`` joined to ``characters.name`` for the
       active project — handles the post-restart case where the swarm
       is gone but the persisted run still exists.

    Names we can't resolve are simply omitted; the caller treats a
    missing uuid as ``score=0`` (matches the pre-existing
    "unknown agent" semantics).
    """
    resolved: dict[str, str] = {}
    # Live swarm path
    if swarm is not None:
        try:
            agent_map = swarm.agents
        except Exception:
            agent_map = None
        if agent_map is not None:
            for n in names:
                agent = agent_map.get(n) if hasattr(agent_map, "get") else None
                uid = getattr(agent, "character_uuid", "") if agent is not None else ""
                if uid:
                    resolved[n] = str(uid)

    missing = [n for n in names if n not in resolved]
    if not missing or not project_uuid:
        return resolved

    # DB path — characters who participated in this project but whose
    # live agent has gone (swarm crash, mid-run restart, etc.).
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(characters.c.character_uuid, characters.c.name)
                .select_from(
                    project_participants.join(
                        characters,
                        project_participants.c.character_uuid == characters.c.character_uuid,
                    )
                )
                .where(project_participants.c.project_uuid == str(project_uuid))
                .where(characters.c.name.in_(missing))
            )
        ).all()
    for row in rows:
        m = row._mapping if hasattr(row, "_mapping") else row
        name = str(m["name"])
        if name not in resolved:
            resolved[name] = str(m["character_uuid"])
    return resolved


async def upsert_character_run_xp(session_id: int, character_uuid: str, xp: int) -> None:
    """Mirror ``stats.total_xp_earned`` into ``character_run_xp``.

    Idempotent — same (session_id, character_uuid) pair gets updated in
    place. Called from the swarm's per-round world-stats tick, where it
    must never raise: a transient DB error there mustn't take down the
    swarm loop, so callers should wrap this in a try/except.
    """
    if not character_uuid:
        return
    await init_db()
    engine = get_engine()
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        try:
            await conn.execute(
                insert(character_run_xp).values(
                    session_id=int(session_id),
                    character_uuid=str(character_uuid),
                    xp=int(xp or 0),
                    updated_at=now,
                )
            )
        except IntegrityError:
            await conn.execute(
                update(character_run_xp)
                .where(character_run_xp.c.session_id == int(session_id))
                .where(character_run_xp.c.character_uuid == str(character_uuid))
                .values(xp=int(xp or 0), updated_at=now)
            )


async def _score_picks(session_id: int, picks: list[str]) -> int:
    """Compute the durable score for a roster.

    Score is ``xp + ACHIEVEMENT_WEIGHT * achievement_count`` summed
    across the three picks. Both terms come from durable storage:
    ``character_run_xp`` for XP, ``earned_achievements`` for badges.

    Unknown agents (renamed/respawned, no character_uuid yet) score
    zero rather than raising — a fantasy roster shouldn't auto-bust
    because of a midstream cast change.

    Fallback: if BOTH durable counts are 0 for a pick *and* the swarm
    is still live with non-zero in-memory stats, use the live number
    instead. That covers the early-run window before the first
    persistence tick has landed; after the first tick, durable storage
    is the source of truth.
    """
    swarm = _live_swarm_for(session_id)
    project_uuid = _live_project_uuid(swarm)

    name_to_uuid = await _resolve_character_uuids(
        session_id=session_id,
        names=list(picks),
        swarm=swarm,
        project_uuid=project_uuid,
    )
    if not name_to_uuid:
        # No durable handle on any pick. Fall back fully to live stats
        # — this is the very-start-of-run path where the registry has
        # not yet hydrated character_uuids onto the agents.
        return sum(_live_stats_score(swarm, n) for n in picks)

    uuids = list({uid for uid in name_to_uuid.values() if uid})
    await init_db()
    engine = get_engine()
    xp_by_uuid: dict[str, int] = {}
    ach_by_uuid: dict[str, int] = {}
    async with engine.connect() as conn:
        if uuids:
            xp_rows = (
                await conn.execute(
                    select(character_run_xp.c.character_uuid, character_run_xp.c.xp)
                    .where(character_run_xp.c.session_id == int(session_id))
                    .where(character_run_xp.c.character_uuid.in_(uuids))
                )
            ).all()
            for row in xp_rows:
                m = row._mapping if hasattr(row, "_mapping") else row
                xp_by_uuid[str(m["character_uuid"])] = int(m["xp"] or 0)

            ach_q = (
                select(
                    earned_achievements.c.character_uuid,
                    sa_func.count(earned_achievements.c.id).label("n"),
                )
                .where(earned_achievements.c.character_uuid.in_(uuids))
                .group_by(earned_achievements.c.character_uuid)
            )
            # Scope achievements to this run when we know its project_uuid;
            # otherwise (post-restart with no live swarm) we'd over-count
            # by including badges from earlier projects. The fallback is
            # acceptable because viewer_drafts are session-scoped — the
            # post-restart scoreboard recreates a new session anyway.
            if project_uuid:
                ach_q = ach_q.where(earned_achievements.c.project_uuid == str(project_uuid))
            ach_rows = (await conn.execute(ach_q)).all()
            for row in ach_rows:
                m = row._mapping if hasattr(row, "_mapping") else row
                ach_by_uuid[str(m["character_uuid"])] = int(m["n"] or 0)

    score = 0
    for name in picks:
        uid = name_to_uuid.get(name)
        if not uid:
            # Pick still maps to an unknown character — score 0, but
            # let the live fallback below upgrade it if we have stats.
            live = _live_stats_score(swarm, name)
            score += live
            continue
        xp = xp_by_uuid.get(uid, 0)
        ach = ach_by_uuid.get(uid, 0)
        per_pick = xp + ACHIEVEMENT_WEIGHT * ach
        if per_pick == 0:
            # Early-run fallback: durable mirror hasn't ticked yet but
            # the in-memory swarm already has counters worth showing.
            per_pick = _live_stats_score(swarm, name)
        score += per_pick
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
        score = await _score_picks(session_id, d.picks)
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
        {"viewer_name": r["viewer_name"], "picks": r["picks"], "score": r["score"]} for r in rows
    ]
    return {"rows": public_rows, "my_rank": my_rank}
