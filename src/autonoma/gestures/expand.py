"""Score → mocap clip frames.

Produces the exact wire format the stage plays (``MocapClip`` v1 in
``web/src/lib/mocap/clipFormat.ts``): uniform frames at ``fps``, a flat
``[x,y,z,w]`` quaternion track per driven bone, scalar tracks per driven
expression. Bones no segment touches are left out, so the idle loop
keeps driving them (layered playback).
"""

from __future__ import annotations

import math
from typing import Any

from autonoma.gestures.dsl import GestureScore
from autonoma.gestures.families import FAMILIES, Euler

DEFAULT_FPS = 30


def _smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def envelope(u: float, *, attack: float = 0.25, release: float = 0.3) -> float:
    """Ease in over ``attack`` of the segment, hold, ease out over ``release``."""
    if u <= 0.0 or u >= 1.0:
        return 0.0
    rise = _smoothstep(u / attack) if attack > 0 else 1.0
    fall = _smoothstep((1.0 - u) / release) if release > 0 else 1.0
    return min(rise, fall)


def euler_yxz_to_quat(x: float, y: float, z: float) -> tuple[float, float, float, float]:
    """three.js ``Quaternion.setFromEuler`` for order ``YXZ`` (the order
    ``addBoneRotation`` uses on the stage)."""
    c1, c2, c3 = math.cos(x / 2), math.cos(y / 2), math.cos(z / 2)
    s1, s2, s3 = math.sin(x / 2), math.sin(y / 2), math.sin(z / 2)
    qx = s1 * c2 * c3 + c1 * s2 * s3
    qy = c1 * s2 * c3 - s1 * c2 * s3
    qz = c1 * c2 * s3 - s1 * s2 * c3
    qw = c1 * c2 * c3 + s1 * s2 * s3
    return (qx, qy, qz, qw)


def pose_at(score: GestureScore, t: float) -> tuple[dict[str, Euler], dict[str, float]]:
    """Accumulated Euler per bone and expression weights at time ``t``."""
    bones: dict[str, list[float]] = {}
    for seg in score.segments:
        u = (t - seg.start) / seg.duration
        env = envelope(u)
        if env <= 0.0:
            continue
        fn = FAMILIES[seg.family]
        for bone, (ex, ey, ez) in fn(u, seg.duration, seg.amplitude, seg.tempo, seg.side).items():
            acc = bones.setdefault(bone, [0.0, 0.0, 0.0])
            acc[0] += ex * env
            acc[1] += ey * env
            acc[2] += ez * env
    faces: dict[str, float] = {}
    for cue in score.face:
        u = (t - cue.start) / cue.duration
        env = envelope(u, attack=0.2, release=0.35)
        if env <= 0.0:
            continue
        faces[cue.expression] = min(1.0, faces.get(cue.expression, 0.0) + cue.weight * env)
    return {b: (v[0], v[1], v[2]) for b, v in bones.items()}, faces


def driven_bones(score: GestureScore) -> set[str]:
    """Every bone any segment can touch (sampled at its peak)."""
    out: set[str] = set()
    for seg in score.segments:
        fn = FAMILIES[seg.family]
        for u in (0.25, 0.5, 0.75):
            out.update(fn(u, seg.duration, seg.amplitude, seg.tempo, seg.side).keys())
    return out


def expand_score(
    score: GestureScore,
    *,
    fps: int = DEFAULT_FPS,
    source_vrm: str = "generated.vrm",
    clip_id: str = "",
) -> dict[str, Any]:
    frame_count = round(score.duration_s * fps) + 1
    bone_names = sorted(driven_bones(score))
    expr_names = sorted({cue.expression for cue in score.face})
    bone_tracks: dict[str, list[float]] = {b: [] for b in bone_names}
    expr_tracks: dict[str, list[float]] = {e: [] for e in expr_names}

    for i in range(frame_count):
        t = i / fps
        pose, faces = pose_at(score, t)
        for b in bone_names:
            x, y, z = pose.get(b, (0.0, 0.0, 0.0))
            bone_tracks[b].extend(euler_yxz_to_quat(x, y, z))
        for e in expr_names:
            expr_tracks[e].append(round(faces.get(e, 0.0), 4))

    return {
        "version": 1,
        "id": clip_id,
        "name": score.name,
        "sourceVrm": source_vrm,
        "durationS": round(score.duration_s, 4),
        "fps": fps,
        "frameCount": frame_count,
        "bones": {b: {"data": [round(v, 5) for v in data]} for b, data in bone_tracks.items()},
        "expressions": {e: {"data": data} for e, data in expr_tracks.items()},
        "meta": {"generated": True},
    }
