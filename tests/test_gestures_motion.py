"""Text-to-motion backend: contract, retargeting, validation, fallback."""

from __future__ import annotations

import math
from typing import Any

import httpx2
import pytest

from autonoma.gestures import motion
from autonoma.gestures.choreographer import CharacterContext, compose
from autonoma.gestures.motion import SMPL22, MotionBackendError, retarget, text_to_motion


def _quat_z(angle: float) -> list[float]:
    return [0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2)]


def _payload(frames: int = 31, fps: int = 30, wave: float = 0.6) -> dict[str, Any]:
    rows = []
    for i in range(frames):
        t = i / (frames - 1)
        frame = [[0.0, 0.0, 0.0, 1.0] for _ in SMPL22]
        frame[SMPL22.index("right_shoulder")] = _quat_z(wave * math.sin(t * math.pi))
        frame[SMPL22.index("left_knee")] = _quat_z(0.3)
        rows.append(frame)
    return {"fps": fps, "skeleton": "smpl22", "rotations": rows}


def test_retarget_maps_smpl_to_vrm_and_drops_legs() -> None:
    clip = retarget(_payload(), name="wave")
    assert clip["version"] == 1 and clip["fps"] == 30 and clip["frameCount"] == 31
    assert clip["durationS"] == 1.0
    assert "rightUpperArm" in clip["bones"] and "hips" in clip["bones"]
    assert "leftLowerLeg" not in clip["bones"]
    assert len(clip["bones"]["rightUpperArm"]["data"]) == 31 * 4
    with_legs = retarget(_payload(), name="wave", include_legs=True)
    assert "leftLowerLeg" in with_legs["bones"]


@pytest.mark.parametrize(
    "bad",
    [
        {"fps": 30, "skeleton": "mixamo", "rotations": []},
        {"fps": 0, "rotations": [[[0, 0, 0, 1]] * 22] * 3},
        {"fps": 30, "rotations": [[[0, 0, 0, 1]] * 21] * 3},
        {"fps": 30, "rotations": [[[0, 0, 0, 0]] * 22] * 3},
        {"fps": 30, "rotations": [[[float("nan"), 0, 0, 1]] * 22] * 3},
    ],
)
def test_retarget_rejects_bad_payloads(bad: dict[str, Any]) -> None:
    with pytest.raises(MotionBackendError):
        retarget(bad, name="x")


def test_retarget_trims_overlong_motion() -> None:
    clip = retarget(_payload(frames=30 * 10 + 1), name="long")
    assert clip["durationS"] <= motion.MAX_DURATION_S


def _transport(
    payload: dict[str, Any] | None, status: int = 200, seen: list[dict[str, Any]] | None = None
):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            import json

            seen.append(json.loads(request.content))
        if payload is None:
            return httpx2.Response(status, text="oops")
        return httpx2.Response(status, json=payload)

    return httpx2.MockTransport(handler)


@pytest.mark.asyncio
async def test_text_to_motion_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(motion.settings, "gesture_motion_url", "http://motion.local/generate")
    seen: list[dict[str, Any]] = []
    clip = await text_to_motion(
        "a fox waves", duration_s=1.0, name="wave", transport=_transport(_payload(), seen=seen)
    )
    assert seen == [{"text": "a fox waves", "duration_s": 1.0, "fps": 30}]
    assert clip["bones"]["rightUpperArm"]


@pytest.mark.asyncio
async def test_text_to_motion_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(motion.settings, "gesture_motion_url", "")
    with pytest.raises(MotionBackendError):
        await text_to_motion("x", duration_s=1, name="x")
    monkeypatch.setattr(motion.settings, "gesture_motion_url", "http://motion.local/generate")
    with pytest.raises(MotionBackendError):
        await text_to_motion("x", duration_s=1, name="x", transport=_transport(None, status=500))
    # A 180° flip between frames fails the velocity validator.
    jerky = _payload()
    jerky["rotations"][10][SMPL22.index("head")] = [0.0, 1.0, 0.0, 0.0]
    with pytest.raises(MotionBackendError, match="rejected"):
        await text_to_motion("x", duration_s=1, name="x", transport=_transport(jerky))


@pytest.mark.asyncio
async def test_compose_prefers_motion_then_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(motion.settings, "gesture_motion_backend", "http")
    monkeypatch.setattr(motion.settings, "gesture_motion_url", "http://motion.local/generate")
    good_clip = retarget(_payload(), name="m")

    async def ok(*a: Any, **kw: Any) -> dict[str, Any]:
        return good_clip

    monkeypatch.setattr(motion, "text_to_motion", ok)
    ctx = CharacterContext(name="Ada", role="coder", species="fox", traits=["bold"])
    res = await compose(None, ctx, "state", "celebrating")
    assert res.source == "motion" and res.clip is good_clip

    async def down(*a: Any, **kw: Any) -> dict[str, Any]:
        raise MotionBackendError("connection refused")

    monkeypatch.setattr(motion, "text_to_motion", down)
    res = await compose(None, ctx, "state", "celebrating")
    assert res.source == "fallback" and "motion" in res.error


def test_motion_prompt_mentions_character_and_trigger() -> None:
    p = motion.motion_prompt("pure victory", traits=["bold"], species="fox", role="coder")
    assert "bold" in p and "fox" in p and "pure victory" in p and "standing in place" in p
