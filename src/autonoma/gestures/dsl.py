"""Gesture score — what the choreographer writes.

Deliberately small: a score is a handful of family segments on a
timeline plus optional face cues. Raw bone angles are never exposed to
the model; the families carry the axis conventions, so every score that
validates is physically plausible by construction.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autonoma.gestures.families import FAMILIES, FAMILY_DESCRIPTIONS

FamilyName = Literal[
    "arm_raise",
    "wave",
    "point",
    "fist_pump",
    "hand_on_chin",
    "facepalm",
    "clap",
    "cheer",
    "hands_on_hips",
    "shrug",
    "nod",
    "head_shake",
    "head_tilt",
    "lean",
    "lean_back",
    "bow",
    "sway",
    "look_around",
]
Side = Literal["right", "left", "both"]
Expression = Literal["happy", "angry", "sad", "relaxed", "surprised", "blink"]

MAX_DURATION_S = 6.0
MIN_DURATION_S = 0.3
MAX_SEGMENTS = 8
MAX_FACE_CUES = 6


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: FamilyName
    side: Side = "right"
    start: float = Field(ge=0.0, le=MAX_DURATION_S, description="seconds from clip start")
    duration: float = Field(gt=0.05, le=MAX_DURATION_S)
    amplitude: float = Field(default=0.8, ge=0.1, le=1.0)
    tempo: float = Field(default=1.0, ge=0.3, le=2.5, description="oscillation speed multiplier")


class FaceCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: Expression
    start: float = Field(ge=0.0, le=MAX_DURATION_S)
    duration: float = Field(gt=0.05, le=MAX_DURATION_S)
    weight: float = Field(default=0.6, ge=0.05, le=1.0)


class GestureScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=48)
    duration_s: float = Field(ge=MIN_DURATION_S, le=MAX_DURATION_S)
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS)
    face: list[FaceCue] = Field(default_factory=list, max_length=MAX_FACE_CUES)

    @model_validator(mode="after")
    def _within_duration(self) -> "GestureScore":
        for seg in self.segments:
            if seg.start + seg.duration > self.duration_s + 1e-6:
                raise ValueError(
                    f"segment {seg.family} ends at {seg.start + seg.duration:.2f}s, "
                    f"after the clip's {self.duration_s:.2f}s"
                )
            if seg.family not in FAMILIES:
                raise ValueError(f"unknown family {seg.family}")
        for cue in self.face:
            if cue.start + cue.duration > self.duration_s + 1e-6:
                raise ValueError(f"face cue {cue.expression} runs past the clip end")
        return self


def tool_input_schema() -> dict[str, Any]:
    """Strict JSON Schema for the ``compose_gesture`` tool (every property
    required, no extras — what strict tool use needs on both providers)."""
    segment = {
        "type": "object",
        "properties": {
            "family": {"type": "string", "enum": list(FAMILIES.keys())},
            "side": {"type": "string", "enum": ["right", "left", "both"]},
            "start": {"type": "number", "description": "seconds from clip start"},
            "duration": {"type": "number", "description": "seconds"},
            "amplitude": {"type": "number", "description": "0.1..1.0"},
            "tempo": {"type": "number", "description": "0.3..2.5, oscillation speed"},
        },
        "required": ["family", "side", "start", "duration", "amplitude", "tempo"],
        "additionalProperties": False,
    }
    cue = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "enum": ["happy", "angry", "sad", "relaxed", "surprised", "blink"],
            },
            "start": {"type": "number"},
            "duration": {"type": "number"},
            "weight": {"type": "number", "description": "0.05..1.0"},
        },
        "required": ["expression", "start", "duration", "weight"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "short evocative name, ≤48 chars"},
            "duration_s": {"type": "number", "description": f"{MIN_DURATION_S}..{MAX_DURATION_S}"},
            "segments": {"type": "array", "items": segment, "description": f"1..{MAX_SEGMENTS}"},
            "face": {"type": "array", "items": cue, "description": f"0..{MAX_FACE_CUES}"},
        },
        "required": ["name", "duration_s", "segments", "face"],
        "additionalProperties": False,
    }


def families_catalog_text() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in FAMILY_DESCRIPTIONS.items())
