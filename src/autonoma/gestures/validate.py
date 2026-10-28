"""Physical plausibility checks on an expanded clip.

Two layers: per-bone Euler limits sampled from the score (cheap, catches
stacked segments that over-rotate a joint) and per-frame angular
velocity on the final quaternion tracks (catches pops). Anything that
fails is rejected before storage; the choreographer gets the message
and retries once, after which the procedural fallback is used.
"""

from __future__ import annotations

import math
from typing import Any

from autonoma.gestures.dsl import GestureScore
from autonoma.gestures.expand import pose_at

# (min, max) radians per axis — generous but inside what the rigs tolerate.
_ARM_LIMITS = {"x": (-1.7, 0.6), "y": (-1.1, 1.1), "z": (-1.8, 1.8)}
_LIMITS: dict[str, dict[str, tuple[float, float]]] = {
    "head": {"x": (-0.5, 0.55), "y": (-0.9, 0.9), "z": (-0.5, 0.5)},
    "neck": {"x": (-0.3, 0.3), "y": (-0.5, 0.5), "z": (-0.3, 0.3)},
    "chest": {"x": (-0.35, 0.65), "y": (-0.4, 0.4), "z": (-0.35, 0.35)},
    "upperChest": {"x": (-0.3, 0.5), "y": (-0.3, 0.3), "z": (-0.3, 0.3)},
    "spine": {"x": (-0.3, 0.5), "y": (-0.3, 0.3), "z": (-0.3, 0.3)},
    "hips": {"x": (-0.2, 0.2), "y": (-0.3, 0.3), "z": (-0.2, 0.2)},
    "leftShoulder": {"x": (-0.4, 0.4), "y": (-0.4, 0.4), "z": (-0.5, 0.5)},
    "rightShoulder": {"x": (-0.4, 0.4), "y": (-0.4, 0.4), "z": (-0.5, 0.5)},
    "leftUpperArm": _ARM_LIMITS,
    "rightUpperArm": _ARM_LIMITS,
    # Elbows bend one way: right closes with negative z, left with positive.
    "leftLowerArm": {"x": (-0.5, 0.5), "y": (-1.2, 1.2), "z": (-0.1, 2.3)},
    "rightLowerArm": {"x": (-0.5, 0.5), "y": (-1.2, 1.2), "z": (-2.3, 0.1)},
    "leftHand": {"x": (-0.8, 0.8), "y": (-0.8, 0.8), "z": (-1.0, 1.0)},
    "rightHand": {"x": (-0.8, 0.8), "y": (-0.8, 0.8), "z": (-1.0, 1.0)},
}
# Max rotation between consecutive frames (rad). 0.4 rad/frame @30fps ≈ 12 rad/s —
# fast families at max tempo sit just under it; a real pop is 1-3 rad.
MAX_STEP_RAD = 0.4
# Max expression weight change between consecutive frames.
MAX_EXPR_STEP = 0.35


class GestureValidationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def validate_score_limits(score: GestureScore, *, samples: int = 48) -> None:
    """Sample the score and reject poses outside the joint limits."""
    for i in range(samples + 1):
        t = score.duration_s * i / samples
        pose, _ = pose_at(score, t)
        for bone, (x, y, z) in pose.items():
            limits = _LIMITS.get(bone)
            if limits is None:
                raise GestureValidationError("unknown_bone", f"family drives unknown bone {bone}")
            for axis, value in (("x", x), ("y", y), ("z", z)):
                lo, hi = limits[axis]
                if value < lo - 1e-6 or value > hi + 1e-6:
                    raise GestureValidationError(
                        "joint_limit",
                        f"{bone}.{axis}={value:.2f} rad at t={t:.2f}s exceeds [{lo}, {hi}] — "
                        "lower the amplitude or don't stack segments on the same arm",
                    )


def _quat_angle(a: list[float], b: list[float]) -> float:
    dot = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])
    return 2.0 * math.acos(min(1.0, dot))


def validate_clip(clip: dict[str, Any]) -> None:
    """Velocity + shape checks on the expanded wire-format clip."""
    frame_count = int(clip.get("frameCount", 0))
    if frame_count < 2:
        raise GestureValidationError("too_short")
    bones = clip.get("bones") or {}
    exprs = clip.get("expressions") or {}
    if not bones and not exprs:
        raise GestureValidationError("empty_clip")
    for bone, track in bones.items():
        data = track["data"]
        if len(data) != frame_count * 4:
            raise GestureValidationError("bad_track", f"{bone} track length")
        for i in range(1, frame_count):
            step = _quat_angle(data[(i - 1) * 4 : i * 4], data[i * 4 : (i + 1) * 4])
            if step > MAX_STEP_RAD:
                raise GestureValidationError(
                    "too_fast",
                    f"{bone} rotates {step:.2f} rad in one frame at frame {i} — "
                    "slow the tempo or lengthen the segment",
                )
    for expr, track in exprs.items():
        data = track["data"]
        if len(data) != frame_count:
            raise GestureValidationError("bad_track", f"{expr} track length")
        for i in range(1, frame_count):
            if abs(data[i] - data[i - 1]) > MAX_EXPR_STEP:
                raise GestureValidationError("too_fast", f"{expr} jumps at frame {i}")
