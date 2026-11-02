"""AMASS (SMPL-X) pose arrays → the Autonoma motion-server contract.

NVIDIA Kimodo's ``Kimodo-SMPLX-RP-v1`` checkpoint writes an AMASS NPZ:
``root_orient`` ``[T, 3]`` and ``pose_body`` ``[T, 63]`` as axis-angle in
SMPL-X joint order, whose first 22 joints are exactly :data:`SMPL22`.
This module turns those arrays into the ``{"fps", "skeleton": "smpl22",
"rotations": [[[x, y, z, w], …22], …]}`` payload that
:func:`autonoma.gestures.motion.retarget` consumes — pure Python, so the
same code is unit-tested here and run by ``scripts/motion_server_kimodo.py``
next to the model.

Frames: AMASS is z-up / +y-forward, the contract (three-vrm) is y-up /
+z-forward. Only the root orientation lives in the world frame — child
joints are parent-relative — so the fix is one constant rotation applied
to the pelvis. Rest pose is SMPL's T-pose in both (all-zero axis-angle).
"""

from __future__ import annotations

import math
from typing import Sequence

from autonoma.gestures.motion import MAX_DURATION_S, SMPL22

Quat = list[float]  # [x, y, z, w]


def axis_angle_to_quat(v: Sequence[float]) -> Quat:
    x, y, z = (float(c) for c in v)
    angle = math.sqrt(x * x + y * y + z * z)
    if angle < 1e-8:
        return [0.0, 0.0, 0.0, 1.0]
    s = math.sin(angle / 2) / angle
    return [x * s, y * s, z * s, math.cos(angle / 2)]


def quat_mul(a: Quat, b: Quat) -> Quat:
    """Hamilton product ``a ⊗ b`` (apply ``b`` first, then ``a``)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]


def _normalise(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return [c / n for c in q]


def slerp(a: Quat, b: Quat, t: float) -> Quat:
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0.0:  # take the short arc
        b = [-c for c in b]
        dot = -dot
    if dot > 0.9995:
        return _normalise([x + (y - x) * t for x, y in zip(a, b)])
    theta0 = math.acos(max(-1.0, min(1.0, dot)))
    theta = theta0 * t
    s0 = math.cos(theta) - dot * math.sin(theta) / math.sin(theta0)
    s1 = math.sin(theta) / math.sin(theta0)
    return [s0 * x + s1 * y for x, y in zip(a, b)]


def _axis_quat(axis: tuple[float, float, float], degrees: float) -> Quat:
    h = math.radians(degrees) / 2
    s = math.sin(h)
    return [axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h)]


# AMASS (z-up, +y forward) → contract (y-up, +z forward):
# Rx(-90°) maps z-up to y-up and +y to -z; Ry(180°) then turns the
# character to face +z.
WORLD_FIX: Quat = quat_mul(_axis_quat((0.0, 1.0, 0.0), 180.0), _axis_quat((1.0, 0.0, 0.0), -90.0))


def frame_to_quats(root_orient: Sequence[float], pose_body: Sequence[float]) -> list[Quat]:
    """One AMASS frame (3 + 63 axis-angle values) → 22 quaternions."""
    if len(root_orient) != 3 or len(pose_body) < 63:
        raise ValueError("expected root_orient[3] and pose_body[63]")
    quats = [quat_mul(WORLD_FIX, axis_angle_to_quat(root_orient))]
    for j in range(21):
        quats.append(axis_angle_to_quat(pose_body[3 * j : 3 * j + 3]))
    return quats


def resample(frames: list[list[Quat]], src_fps: float, dst_fps: float) -> list[list[Quat]]:
    """Resample quaternion frames with per-joint slerp."""
    if not frames or src_fps <= 0 or dst_fps <= 0:
        return frames
    if abs(src_fps - dst_fps) < 1e-6:
        return frames
    duration = (len(frames) - 1) / src_fps
    count = int(round(duration * dst_fps)) + 1
    out: list[list[Quat]] = []
    for i in range(count):
        pos = min(i / dst_fps, duration) * src_fps
        lo = min(int(math.floor(pos)), len(frames) - 1)
        hi = min(lo + 1, len(frames) - 1)
        t = pos - lo
        out.append([slerp(a, b, t) if hi != lo else list(a) for a, b in zip(frames[lo], frames[hi])])
    return out


def amass_to_contract(
    root_orient: Sequence[Sequence[float]],
    pose_body: Sequence[Sequence[float]],
    src_fps: float,
    *,
    fps: int = 30,
    max_duration_s: float = MAX_DURATION_S,
) -> dict:
    """Build the motion-server response payload."""
    if len(root_orient) != len(pose_body) or len(root_orient) < 2:
        raise ValueError("root_orient and pose_body need the same length (≥ 2 frames)")
    frames = [frame_to_quats(r, p) for r, p in zip(root_orient, pose_body)]
    frames = resample(frames, float(src_fps), float(fps))
    limit = int(max_duration_s * fps) + 1
    frames = frames[:limit]
    return {
        "fps": int(fps),
        "skeleton": "smpl22",
        "joints": list(SMPL22),
        "rotations": [[[round(c, 5) for c in q] for q in frame] for frame in frames],
    }
