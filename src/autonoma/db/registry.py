"""Persistent character registry — the bridge between in-memory agents and the DB.

Lifecycle
─────────
Three DB-touching moments per swarm run:

1. **swarm.run() start** → ``registry.begin_project(name, goal, max_rounds)``
   creates a ``projects`` row and remembers its uuid.
2. **spawn_agent()** → ``registry.hydrate(seed_hash, name, role, bones)``
   returns a ``LiveCharacter`` (new or loaded). The swarm applies the
   returned level / xp / lifetime counters to the in-memory agent.
3. **swarm.run() end** → ``registry.finish_project(...)`` persists:
   - updated character rows (level, xp, lifetime stats, last_seen)
   - a ``project_participants`` row per character
   - every relationship edge with familiarity > 0
   - wills / graveyard entries for anyone who died
   - one ``character_stats_history`` snapshot per survivor

Revival policy
──────────────
At hydrate time, given a (seed_hash, name) lookup:

- **Living character exists** → return it; the swarm uses its persisted
  level + lifetime stats.
- **Dead character exists AND rarity == legendary** → revive it
  (``is_alive = 1``) and return it. Legends never stay dead.
- **Dead character exists AND rarity != legendary** → create a *new* row
  with a new uuid. The old row stays in the graveyard immutable; the
  "same name" coming back is a fresh generation, not a reincarnation.
- **No character exists** → create one from the bones.

The in-memory cache (``_cache``) keeps per-session records so repeated
spawns inside the same run don't thrash the DB.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, insert, select, update

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import (
    character_stats_history,
    characters,
    famous_quotes,
    graveyard,
    project_participants,
    projects,
)
from autonoma.db.schema import relationships as relationships_table
from autonoma.db.schema import wills as wills_table

logger = logging.getLogger(__name__)


def seed_hash_for(role: str, name: str) -> str:
    """Stable hash matching ``AgentBones.from_role``'s seed derivation.

    Kept in sync with ``autonoma.world``. If that function's seed formula
    changes, update this helper too or persisted characters will become
    unreachable.
    """
    seed_str = f"{role}:{name}:autonoma-world-v1"
    return hashlib.md5(seed_str.encode()).hexdigest()


@dataclass
class LiveCharacter:
    """In-memory view of a persisted character. Mutated during the run;
    flushed back to the DB at ``finish_project``."""

    character_uuid: str
    seed_hash: str
    name: str
    role: str
    species: str
    species_emoji: str
    catchphrase: str
    rarity: str
    level: int
    total_xp_earned: int
    runs_survived: int
    runs_died: int
    tasks_completed_lifetime: int
    files_created_lifetime: int
    stats: dict[str, int]
    traits: list[str]
    last_mood: str
    voice_id: str
    is_alive: bool
    is_new: bool = False  # True if this row was just created in hydrate()
    # recalled context for opening narration / situation report
    past_wills: list[str] = field(default_factory=list)
    past_epitaphs: list[str] = field(default_factory=list)


class CharacterRegistry:
    """Session-scoped registry. Create one per swarm run."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        # character_uuid -> LiveCharacter
        self._cache: dict[str, LiveCharacter] = {}
        # seed_hash -> character_uuid (session lookup speedup)
        self._seed_to_uuid: dict[str, str] = {}
        self._project_uuid: str | None = None
        self._project_started_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def project_uuid(self) -> str | None:
        return self._project_uuid

    # ── Project lifecycle ──────────────────────────────────────────────

    async def begin_project(
        self,
        name: str,
        description: str,
        goal: str,
        max_rounds: int,
    ) -> str | None:
        """Open a persisted project row. Returns project_uuid (or None if disabled)."""
        if not self._enabled:
            return None
        await init_db()
        self._project_uuid = str(uuid.uuid4())
        self._project_started_at = datetime.utcnow()
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                insert(projects).values(
                    project_uuid=self._project_uuid,
                    name=name[:128],
                    description=description,
                    goal=goal,
                    status="running",
                    max_rounds=max_rounds,
                )
            )
        logger.info("Registry: project %s started", self._project_uuid)
        return self._project_uuid

    async def finish_project(
        self,
        *,
        status: str,
        exit_reason: str,
        rounds_used: int,
        final_answer: str,
        survivors: list[str],
        deaths: list[dict[str, Any]],
        wills: list[dict[str, str]],
        relationships: list[dict[str, Any]],
        famous: list[dict[str, Any]],
    ) -> None:
        """Flush every session mutation back to the DB.

        Arguments
        ---------
        survivors
            ``character_uuid``s of every agent alive at run end.
        deaths
            List of ``{character_uuid, round, cause, epitaph}``.
        wills
            List of ``{character_uuid, text}``.
        relationships
            List of ``{from_uuid, to_uuid, trust, familiarity, shared_tasks,
            conflicts, sentiment, last_interaction}``.
        famous
            List of ``{character_uuid, text, round}``.
        """
        if not self._enabled or self._project_uuid is None:
            return
        engine = get_engine()
        project_uuid = self._project_uuid
        async with engine.begin() as conn:
            # 1. projects row
            await conn.execute(
                update(projects)
                .where(projects.c.project_uuid == project_uuid)
                .values(
                    status=status,
                    exit_reason=exit_reason,
                    rounds_used=rounds_used,
                    ended_at=datetime.utcnow(),
                    final_answer=final_answer,
                )
            )

            # 2. characters rows — flush lifetime stats for everyone the
            #    session touched (cache == everyone hydrated this run).
            for live in self._cache.values():
                survived = live.character_uuid in survivors
                # bump lifetime counters ONE time at run end
                live.runs_survived += 1 if survived else 0
                live.runs_died += 0 if survived else 1
                live.is_alive = survived

                await conn.execute(
                    update(characters)
                    .where(characters.c.character_uuid == live.character_uuid)
                    .values(
                        level=live.level,
                        total_xp_earned=live.total_xp_earned,
                        runs_survived=live.runs_survived,
                        runs_died=live.runs_died,
                        tasks_completed_lifetime=live.tasks_completed_lifetime,
                        files_created_lifetime=live.files_created_lifetime,
                        stats_json=json.dumps(live.stats),
                        traits_json=json.dumps(live.traits),
                        last_mood=live.last_mood,
                        voice_id=live.voice_id,
                        is_alive=1 if live.is_alive else 0,
                        last_seen_at=datetime.utcnow(),
                    )
                )

                # 3. project_participants
                await conn.execute(
                    insert(project_participants).values(
                        project_uuid=project_uuid,
                        character_uuid=live.character_uuid,
                        role_in_run=live.role,
                        xp_earned=live.total_xp_earned,  # recalculated downstream
                        tasks_completed=live.tasks_completed_lifetime,
                        files_created=live.files_created_lifetime,
                        survived=1 if survived else 0,
                        death_cause=_find_death_cause(live.character_uuid, deaths),
                        left_at=datetime.utcnow(),
                    )
                )

                # 4. stats history snapshot (survivors only — losing characters get one via
                #    graveyard + participants row, no need to double-record).
                if survived:
                    await conn.execute(
                        insert(character_stats_history).values(
                            character_uuid=live.character_uuid,
                            project_uuid=project_uuid,
                            level=live.level,
                            total_xp_earned=live.total_xp_earned,
                            stats_json=json.dumps(live.stats),
                        )
                    )

            # 5. relationships
            for rel in relationships:
                # upsert: try update; if zero rows, insert.
                result = await conn.execute(
                    update(relationships_table)
                    .where(
                        and_(
                            relationships_table.c.from_uuid == rel["from_uuid"],
                            relationships_table.c.to_uuid == rel["to_uuid"],
                        )
                    )
                    .values(
                        trust=rel["trust"],
                        familiarity=rel["familiarity"],
                        shared_tasks=rel.get("shared_tasks", 0),
                        conflicts=rel.get("conflicts", 0),
                        sentiment=rel.get("sentiment", "neutral"),
                        last_interaction=rel.get("last_interaction", ""),
                        updated_at=datetime.utcnow(),
                    )
                )
                if result.rowcount == 0:
                    await conn.execute(
                        insert(relationships_table).values(
                            from_uuid=rel["from_uuid"],
                            to_uuid=rel["to_uuid"],
                            trust=rel["trust"],
                            familiarity=rel["familiarity"],
                            shared_tasks=rel.get("shared_tasks", 0),
                            conflicts=rel.get("conflicts", 0),
                            sentiment=rel.get("sentiment", "neutral"),
                            last_interaction=rel.get("last_interaction", ""),
                        )
                    )

            # 6. graveyard rows
            for d in deaths:
                await conn.execute(
                    insert(graveyard).values(
                        character_uuid=d["character_uuid"],
                        project_uuid=project_uuid,
                        died_at_round=d.get("round", 0),
                        cause=d.get("cause", "unknown")[:64],
                        epitaph=d.get("epitaph", ""),
                    )
                )

            # 7. wills
            for w in wills:
                if not w.get("text"):
                    continue
                await conn.execute(
                    insert(wills_table).values(
                        character_uuid=w["character_uuid"],
                        project_uuid=project_uuid,
                        text=w["text"],
                    )
                )

            # 8. famous quotes
            for q in famous:
                if not q.get("text"):
                    continue
                await conn.execute(
                    insert(famous_quotes).values(
                        character_uuid=q["character_uuid"],
                        project_uuid=project_uuid,
                        text=q["text"],
                        round_number=q.get("round", 0),
                    )
                )

        logger.info(
            "Registry: project %s finished (%s, rounds=%d, survivors=%d, deaths=%d)",
            project_uuid, status, rounds_used, len(survivors), len(deaths),
        )
        self._project_uuid = None

    # ── Per-agent hydration ────────────────────────────────────────────

    async def hydrate(
        self,
        *,
        role: str,
        name: str,
        bones_species: str,
        bones_species_emoji: str,
        bones_catchphrase: str,
        bones_rarity: str,
        bones_stats: dict[str, int],
        bones_traits: list[str],
    ) -> LiveCharacter:
        """Return a LiveCharacter for (role, name). Creates a row when needed.

        The swarm passes the in-memory bones fields so we don't re-import
        ``world.AgentBones`` here (avoids a circular dep).
        """
        sh = seed_hash_for(role, name)
        if sh in self._seed_to_uuid:
            return self._cache[self._seed_to_uuid[sh]]

        if not self._enabled:
            # stub: synthesize a LiveCharacter without DB roundtrip
            live = LiveCharacter(
                character_uuid=str(uuid.uuid4()),
                seed_hash=sh,
                name=name,
                role=role,
                species=bones_species,
                species_emoji=bones_species_emoji,
                catchphrase=bones_catchphrase,
                rarity=bones_rarity,
                level=1,
                total_xp_earned=0,
                runs_survived=0,
                runs_died=0,
                tasks_completed_lifetime=0,
                files_created_lifetime=0,
                stats=dict(bones_stats),
                traits=list(bones_traits),
                last_mood="",
                voice_id="",
                is_alive=True,
                is_new=True,
            )
            self._cache[live.character_uuid] = live
            self._seed_to_uuid[sh] = live.character_uuid
            return live

        await init_db()
        engine = get_engine()

        # Find the best candidate row for this seed: prefer alive, else the
        # latest dead row if legendary, else fall through to create.
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(characters)
                    .where(characters.c.seed_hash == sh)
                    .where(characters.c.name == name)
                    .order_by(desc(characters.c.is_alive), desc(characters.c.last_seen_at))
                )
            ).mappings().all()

        chosen = None
        if rows:
            alive_rows = [r for r in rows if r["is_alive"]]
            if alive_rows:
                chosen = alive_rows[0]
            else:
                # Resurrect legendaries; everyone else stays dead.
                latest = rows[0]
                if latest["rarity"] == "legendary":
                    chosen = latest

        if chosen is not None:
            live = _live_from_row(chosen)
            # Recall past wills + epitaphs for narrator flavor.
            live.past_wills, live.past_epitaphs = await self._load_legacy(live.character_uuid)
            if not live.is_alive and live.rarity == "legendary":
                # mark alive now; final row update happens at finish_project
                live.is_alive = True
                async with engine.begin() as conn:
                    await conn.execute(
                        update(characters)
                        .where(characters.c.character_uuid == live.character_uuid)
                        .values(is_alive=1)
                    )
            self._cache[live.character_uuid] = live
            self._seed_to_uuid[sh] = live.character_uuid
            return live

        # Fresh spawn.
        new_uuid = str(uuid.uuid4())
        async with engine.begin() as conn:
            await conn.execute(
                insert(characters).values(
                    character_uuid=new_uuid,
                    seed_hash=sh,
                    name=name,
                    role=role,
                    species=bones_species,
                    species_emoji=bones_species_emoji,
                    catchphrase=bones_catchphrase,
                    rarity=bones_rarity,
                    level=1,
                    total_xp_earned=0,
                    stats_json=json.dumps(dict(bones_stats)),
                    traits_json=json.dumps(list(bones_traits)),
                    is_alive=1,
                )
            )
        live = LiveCharacter(
            character_uuid=new_uuid,
            seed_hash=sh,
            name=name,
            role=role,
            species=bones_species,
            species_emoji=bones_species_emoji,
            catchphrase=bones_catchphrase,
            rarity=bones_rarity,
            level=1,
            total_xp_earned=0,
            runs_survived=0,
            runs_died=0,
            tasks_completed_lifetime=0,
            files_created_lifetime=0,
            stats=dict(bones_stats),
            traits=list(bones_traits),
            last_mood="",
            voice_id="",
            is_alive=True,
            is_new=True,
        )
        self._cache[new_uuid] = live
        self._seed_to_uuid[sh] = new_uuid
        return live

    # ── Helpers ────────────────────────────────────────────────────────

    async def _load_legacy(self, character_uuid: str) -> tuple[list[str], list[str]]:
        """Fetch a character's prior wills + epitaphs (for narrator context)."""
        engine = get_engine()
        async with engine.connect() as conn:
            wills_rows = (
                await conn.execute(
                    select(wills_table.c.text)
                    .where(wills_table.c.character_uuid == character_uuid)
                    .order_by(desc(wills_table.c.written_at))
                    .limit(3)
                )
            ).all()
            grave_rows = (
                await conn.execute(
                    select(graveyard.c.epitaph)
                    .where(graveyard.c.character_uuid == character_uuid)
                    .order_by(desc(graveyard.c.died_at))
                    .limit(3)
                )
            ).all()
        return (
            [r[0] for r in wills_rows if r[0]],
            [r[0] for r in grave_rows if r[0]],
        )

    def resolve_name(self, name: str) -> str | None:
        """Return character_uuid for a display name used in this session."""
        for live in self._cache.values():
            if live.name == name:
                return live.character_uuid
        return None

    def cached(self) -> list[LiveCharacter]:
        return list(self._cache.values())


# ── Module helpers ────────────────────────────────────────────────────


def _live_from_row(row) -> LiveCharacter:
    return LiveCharacter(
        character_uuid=row["character_uuid"],
        seed_hash=row["seed_hash"],
        name=row["name"],
        role=row["role"],
        species=row["species"],
        species_emoji=row["species_emoji"],
        catchphrase=row["catchphrase"],
        rarity=row["rarity"],
        level=row["level"],
        total_xp_earned=row["total_xp_earned"],
        runs_survived=row["runs_survived"],
        runs_died=row["runs_died"],
        tasks_completed_lifetime=row["tasks_completed_lifetime"],
        files_created_lifetime=row["files_created_lifetime"],
        stats=json.loads(row["stats_json"] or "{}"),
        traits=json.loads(row["traits_json"] or "[]"),
        last_mood=row["last_mood"],
        voice_id=row["voice_id"],
        is_alive=bool(row["is_alive"]),
        is_new=False,
    )


def _find_death_cause(character_uuid: str, deaths: list[dict[str, Any]]) -> str:
    for d in deaths:
        if d.get("character_uuid") == character_uuid:
            return str(d.get("cause", "unknown"))[:64]
    return ""
