"""LLM choreographer — composes a :class:`GestureScore` for a character.

The model never sees bone angles. It picks motion families, timing,
amplitude and tempo (and face cues) through one strict tool call; the
families carry the physics. A score that fails validation is sent back
once with the error; after that the procedural fallback composes a safe
score so generation always yields *something* playable.
"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from autonoma.gestures.dsl import GestureScore, families_catalog_text, tool_input_schema
from autonoma.gestures.expand import expand_score
from autonoma.gestures.validate import GestureValidationError, validate_clip, validate_score_limits
from autonoma.llm import BaseLLMClient, ToolSpec

logger = logging.getLogger(__name__)

TRIGGER_MEANING: dict[str, str] = {
    "state:celebrating": "the whole team just finished the project — pure victory",
    "state:thinking": "pondering the next move; thoughtful, not sleepy",
    "state:working": "heads-down building something",
    "state:talking": "mid-conversation beat",
    "state:error": "something just broke; dismay, not rage",
    "state:spawning": "first appearance on stage — make an entrance",
    "state:idle": "waiting, relaxed",
    "emote:✦": "excited sparkle",
    "emote:★": "proud of a win",
    "emote:‼": "determined / fired up",
    "emote:💡": "an idea just landed",
    "emote:♪": "happy, humming",
    "emote:?": "curious, puzzled",
    "emote:•": "focused, locked in",
    "emote:💧": "worried, nervous",
    "emote:💤": "tired, drowsy",
    "emote:💢": "frustrated",
    "emote:✧": "mischievous, scheming",
    "emote:～": "relaxed, chill",
    "emote:✿": "nostalgic, soft",
}


@dataclass
class CharacterContext:
    name: str
    role: str = ""
    species: str = ""
    traits: list[str] = field(default_factory=list)
    catchphrase: str = ""
    rarity: str = ""
    mood: str = ""


@dataclass
class ComposeResult:
    score: GestureScore
    clip: dict[str, Any]
    source: str  # "motion" | "llm" | "fallback"
    model: str = ""
    attempts: int = 0
    error: str = ""


COMPOSE_TOOL = ToolSpec(
    name="compose_gesture",
    description=(
        "Compose one short body gesture for a 3D VTuber character by placing "
        "motion-family segments on a timeline. Call exactly once."
    ),
    input_schema=tool_input_schema(),
)


def _system_prompt() -> str:
    return (
        "You are a character animator composing short gestures for 3D VTuber "
        "characters (VRM humanoids) on a live stage. You never write bone angles; "
        "you place motion families on a timeline.\n\n"
        f"Motion families:\n{families_catalog_text()}\n\n"
        "Rules:\n"
        "- 0.8 to 4 seconds total; 2 to 5 segments; segments may overlap to layer "
        "(e.g. a nod during a wave) but never stack two arm families on the same arm "
        "at the same time.\n"
        "- amplitude 0.1..1.0 scales size; tempo 0.3..2.5 scales oscillation speed.\n"
        "- Face cues are optional: happy/angry/sad/relaxed/surprised/blink with weight 0.05..1.0.\n"
        "- Make it read instantly from a distance and match the character's personality.\n"
        "- Prefer asymmetry and a clear beginning-middle-end over symmetric poses."
    )


def _user_prompt(ctx: CharacterContext, kind: str, value: str, feedback: str) -> str:
    meaning = TRIGGER_MEANING.get(f"{kind}:{value}", f"{kind} = {value}")
    lines = [
        f"Character: {ctx.name}",
        f"Role: {ctx.role or 'teammate'}",
        f"Species: {ctx.species or 'unknown'}; rarity: {ctx.rarity or 'common'}",
        f"Traits: {', '.join(ctx.traits) or 'none listed'}",
        f'Catchphrase: "{ctx.catchphrase}"' if ctx.catchphrase else "",
        f"Current mood: {ctx.mood}" if ctx.mood else "",
        "",
        f"Trigger: {kind} = {value} — {meaning}.",
        "Compose this character's signature gesture for that trigger. "
        "Use the compose_gesture tool.",
    ]
    if feedback:
        lines += ["", f"Your previous attempt was rejected: {feedback}. Fix it."]
    return "\n".join(line for line in lines if line is not None)


def _seed(ctx: CharacterContext, kind: str, value: str) -> int:
    return int(hashlib.md5(f"{ctx.name}:{kind}:{value}".encode()).hexdigest()[:8], 16)


def fallback_score(ctx: CharacterContext, kind: str, value: str) -> GestureScore:
    """Procedural composition, seeded by the character — always valid."""
    rng = random.Random(_seed(ctx, kind, value))
    side = rng.choice(["right", "left"])
    amp = round(rng.uniform(0.6, 0.95), 2)
    tempo = round(rng.uniform(0.8, 1.4), 2)
    key = f"{kind}:{value}"
    if key in ("state:celebrating", "emote:★", "emote:✦"):
        fam = rng.choice(["cheer", "arm_raise", "fist_pump"])
        segs = [
            dict(
                family=fam,
                side="both" if fam == "cheer" else side,
                start=0.0,
                duration=1.6,
                amplitude=amp,
                tempo=tempo,
            ),
            dict(family="nod", side="both", start=0.4, duration=0.8, amplitude=0.5, tempo=1.2),
        ]
        face = [dict(expression="happy", start=0.0, duration=2.0, weight=0.8)]
        duration = 2.2
    elif key in ("state:thinking", "emote:?", "emote:💡"):
        segs = [
            dict(
                family="hand_on_chin", side=side, start=0.0, duration=1.8, amplitude=amp, tempo=1.0
            ),
            dict(
                family="look_around", side="both", start=0.3, duration=1.2, amplitude=0.4, tempo=0.8
            ),
        ]
        face = (
            [dict(expression="surprised", start=1.2, duration=0.6, weight=0.3)]
            if key == "emote:💡"
            else []
        )
        duration = 2.0
    elif key in ("state:error", "emote:💢", "emote:💧"):
        segs = [
            dict(family="facepalm", side=side, start=0.0, duration=1.6, amplitude=amp, tempo=1.0),
            dict(
                family="head_shake", side="both", start=0.5, duration=1.0, amplitude=0.5, tempo=1.2
            ),
        ]
        face = [dict(expression="sad", start=0.0, duration=1.8, weight=0.5)]
        duration = 1.9
    elif key in ("state:spawning", "emote:♪"):
        segs = [
            dict(family="wave", side=side, start=0.0, duration=1.8, amplitude=amp, tempo=tempo),
            dict(family="lean", side=side, start=0.2, duration=1.4, amplitude=0.5, tempo=1.0),
        ]
        face = [dict(expression="happy", start=0.0, duration=2.0, weight=0.7)]
        duration = 2.1
    elif key in ("emote:💤", "emote:～", "emote:✿", "state:idle"):
        segs = [
            dict(family="sway", side="both", start=0.0, duration=2.4, amplitude=amp, tempo=0.8),
            dict(family="head_tilt", side=side, start=0.4, duration=1.6, amplitude=0.6, tempo=1.0),
        ]
        face = [dict(expression="relaxed", start=0.0, duration=2.4, weight=0.6)]
        duration = 2.5
    elif key in ("emote:✧", "emote:‼", "emote:•"):
        segs = [
            dict(
                family="hands_on_hips",
                side="both",
                start=0.0,
                duration=1.6,
                amplitude=amp,
                tempo=1.0,
            ),
            dict(family="head_tilt", side=side, start=0.5, duration=0.9, amplitude=0.7, tempo=1.0),
        ]
        face = [dict(expression="happy", start=0.3, duration=1.2, weight=0.4)]
        duration = 1.8
    else:
        segs = [
            dict(
                family=rng.choice(["nod", "shrug", "point", "wave"]),
                side=side,
                start=0.0,
                duration=1.2,
                amplitude=amp,
                tempo=tempo,
            )
        ]
        face = []
        duration = 1.4
    return GestureScore(name=f"{ctx.name}'s {value}", duration_s=duration, segments=segs, face=face)  # type: ignore[arg-type]


def _check(score: GestureScore) -> dict[str, Any]:
    validate_score_limits(score)
    clip = expand_score(score)
    validate_clip(clip)
    return clip


async def compose(
    client: BaseLLMClient | None,
    ctx: CharacterContext,
    kind: str,
    value: str,
    *,
    model: str = "",
    effort: str | None = "low",
    max_attempts: int = 2,
) -> ComposeResult:
    """Text-to-motion (when configured), else the LLM choreographer, else
    the procedural fallback — the first one that validates wins."""
    feedback = ""
    attempts = 0
    last_error = ""
    from autonoma.config import settings

    if settings.gesture_motion_backend == "http" and settings.gesture_motion_url:
        from autonoma.gestures.motion import MotionBackendError, motion_prompt, text_to_motion

        meaning = TRIGGER_MEANING.get(f"{kind}:{value}", f"{kind} {value}")
        try:
            clip = await text_to_motion(
                motion_prompt(meaning, traits=ctx.traits, species=ctx.species, role=ctx.role),
                duration_s=2.5,
                name=f"{ctx.name}'s {value}",
            )
            # The score records provenance; playback uses the clip.
            score = GestureScore(
                name=f"{ctx.name}'s {value}",
                duration_s=max(0.3, min(6.0, float(clip["durationS"]))),
                segments=[
                    {
                        "family": "sway",
                        "side": "both",
                        "start": 0.0,
                        "duration": max(0.3, min(6.0, float(clip["durationS"]))),
                        "amplitude": 0.1,
                    }
                ],  # type: ignore[list-item]
            )
            return ComposeResult(score=score, clip=clip, source="motion", model="motion-server")
        except MotionBackendError as exc:
            last_error = f"motion: {exc}"
            logger.warning("[gestures] text-to-motion failed, using choreographer: %s", exc)
    if client is not None and model:
        for _ in range(max_attempts):
            attempts += 1
            try:
                response = await client.create(
                    model=model,
                    max_tokens=4096,
                    system=_system_prompt(),
                    messages=[
                        {"role": "user", "content": _user_prompt(ctx, kind, value, feedback)}
                    ],
                    effort=effort,
                    tools=[COMPOSE_TOOL],
                    single_tool_call=True,
                )
            except Exception as exc:  # provider errors → fallback
                last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("[gestures] choreographer call failed: %s", last_error)
                break
            if response.refused or not response.tool_calls:
                last_error = "no tool call" if not response.refused else "refused"
                feedback = "you must call compose_gesture"
                continue
            try:
                score = GestureScore.model_validate(response.tool_calls[0].input)
                clip = _check(score)
            except (ValidationError, GestureValidationError, ValueError) as exc:
                last_error = str(exc)[:300]
                feedback = last_error
                continue
            return ComposeResult(
                score=score, clip=clip, source="llm", model=model, attempts=attempts
            )

    score = fallback_score(ctx, kind, value)
    clip = _check(score)
    return ComposeResult(
        score=score, clip=clip, source="fallback", model=model, attempts=attempts, error=last_error
    )
