"""Retirement / ghost lifecycle (feature #2).

Lifecycle
─────────
Characters in the persistent registry currently accumulate runs and XP
forever. After the swarm finishes processing a character that has
survived many runs at a respectable level, we want to retire it
*honourably*: the row stays in ``characters`` (with ``retired_at`` set,
``is_alive=0``) so its memoir, journal, achievements, etc. are still
inspectable, but the registry will treat it as out-of-rotation when
spawning new agents.

Retired characters can re-appear as ghosts — short cameo / advice /
dream entries that the narrator can fold into a future round. Each
appearance is appended to the ``ghost_appearances`` table so we can
draw a "haunting timeline" later.

Wiring
──────
This module is *pure logic + DB I/O only*. It deliberately does not
import ``swarm.py`` / ``api.py`` / ``registry.py`` — Wave 2 hooks the
retirement check into the swarm's per-run wrap-up and the ghost summon
into the round narrator. Until then, the functions here are callable
directly (e.g. from a CLI command or a test harness) and emit a
``character.retired`` event on the bus when they fire.

RNG discipline
──────────────
``summon_ghost_for_round`` accepts a ``random.Random`` instance and
never reaches for module-global ``random``. This keeps swarm
determinism intact — a run with a seeded RNG must produce the same
ghost cameo on replay.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import desc, insert, select, update

from autonoma.config import settings
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import characters, ghost_appearances
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# Probability per round that a ghost cameo fires. Kept as a module-level
# constant (rather than a setting) because it's a narrative cadence
# choice, not an operator knob. Tweak here if cameos feel too frequent.
GHOST_SUMMON_PROBABILITY = 0.05

# Allowed ``ghost_appearances.kind`` values. Mirrors the comment on the
# schema column. Validated at insert time so a typo can't slip through
# and break the dashboard's "filter by kind" query.
GHOST_KINDS: frozenset[str] = frozenset({"dream", "advice", "cameo"})

# Weight per rarity tier when summoning a ghost. Legendaries drown out
# commons heavily — they're the characters players remember.
_RARITY_WEIGHTS: dict[str, float] = {
    "legendary": 8.0,
    "epic": 4.0,
    "rare": 2.0,
    "uncommon": 1.5,
    "common": 1.0,
}


@dataclass
class GhostRecord:
    """A retired character ready to be summoned as a ghost.

    ``memoir_text`` is the latest compacted memoir (feature #3) which
    the narrator can quote / paraphrase. ``kind`` reflects the *latest*
    appearance kind for this character if one exists, else ``"dream"``
    by convention so callers always have something to render.
    """

    character_uuid: str
    name: str
    role: str
    species_emoji: str
    memoir_text: str
    kind: str
    last_seen_at: datetime | None


# ── Eligibility ────────────────────────────────────────────────────────


def is_retirement_eligible(runs_survived: int, level: int) -> bool:
    """Pure check against the configured thresholds.

    Reads ``retirement_enabled``, ``retirement_min_runs`` and
    ``retirement_min_level`` from ``settings`` at call time (not at
    import time) so a monkeypatched setting in a test takes effect
    without re-importing the module.
    """
    if not settings.retirement_enabled:
        return False
    if runs_survived < settings.retirement_min_runs:
        return False
    if level < settings.retirement_min_level:
        return False
    return True


# ── Mutators ───────────────────────────────────────────────────────────


async def retire_character(
    character_uuid: str,
    project_uuid: str | None = None,
) -> bool:
    """Mark ``character_uuid`` retired; returns True on success.

    Idempotent — calling on an already-retired character is a no-op
    that returns ``False`` (no row was updated).

    Emits ``character.retired`` on the bus with
    ``{character_uuid, project_uuid, retired_at}`` so subscribers
    (narrator, frontend, observability) can react.
    """
    await init_db()
    engine = get_engine()
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        # Only flip the flag if it's still NULL — protects against
        # double-retirement bumping the timestamp on every call.
        result = await conn.execute(
            update(characters)
            .where(characters.c.character_uuid == character_uuid)
            .where(characters.c.retired_at.is_(None))
            .values(retired_at=now, is_alive=0)
        )
    if result.rowcount == 0:
        logger.info(
            "retire_character: %s already retired or missing — skipping",
            character_uuid,
        )
        return False

    await bus.emit(
        "character.retired",
        character_uuid=character_uuid,
        project_uuid=project_uuid,
        retired_at=now.isoformat(),
    )
    logger.info("Character %s retired at %s", character_uuid, now.isoformat())
    return True


async def record_ghost_appearance(
    character_uuid: str,
    kind: str,
    round_number: int,
    text: str,
    witnessed_by: str = "",
    project_uuid: str | None = None,
) -> int:
    """Append a row to ``ghost_appearances``; returns the new row id.

    ``kind`` is validated against :data:`GHOST_KINDS`. An invalid kind
    raises ``ValueError`` rather than silently storing junk that
    breaks the per-kind filter on the haunting timeline view.
    """
    if kind not in GHOST_KINDS:
        raise ValueError(f"ghost appearance kind {kind!r} not in {sorted(GHOST_KINDS)}")
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            insert(ghost_appearances).values(
                character_uuid=character_uuid,
                project_uuid=project_uuid,
                kind=kind,
                round_number=round_number,
                witnessed_by=witnessed_by[:64],
                text=text,
            )
        )
    # ``inserted_primary_key`` is a tuple; first element is the id.
    new_id = result.inserted_primary_key[0] if result.inserted_primary_key else 0
    return int(new_id)


# ── Read helpers ───────────────────────────────────────────────────────


async def list_active_ghosts(
    owner_user_id: str | None = None,
    limit: int = 20,
) -> list[GhostRecord]:
    """List retired characters, newest retirement first.

    The result is suitable for the future "ghosts" panel in the UI —
    one row per retired character, with their latest appearance kind
    folded in (``dream`` if they've never been summoned yet so the UI
    has *something* to render).

    ``owner_user_id`` is accepted for forward compatibility with a
    per-user ghost roster, but ``characters`` does not currently carry
    an owner column. When supplied today it is ignored; a deprecation
    warning fires once at the call site so we notice during wiring.
    """
    if owner_user_id:
        # No owner column on ``characters`` yet — log so the upstream
        # caller realises the filter is a no-op until we add one.
        logger.debug(
            "list_active_ghosts: owner_user_id=%s passed but characters "
            "table has no owner column; returning unfiltered",
            owner_user_id,
        )

    await init_db()
    engine = get_engine()
    # 1) Pull retired characters, newest first.
    async with engine.connect() as conn:
        char_rows = (
            (
                await conn.execute(
                    select(
                        characters.c.character_uuid,
                        characters.c.name,
                        characters.c.role,
                        characters.c.species_emoji,
                        characters.c.memoir_text,
                        characters.c.retired_at,
                        characters.c.last_seen_at,
                    )
                    .where(characters.c.retired_at.is_not(None))
                    .order_by(desc(characters.c.retired_at))
                    .limit(max(1, int(limit)))
                )
            )
            .mappings()
            .all()
        )

        if not char_rows:
            return []

        # 2) For each retired character, find the *latest* appearance
        #    kind so the panel can show "last seen as: advice".
        #    A single query keyed by character_uuid is cheaper than a
        #    per-row roundtrip; SQLite's ``IN`` clause caps at ~1000
        #    elements which we'll never exceed at limit=20.
        uuids = [r["character_uuid"] for r in char_rows]
        appearance_rows = (
            (
                await conn.execute(
                    select(
                        ghost_appearances.c.character_uuid,
                        ghost_appearances.c.kind,
                        ghost_appearances.c.created_at,
                    )
                    .where(ghost_appearances.c.character_uuid.in_(uuids))
                    # Tie-break on id so multiple rows inserted within the
                    # same SQLite-second still resolve newest-first.
                    .order_by(
                        desc(ghost_appearances.c.created_at),
                        desc(ghost_appearances.c.id),
                    )
                )
            )
            .mappings()
            .all()
        )

    # Pick latest kind per character_uuid (rows are already newest-first).
    latest_kind: dict[str, str] = {}
    for ar in appearance_rows:
        cu = ar["character_uuid"]
        if cu not in latest_kind:
            latest_kind[cu] = str(ar["kind"]) if ar["kind"] in GHOST_KINDS else "dream"

    out: list[GhostRecord] = []
    for r in char_rows:
        out.append(
            GhostRecord(
                character_uuid=r["character_uuid"],
                name=r["name"],
                role=r["role"],
                species_emoji=r["species_emoji"],
                memoir_text=r["memoir_text"] or "",
                kind=latest_kind.get(r["character_uuid"], "dream"),
                last_seen_at=r["retired_at"] or r["last_seen_at"],
            )
        )
    return out


# ── Random summon ─────────────────────────────────────────────────────


async def summon_ghost_for_round(
    round_number: int,
    current_agents: list[str],
    rng: random.Random,
) -> GhostRecord | None:
    """Maybe pick a retired character to cameo this round.

    Returns ``None`` ~95% of the time so the swarm doesn't get
    constantly haunted; on the rare hit, returns a :class:`GhostRecord`
    weighted by rarity (legendaries dominate). Excludes any retired
    character whose name is currently active in ``current_agents`` —
    a ghost shouldn't appear next to its own living self.

    All randomness goes through ``rng`` so the same seed reproduces
    the same haunting schedule.
    """
    if not settings.retirement_enabled:
        return None

    if rng.random() >= GHOST_SUMMON_PROBABILITY:
        return None

    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(
                        characters.c.character_uuid,
                        characters.c.name,
                        characters.c.role,
                        characters.c.species_emoji,
                        characters.c.memoir_text,
                        characters.c.rarity,
                        characters.c.retired_at,
                        characters.c.last_seen_at,
                    ).where(characters.c.retired_at.is_not(None))
                )
            )
            .mappings()
            .all()
        )

    # Filter out anyone whose name is currently in play.
    active_names = set(current_agents or [])
    candidates = [r for r in rows if r["name"] not in active_names]
    if not candidates:
        return None

    weights = [_RARITY_WEIGHTS.get(str(r["rarity"]).lower(), 1.0) for r in candidates]
    # ``random.Random.choices`` is the deterministic-friendly weighted
    # picker (uses ``self.random`` internally).
    chosen = rng.choices(candidates, weights=weights, k=1)[0]

    # Pick a kind for this cameo. Skewed slightly toward "advice"
    # because that's the most narratively useful flavor for an active
    # round, with "dream" as the next most common quiet flourish.
    kind = rng.choices(
        ["advice", "dream", "cameo"],
        weights=[3.0, 2.0, 1.0],
        k=1,
    )[0]

    logger.info(
        "Ghost summoned for round %s: %s (%s, kind=%s)",
        round_number,
        chosen["name"],
        chosen["rarity"],
        kind,
    )

    return GhostRecord(
        character_uuid=chosen["character_uuid"],
        name=chosen["name"],
        role=chosen["role"],
        species_emoji=chosen["species_emoji"],
        memoir_text=chosen["memoir_text"] or "",
        kind=kind,
        last_seen_at=chosen["retired_at"] or chosen["last_seen_at"],
    )
