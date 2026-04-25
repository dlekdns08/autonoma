"""Cutscene data models — Pydantic for free validation/serialisation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from pydantic import BaseModel, Field, field_validator


class CutsceneStepKind(str, Enum):
    CLIP = "clip"
    SPEECH = "speech"
    SFX = "sfx"
    DELAY = "delay"


# Per-kind payload shapes. We keep them as flat dicts (rather than a
# discriminated union) because the frontend timeline editor edits steps
# generically — it only cares about ``kind`` + ``at_ms`` + ``payload``.

class CutsceneStep(BaseModel):
    """A single timed step in a cutscene."""

    # Offset in milliseconds from the cutscene's start. The runtime
    # player schedules each step independently, so steps may overlap
    # (e.g. play a clip + speech line at the same time).
    at_ms: int = Field(ge=0, le=10 * 60 * 1000)
    kind: CutsceneStepKind
    # Short label so the editor list reads cleanly. Optional.
    label: str = Field(default="", max_length=80)
    # Kind-specific payload. Validated lazily by the player; the schema
    # gives a hint, the player tolerates missing/extra keys.
    #
    # Expected keys per kind:
    #   clip:   {clip_id: str, vrm_file: str}
    #   speech: {agent: str, text: str, style?: str}
    #   sfx:    {sfx_name: str}
    #   delay:  {} (purely scheduling)
    payload: dict[str, Any] = Field(default_factory=dict)


class CutsceneTrigger(BaseModel):
    """When/how a cutscene fires automatically.

    ``kind="manual"`` is the default — the host clicks Play. Other
    kinds let the cutscene fire off bus events:

      * ``project_complete`` — fire when ``project.completed`` event
        emits. Most common: end-of-run cinematic.
      * ``achievement`` — fire when ``achievement.earned`` matches
        ``value`` (achievement_id).
      * ``boss_defeated`` — fire on boss defeat.
    """

    kind: Literal[
        "manual",
        "project_complete",
        "achievement",
        "boss_defeated",
    ] = "manual"
    value: str = ""  # e.g. achievement_id when kind="achievement"


class Cutscene(BaseModel):
    """A complete cutscene script. Persisted as JSON, played by the
    frontend ``useCutscenes`` hook.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    owner_user_id: str
    name: str = Field(default="Untitled Cutscene", max_length=120)
    description: str = Field(default="", max_length=500)
    steps: list[CutsceneStep] = Field(default_factory=list)
    trigger: CutsceneTrigger = Field(default_factory=CutsceneTrigger)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    @field_validator("steps")
    @classmethod
    def _sort_steps(cls, value: list[CutsceneStep]) -> list[CutsceneStep]:
        # The player tolerates unsorted steps but the editor reads more
        # naturally when they appear in chronological order.
        return sorted(value, key=lambda s: s.at_ms)

    def total_duration_ms(self) -> int:
        if not self.steps:
            return 0
        last = self.steps[-1]
        # ``delay`` payload may carry an explicit length. Other kinds
        # don't — we assume ~2s budget per step purely for the editor's
        # progress bar; the actual playback duration is determined by
        # the underlying clip/speech length on the client.
        if last.kind == CutsceneStepKind.DELAY:
            extra = int(last.payload.get("duration_ms", 0) or 0)
        else:
            extra = 2_000
        return last.at_ms + max(0, extra)

    def touch(self) -> None:
        self.updated_at = _now_iso()
