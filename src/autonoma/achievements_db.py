"""Persistent achievements / badges (Feature #12).

The catalog (``ACHIEVEMENTS``) and the in-memory tier checker
(``check_achievements``) live in :mod:`autonoma.world` already; this
module is the persistence + pub/sub layer that makes a badge actually
"earned" across runs.

Schema lives in :mod:`autonoma.db.schema` as ``earned_achievements``
(migration 012). The ``UniqueConstraint("character_uuid",
"achievement_id")`` is what gives :func:`record_achievement` its
"award once, ever" semantics — we ``INSERT`` and let the unique
violation tell us the badge was already on file.

Public surface
──────────────
- :class:`EarnedAchievement` — dataclass returned to API callers.
- :func:`record_achievement` — single-id award; returns ``True`` only
  when the row was newly created (so callers can fan out a one-time
  "shiny new badge" cutscene/SFX).
- :func:`batch_record` — convenience for the swarm post-round hook
  that calls :func:`autonoma.world.check_achievements`. Returns the
  ids that were *genuinely* new this call (i.e. survived the unique
  constraint).
- :func:`list_achievements` — per-character timeline, newest first.
- :func:`list_recent_globally` — global ticker for the OBS HUD; small
  join with ``characters`` to render names without a second roundtrip.

On a successful insert we emit ``character.achievement_earned`` on the
shared :data:`autonoma.event_bus.bus` so the highlight recorder, the
TTS commentator, and the OBS overlay can all react without coupling
to this module directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, insert, select
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import characters, earned_achievements
from autonoma.event_bus import bus
from autonoma.world import ACHIEVEMENTS

logger = logging.getLogger(__name__)


@dataclass
class EarnedAchievement:
    """A persisted badge row, decorated for the API layer."""

    achievement_id: str
    tier: str
    earned_at: datetime
    project_uuid: str | None


def _resolve_tier(achievement_id: str, fallback: str = "") -> str:
    """Look up the tier string for ``achievement_id`` in the catalog.

    Falls back to ``fallback`` when the id is unknown (e.g. a legacy
    badge whose catalog entry has since been pruned). Tier values in
    the catalog are :class:`autonoma.world.AchievementTier` enums; we
    coerce to their plain ``str`` value here so callers don't have to
    care about the enum at all.
    """
    spec = ACHIEVEMENTS.get(achievement_id)
    if not spec:
        return fallback
    tier = spec.get("tier", fallback)
    # AchievementTier is a str-Enum; both ``.value`` and ``str(tier)``
    # would work but we want the bare value to keep API payloads tidy.
    return getattr(tier, "value", tier) or fallback


async def record_achievement(
    character_uuid: str,
    achievement_id: str,
    tier: str = "",
    project_uuid: str | None = None,
) -> bool:
    """Persist a single achievement award.

    Returns
    -------
    bool
        ``True`` when the row was newly inserted; ``False`` when the
        unique constraint rejected the insert because the character
        already holds this badge.

    Side effects
    ------------
    Emits ``character.achievement_earned`` on the bus on first-time
    awards. Re-awards are silent — the bus is for "novel events", not
    "we double-checked the database".
    """
    if not achievement_id:
        return False

    resolved_tier = tier or _resolve_tier(achievement_id)

    await init_db()
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                insert(earned_achievements).values(
                    character_uuid=character_uuid,
                    project_uuid=project_uuid,
                    achievement_id=achievement_id,
                    tier=resolved_tier,
                )
            )
    except IntegrityError:
        # Unique constraint hit ⇒ already earned. This is the
        # idempotent-by-design path; not a bug, not worth logging.
        return False

    await bus.emit(
        "character.achievement_earned",
        character_uuid=character_uuid,
        achievement_id=achievement_id,
        tier=resolved_tier,
        project_uuid=project_uuid,
    )
    return True


async def list_achievements(character_uuid: str) -> list[EarnedAchievement]:
    """Return every badge ``character_uuid`` holds, newest first.

    Sort order matches the public profile page: most-recent earn at
    the top so a character page leads with what they just unlocked.
    """
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(earned_achievements)
                .where(earned_achievements.c.character_uuid == character_uuid)
                .order_by(desc(earned_achievements.c.earned_at))
            )
        ).mappings().all()
    return [
        EarnedAchievement(
            achievement_id=r["achievement_id"],
            tier=r["tier"],
            earned_at=r["earned_at"],
            project_uuid=r["project_uuid"],
        )
        for r in rows
    ]


async def list_recent_globally(limit: int = 20) -> list[dict[str, Any]]:
    """Recent achievement awards across the whole world.

    Used by the OBS HUD live-ticker. We join ``characters`` so the
    overlay can render ``"Zara 🦊 unlocked Storyteller ★"`` without a
    second roundtrip per row; the catalog metadata (title /
    description) is merged in from :data:`ACHIEVEMENTS` so the wire
    format is self-describing.
    """
    safe_limit = max(1, min(int(limit), 200))
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    earned_achievements.c.achievement_id,
                    earned_achievements.c.tier,
                    earned_achievements.c.earned_at,
                    earned_achievements.c.project_uuid,
                    earned_achievements.c.character_uuid,
                    characters.c.name,
                    characters.c.species_emoji,
                    characters.c.role,
                )
                .select_from(
                    earned_achievements.join(
                        characters,
                        earned_achievements.c.character_uuid
                        == characters.c.character_uuid,
                    )
                )
                # Tie-break on id so multiple inserts within the same
                # SQLite-second still resolve newest-first.
                .order_by(
                    desc(earned_achievements.c.earned_at),
                    desc(earned_achievements.c.id),
                )
                .limit(safe_limit)
            )
        ).mappings().all()

    out: list[dict[str, Any]] = []
    for r in rows:
        spec = ACHIEVEMENTS.get(r["achievement_id"]) or {}
        out.append(
            {
                "character_uuid": r["character_uuid"],
                "character_name": r["name"],
                "species_emoji": r["species_emoji"],
                "role": r["role"],
                "achievement_id": r["achievement_id"],
                "title": spec.get("title", r["achievement_id"]),
                "description": spec.get("description", ""),
                "tier": r["tier"],
                "project_uuid": r["project_uuid"],
                "earned_at": str(r["earned_at"]),
            }
        )
    return out


async def batch_record(
    character_uuid: str,
    ids: list[str],
    project_uuid: str | None = None,
) -> list[str]:
    """Award a batch of ids; return only the ones that were newly persisted.

    Designed to take the output of :func:`autonoma.world.check_achievements`
    directly. Internally just loops :func:`record_achievement` so the
    unique constraint is the single source of truth on
    "have-we-already-given-this-out".
    """
    newly: list[str] = []
    for ach_id in ids:
        if not ach_id:
            continue
        tier = _resolve_tier(ach_id)
        was_new = await record_achievement(
            character_uuid=character_uuid,
            achievement_id=ach_id,
            tier=tier,
            project_uuid=project_uuid,
        )
        if was_new:
            newly.append(ach_id)
    return newly


__all__ = [
    "EarnedAchievement",
    "record_achievement",
    "list_achievements",
    "list_recent_globally",
    "batch_record",
]
