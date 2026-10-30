"""Text-to-motion backend for self-authored gestures (opt-in).

Motion-diffusion models need a GPU, so Autonoma talks to an external
*motion server* over HTTP and retargets its output onto the VRM humanoid.
Any model can sit behind the server as long as it honours this contract:

    POST {gesture_motion_url}
    {"text": "...", "duration_s": 2.5, "fps": 30}

    200 {"fps": 30,
         "skeleton": "smpl22",
         "rotations": [               # one entry per frame
            [[x, y, z, w], ... 22]    # LOCAL joint rotations, SMPL joint order
         ]}

Conventions: Y-up, the character faces +Z, rest pose is SMPL's T-pose
(identity rotations), quaternions are normalised — the same frame three-vrm
uses for normalized humanoid bones, so retargeting is a name mapping.
The root (pelvis) rotation is kept but root translation is not used (the
stage plays clips in place). The result goes through the same velocity
validator as procedural gestures; anything that fails is discarded and
the caller falls back to the DSL choreographer.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from autonoma.config import settings
from autonoma.gestures.validate import GestureValidationError, validate_clip

logger = logging.getLogger(__name__)

# SMPL joint order (22 body joints, as used by HumanML3D-style models).
SMPL22 = (
    "pelvis",
    "left_hip",
    "right_hip",
    "spine1",
    "left_knee",
    "right_knee",
    "spine2",
    "left_ankle",
    "right_ankle",
    "spine3",
    "left_foot",
    "right_foot",
    "neck",
    "left_collar",
    "right_collar",
    "head",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
)

SMPL_TO_VRM: dict[str, str] = {
    "pelvis": "hips",
    "spine1": "spine",
    "spine2": "chest",
    "spine3": "upperChest",
    "neck": "neck",
    "head": "head",
    "left_collar": "leftShoulder",
    "right_collar": "rightShoulder",
    "left_shoulder": "leftUpperArm",
    "right_shoulder": "rightUpperArm",
    "left_elbow": "leftLowerArm",
    "right_elbow": "rightLowerArm",
    "left_wrist": "leftHand",
    "right_wrist": "rightHand",
    "left_hip": "leftUpperLeg",
    "right_hip": "rightUpperLeg",
    "left_knee": "leftLowerLeg",
    "right_knee": "rightLowerLeg",
    "left_ankle": "leftFoot",
    "right_ankle": "rightFoot",
}

MAX_DURATION_S = 6.0


class MotionBackendError(RuntimeError):
    pass


def _normalise(q: list[float]) -> list[float]:
    if len(q) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in q):
        raise MotionBackendError("bad quaternion")
    n = math.sqrt(sum(v * v for v in q))
    if n < 1e-6:
        raise MotionBackendError("zero quaternion")
    return [round(v / n, 5) for v in q]


def retarget(payload: dict[str, Any], *, name: str, include_legs: bool = False) -> dict[str, Any]:
    """Motion-server payload → MocapClip v1 dict (bone tracks only).

    Legs are dropped by default: stage characters stand in a fixed spot
    and generated walk cycles would slide in place.
    """
    if payload.get("skeleton", "smpl22") != "smpl22":
        raise MotionBackendError(f"unsupported skeleton {payload.get('skeleton')!r}")
    fps = int(payload.get("fps") or 0)
    frames = payload.get("rotations")
    if not (1 <= fps <= 120) or not isinstance(frames, list) or len(frames) < 2:
        raise MotionBackendError("payload needs fps 1..120 and at least two frames")
    duration = (len(frames) - 1) / fps
    if duration > MAX_DURATION_S:
        frames = frames[: int(MAX_DURATION_S * fps) + 1]
        duration = (len(frames) - 1) / fps

    leg_bones = {
        "leftUpperLeg",
        "rightUpperLeg",
        "leftLowerLeg",
        "rightLowerLeg",
        "leftFoot",
        "rightFoot",
    }
    tracks: dict[str, list[float]] = {}
    for f_idx, frame in enumerate(frames):
        if not isinstance(frame, list) or len(frame) != len(SMPL22):
            raise MotionBackendError(f"frame {f_idx} must have {len(SMPL22)} joints")
        for joint, quat in zip(SMPL22, frame):
            bone = SMPL_TO_VRM.get(joint)
            if bone is None or (bone in leg_bones and not include_legs):
                continue
            tracks.setdefault(bone, []).extend(_normalise(quat))

    return {
        "version": 1,
        "id": "",
        "name": name,
        "sourceVrm": "generated.vrm",
        "durationS": round(duration, 4),
        "fps": fps,
        "frameCount": len(frames),
        "bones": {b: {"data": d} for b, d in tracks.items()},
        "expressions": {},
        "meta": {"generated": True, "backend": "motion"},
    }


async def text_to_motion(
    text: str,
    *,
    duration_s: float,
    name: str,
    transport: Any = None,
) -> dict[str, Any]:
    """Call the motion server and return a validated, retargeted clip."""
    url = settings.gesture_motion_url
    if not url:
        raise MotionBackendError("gesture_motion_url is not set")
    import httpx2

    async with httpx2.AsyncClient(
        timeout=settings.gesture_motion_timeout_s, transport=transport
    ) as http:
        resp = await http.post(
            url,
            json={"text": text, "duration_s": min(duration_s, MAX_DURATION_S), "fps": 30},
        )
    if resp.status_code != 200:
        raise MotionBackendError(f"motion server returned HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise MotionBackendError("motion server returned invalid JSON") from exc
    clip = retarget(payload, name=name)
    try:
        validate_clip(clip)
    except GestureValidationError as exc:
        raise MotionBackendError(f"generated motion rejected: {exc.detail or exc.code}") from exc
    return clip


def motion_prompt(trigger_meaning: str, *, traits: list[str], species: str, role: str) -> str:
    """Natural-language prompt for motion models (HumanML3D style)."""
    who = " ".join(t for t in (", ".join(traits), species) if t) or "a person"
    return (
        f"a {who} {role or 'character'} standing in place, {trigger_meaning}, expressive upper body"
    )
