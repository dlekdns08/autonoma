"""Generated-gesture store + orchestration.

``ensure_gesture`` is the one entry point the swarm and the API use: it
returns the active gesture for ``(agent, trigger)`` or composes, validates
and stores a new one. Clips are served in the same shape as mocap clips
so the stage's clip loader needs only an id prefix (``gen:<id>``).
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select, update

from autonoma.config import settings
from autonoma.db.engine import get_engine
from autonoma.db.schema import generated_gestures
from autonoma.event_bus import bus
from autonoma.gestures.choreographer import CharacterContext, compose
from autonoma.gestures.dsl import GestureScore
from autonoma.llm import BaseLLMClient
from autonoma.mocap.triggers import validate_trigger

logger = logging.getLogger(__name__)

# In-process guard so concurrent requests for the same slot compose once.
_inflight: dict[str, asyncio.Task[Any]] = {}


@dataclass(slots=True)
class GeneratedGesture:
    id: str
    agent_name: str
    character_uuid: str
    trigger_kind: str
    trigger_value: str
    name: str
    duration_s: float
    fps: int
    frame_count: int
    source: str
    model: str
    votes_up: int
    votes_down: int
    status: str
    created_at: str
    score: dict[str, Any] | None = None

    @property
    def clip_ref(self) -> str:
        """Id the stage's clip loader understands (``gen:`` namespace)."""
        return f"gen:{self.id}"

    def to_dict(self, *, include_score: bool = False) -> dict[str, Any]:
        d = {
            "id": self.id,
            "clip_ref": self.clip_ref,
            "agent_name": self.agent_name,
            "character_uuid": self.character_uuid,
            "trigger_kind": self.trigger_kind,
            "trigger_value": self.trigger_value,
            "name": self.name,
            "duration_s": self.duration_s,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "source": self.source,
            "model": self.model,
            "votes_up": self.votes_up,
            "votes_down": self.votes_down,
            "status": self.status,
            "created_at": self.created_at,
        }
        if include_score:
            d["score"] = self.score
        return d


def _row(row: Any, *, with_score: bool = False) -> GeneratedGesture:
    return GeneratedGesture(
        id=row.id,
        agent_name=row.agent_name,
        character_uuid=row.character_uuid or "",
        trigger_kind=row.trigger_kind,
        trigger_value=row.trigger_value,
        name=row.name,
        duration_s=float(row.duration_s),
        fps=int(row.fps),
        frame_count=int(row.frame_count),
        source=row.source,
        model=row.model or "",
        votes_up=int(row.votes_up or 0),
        votes_down=int(row.votes_down or 0),
        status=row.status,
        created_at=str(row.created_at or ""),
        score=json.loads(row.score_json) if with_score and row.score_json else None,
    )


# ── reads ─────────────────────────────────────────────────────────────


async def get_active(agent_name: str, kind: str, value: str) -> GeneratedGesture | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(generated_gestures)
                .where(
                    and_(
                        generated_gestures.c.agent_name == agent_name,
                        generated_gestures.c.trigger_kind == kind,
                        generated_gestures.c.trigger_value == value,
                        generated_gestures.c.status == "active",
                    )
                )
                .order_by(generated_gestures.c.created_at.desc())
            )
        ).first()
    return _row(row) if row is not None else None


async def list_for_agent(
    agent_name: str, *, include_retired: bool = False
) -> list[GeneratedGesture]:
    engine = get_engine()
    async with engine.connect() as conn:
        q = select(generated_gestures).where(generated_gestures.c.agent_name == agent_name)
        if not include_retired:
            q = q.where(generated_gestures.c.status == "active")
        rows = (await conn.execute(q.order_by(generated_gestures.c.created_at.desc()))).all()
    return [_row(r) for r in rows]


async def get(gesture_id: str, *, with_score: bool = False) -> GeneratedGesture | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(generated_gestures).where(generated_gestures.c.id == gesture_id)
            )
        ).first()
    return _row(row, with_score=with_score) if row is not None else None


async def get_payload(gesture_id: str) -> tuple[GeneratedGesture, str] | None:
    """``(gesture, payload_gz_b64)`` in the mocap clip endpoint's shape."""
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(generated_gestures).where(generated_gestures.c.id == gesture_id)
            )
        ).first()
    if row is None:
        return None
    return _row(row), base64.b64encode(row.payload_gz).decode("ascii")


# ── writes ────────────────────────────────────────────────────────────


