"""Self-authored gestures: DSL, families, expansion, validation, choreographer."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from autonoma.agents.base import MOOD_EMOTE
from autonoma.gestures import choreographer
from autonoma.gestures.choreographer import CharacterContext, compose, fallback_score
from autonoma.gestures.dsl import GestureScore, tool_input_schema
from autonoma.gestures.expand import euler_yxz_to_quat, expand_score
from autonoma.gestures.families import FAMILIES, FAMILY_DESCRIPTIONS
from autonoma.gestures.validate import (
    GestureValidationError,
    validate_clip,
    validate_score_limits,
)
from autonoma.llm import BaseLLMClient, LLMResponse, ToolCall
from autonoma.mocap.triggers import EMOTE_TRIGGERS, STATE_TRIGGERS


def _score(**over: Any) -> GestureScore:
    base: dict[str, Any] = dict(
        name="test",
        duration_s=2.0,
        segments=[
            {"family": "wave", "side": "right", "start": 0.1, "duration": 1.6},
            {"family": "nod", "side": "both", "start": 0.3, "duration": 1.0},
        ],
        face=[{"expression": "happy", "start": 0.0, "duration": 2.0, "weight": 0.7}],
    )
    base.update(over)
    return GestureScore.model_validate(base)


def test_every_family_is_described_and_within_limits_at_full_amplitude() -> None:
    assert set(FAMILIES) == set(FAMILY_DESCRIPTIONS)
    for family in FAMILIES:
        for side in ("right", "left", "both"):
            score = GestureScore(
                name=family,
                duration_s=2.0,
                segments=[
                    {
                        "family": family,
                        "side": side,
                        "start": 0.0,
                        "duration": 2.0,
                        "amplitude": 1.0,
                        "tempo": 2.5,
                    }  # type: ignore[list-item]
                ],
            )
            validate_score_limits(score)
            validate_clip(expand_score(score))


def test_tool_schema_is_strict() -> None:
    schema = tool_input_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    seg = schema["properties"]["segments"]["items"]
    assert seg["additionalProperties"] is False
    assert set(seg["required"]) == set(seg["properties"])
    assert set(seg["properties"]["family"]["enum"]) == set(FAMILIES)


def test_score_rejects_segments_past_the_end_and_extras() -> None:
    with pytest.raises(ValidationError):
        _score(segments=[{"family": "wave", "side": "right", "start": 1.5, "duration": 1.0}])
    with pytest.raises(ValidationError):
        _score(duration_s=9.0)
    with pytest.raises(ValidationError):
        _score(
            segments=[
                {"family": "wave", "side": "right", "start": 0, "duration": 1, "bone": "head"}
            ]
        )


def test_expand_produces_wire_format() -> None:
    clip = expand_score(_score(), fps=30)
    assert clip["version"] == 1
    assert clip["frameCount"] == 61
    assert clip["durationS"] == 2.0
    for bone, track in clip["bones"].items():
        assert len(track["data"]) == 61 * 4, bone
    assert set(clip["bones"]) == {"chest", "head", "rightHand", "rightLowerArm", "rightUpperArm"}
    assert list(clip["expressions"]) == ["happy"]
    assert len(clip["expressions"]["happy"]["data"]) == 61
    # Frame 0 is rest (identity quaternion) — envelope starts at zero.
    assert clip["bones"]["rightUpperArm"]["data"][:4] == [0.0, 0.0, 0.0, 1.0]
    assert max(clip["expressions"]["happy"]["data"]) == pytest.approx(0.7, abs=0.01)


def test_euler_yxz_matches_three_js_convention() -> None:
    # Pure rotations map onto a single axis component.
    x, y, z, w = euler_yxz_to_quat(0.0, 0.0, 1.0)
    assert (x, y) == (0.0, 0.0) and z > 0 and w > 0
    x, y, z, w = euler_yxz_to_quat(0.5, 0.0, 0.0)
    assert x > 0 and y == 0.0 and z == 0.0


def test_stacked_arm_segments_fail_joint_limits() -> None:
    score = GestureScore(
        name="overstack",
        duration_s=2.0,
        segments=[
            {
                "family": "arm_raise",
                "side": "right",
                "start": 0.0,
                "duration": 2.0,
                "amplitude": 1.0,
            },  # type: ignore[list-item]
            {"family": "wave", "side": "right", "start": 0.0, "duration": 2.0, "amplitude": 1.0},  # type: ignore[list-item]
        ],
    )
    with pytest.raises(GestureValidationError) as exc:
        validate_score_limits(score)
    assert exc.value.code == "joint_limit"


def test_velocity_cap_catches_pops() -> None:
    clip = expand_score(_score())
    track = clip["bones"]["head"]["data"]
    # Inject a 180° flip between two frames.
    track[40:44] = [0.0, 1.0, 0.0, 0.0]
    with pytest.raises(GestureValidationError) as exc:
        validate_clip(clip)
    assert exc.value.code == "too_fast"


@pytest.mark.parametrize(
    "kind,value", [("state", s) for s in STATE_TRIGGERS] + [("emote", e) for e in EMOTE_TRIGGERS]
)
def test_fallback_scores_validate_for_every_trigger(kind: str, value: str) -> None:
    ctx = CharacterContext(name="Ada", role="coder", species="fox", traits=["bold"])
    score = fallback_score(ctx, kind, value)
    validate_score_limits(score)
    validate_clip(expand_score(score))


def test_fallback_is_deterministic_per_character() -> None:
    a = fallback_score(CharacterContext(name="Ada"), "state", "celebrating")
    b = fallback_score(CharacterContext(name="Ada"), "state", "celebrating")
    c = fallback_score(CharacterContext(name="Bob"), "state", "celebrating")
    assert a == b
    assert a != c or a.name != c.name


def test_mood_emotes_are_valid_emote_triggers() -> None:
    assert set(MOOD_EMOTE.values()) <= set(EMOTE_TRIGGERS)


class StubClient(BaseLLMClient):
    def __init__(self, inputs: list[dict[str, Any] | None]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._inputs = list(inputs)

    async def create(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        inp = self._inputs.pop(0)
        if inp is None:
            return LLMResponse(text="I'd rather not", input_tokens=1, output_tokens=1)
        return LLMResponse(
            text="",
            input_tokens=1,
            output_tokens=1,
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="c1", name="compose_gesture", input=inp)],
        )


_GOOD = {
    "name": "fox flourish",
    "duration_s": 2.4,
    "segments": [
        {
            "family": "cheer",
            "side": "both",
            "start": 0.0,
            "duration": 1.8,
            "amplitude": 0.9,
            "tempo": 1.2,
        },
        {
            "family": "head_tilt",
            "side": "left",
            "start": 1.6,
            "duration": 0.8,
            "amplitude": 0.7,
            "tempo": 1.0,
        },
    ],
    "face": [{"expression": "happy", "start": 0.0, "duration": 2.4, "weight": 0.8}],
}


@pytest.mark.asyncio
async def test_compose_uses_llm_score_when_valid() -> None:
    client = StubClient([_GOOD])
    ctx = CharacterContext(
        name="Ada", role="coder", species="fox", traits=["bold"], catchphrase="go!"
    )
    result = await compose(client, ctx, "state", "celebrating", model="claude-haiku-5-5")
    assert result.source == "llm"
    assert result.score.name == "fox flourish"
    assert result.clip["frameCount"] == round(2.4 * 30) + 1
    call = client.calls[0]
    assert call["tools"][0].name == "compose_gesture"
    assert "Ada" in call["messages"][0]["content"]
    assert "celebrating" in call["messages"][0]["content"]
    assert call["effort"] == "low"


@pytest.mark.asyncio
async def test_compose_retries_with_feedback_then_falls_back() -> None:
    bad = {
        **_GOOD,
        "segments": [
            {
                "family": "arm_raise",
                "side": "right",
                "start": 0.0,
                "duration": 2.0,
                "amplitude": 1.0,
                "tempo": 1.0,
            },
            {
                "family": "wave",
                "side": "right",
                "start": 0.0,
                "duration": 2.0,
                "amplitude": 1.0,
                "tempo": 1.0,
            },
        ],
    }
    client = StubClient([bad, bad])
    result = await compose(client, CharacterContext(name="Ada"), "state", "thinking", model="m")
    assert result.source == "fallback"
    assert result.attempts == 2
    assert "rejected" in client.calls[1]["messages"][0]["content"]
    assert "joint_limit" in result.error or "exceeds" in result.error


@pytest.mark.asyncio
async def test_compose_without_client_is_procedural() -> None:
    result = await compose(None, CharacterContext(name="Ada"), "emote", "♪")
    assert result.source == "fallback" and result.attempts == 0
    validate_clip(result.clip)


@pytest.mark.asyncio
async def test_compose_handles_refusal_and_no_tool_call() -> None:
    client = StubClient([None, _GOOD])
    result = await compose(client, CharacterContext(name="Ada"), "state", "celebrating", model="m")
    assert result.source == "llm" and result.attempts == 2
    assert choreographer.TRIGGER_MEANING["state:celebrating"]