async def _store(
    *,
    agent_name: str,
    character_uuid: str,
    kind: str,
    value: str,
    score: GestureScore,
    clip: dict[str, Any],
    source: str,
    model: str,
) -> GeneratedGesture:
    gesture_id = str(uuid.uuid4())
    clip = {**clip, "id": f"gen:{gesture_id}"}
    payload_gz = gzip.compress(json.dumps(clip, separators=(",", ":")).encode("utf-8"))
    engine = get_engine()
    async with engine.begin() as conn:
        # Retire any previous active row for the slot so lookups stay unique.
        await conn.execute(
            update(generated_gestures)
            .where(
                and_(
                    generated_gestures.c.agent_name == agent_name,
                    generated_gestures.c.trigger_kind == kind,
                    generated_gestures.c.trigger_value == value,
                    generated_gestures.c.status == "active",
                )
            )
            .values(status="retired")
        )
        await conn.execute(
            generated_gestures.insert().values(
                id=gesture_id,
                agent_name=agent_name,
                character_uuid=character_uuid or "",
                trigger_kind=kind,
                trigger_value=value,
                name=score.name[:64],
                score_json=score.model_dump_json(),
                payload_gz=payload_gz,
                duration_s=float(clip["durationS"]),
                fps=int(clip["fps"]),
                frame_count=int(clip["frameCount"]),
                source=source,
                model=model[:64],
                status="active",
            )
        )
        row = (
            await conn.execute(
                select(generated_gestures).where(generated_gestures.c.id == gesture_id)
            )
        ).first()
    assert row is not None
    return _row(row)


async def ensure_gesture(
    *,
    agent_name: str,
    kind: str,
    value: str,
    ctx: CharacterContext,
    client: BaseLLMClient | None,
    model: str = "",
    effort: str | None = None,
    character_uuid: str = "",
    force: bool = False,
) -> GeneratedGesture:
    """Return the active gesture for the slot, composing one if missing."""
    err = validate_trigger(kind, value)
    if err:
        raise ValueError(err)
    if not force:
        existing = await get_active(agent_name, kind, value)
        if existing is not None:
            return existing

    key = f"{agent_name}|{kind}|{value}"
    task = _inflight.get(key)
    if task is None:

        async def _compose_and_store() -> GeneratedGesture:
            result = await compose(
                client,
                ctx,
                kind,
                value,
                model=model,
                effort=effort or settings.gesture_gen_effort or None,
            )
            row = await _store(
                agent_name=agent_name,
                character_uuid=character_uuid,
                kind=kind,
                value=value,
                score=result.score,
                clip=result.clip,
                source=result.source,
                model=result.model,
            )
            logger.info(
                "[gestures] %s %s:%s → %r (%s, %d attempt(s))",
                agent_name,
                kind,
                value,
                row.name,
                result.source,
                result.attempts,
            )
            await bus.emit(
                "gesture.generated",
                agent=agent_name,
                kind=kind,
                value=value,
                id=row.id,
                clip_ref=row.clip_ref,
                gesture_name=row.name,
                source=result.source,
            )
            return row

        task = asyncio.create_task(_compose_and_store())
        _inflight[key] = task
        task.add_done_callback(lambda _t: _inflight.pop(key, None))
    return await task


async def vote(gesture_id: str, *, up: bool) -> GeneratedGesture | None:
    engine = get_engine()
    async with engine.begin() as conn:
        col = generated_gestures.c.votes_up if up else generated_gestures.c.votes_down
        await conn.execute(
            update(generated_gestures)
            .where(generated_gestures.c.id == gesture_id)
            .values({col.name: col + 1})
        )
        row = (
            await conn.execute(
                select(generated_gestures).where(generated_gestures.c.id == gesture_id)
            )
        ).first()
        if row is None:
            return None
        net = int(row.votes_up or 0) - int(row.votes_down or 0)
        if row.status == "active" and net <= settings.gesture_retire_net_votes:
            await conn.execute(
                update(generated_gestures)
                .where(generated_gestures.c.id == gesture_id)
                .values(status="retired")
            )
            row = (
                await conn.execute(
                    select(generated_gestures).where(generated_gestures.c.id == gesture_id)
                )
            ).first()
    gesture = _row(row)
    if gesture.status == "retired":
        await bus.emit(
            "gesture.retired",
            agent=gesture.agent_name,
            kind=gesture.trigger_kind,
            value=gesture.trigger_value,
            id=gesture.id,
        )
    return gesture


def signature_triggers(mood: str, emote_for_mood: dict[str, str]) -> list[tuple[str, str]]:
    """Resolve ``settings.gesture_gen_signature_triggers`` for an agent."""
    out: list[tuple[str, str]] = []
    for raw in settings.gesture_gen_signature_triggers:
        kind, _, value = raw.partition(":")
        if value == "@mood":
            value = emote_for_mood.get(mood, "")
        if not kind or not value:
            continue
        if validate_trigger(kind, value) is None:
            out.append((kind, value))
    return out
