"""Procedural motion families.

Each family maps normalized time ``u`` (0..1 over the segment) to Euler
deltas per humanoid bone, in the bone axis conventions the stage uses
(``addBoneRotation`` in ``VRMCharacter.tsx``, Euler order YXZ):

- ``rightUpperArm.z`` positive raises the arm sideways/up (1.55 ≈ straight
  up); ``leftUpperArm.z`` is mirrored (negative raises).
- ``*UpperArm.x`` negative flexes the shoulder forward.
- ``rightLowerArm.z`` negative closes the elbow; ``leftLowerArm.z`` positive.
- ``head.x`` positive nods down, ``head.y`` turns, ``head.z`` tilts.
- ``chest.x`` positive leans forward.

The envelope (ease in / hold / ease out) is applied by the expander, so a
family only describes the *shape* at full amplitude; ``amp`` scales it
and ``tempo`` scales oscillation frequency. Families never exceed the
joint limits in ``validate.py`` at ``amp=1``.
"""

from __future__ import annotations

import math
from typing import Callable

Euler = tuple[float, float, float]
Pose = dict[str, Euler]
FamilyFn = Callable[[float, float, float, float, str], Pose]
# signature: (u, duration_s, amp, tempo, side) -> pose


def _mirror(side: str) -> list[tuple[str, float]]:
    """Which arm(s) to drive and the z-sign that raises that arm."""
    if side == "left":
        return [("left", -1.0)]
    if side == "both":
        return [("right", 1.0), ("left", -1.0)]
    return [("right", 1.0)]


def _add(pose: Pose, bone: str, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
    px, py, pz = pose.get(bone, (0.0, 0.0, 0.0))
    pose[bone] = (px + x, py + y, pz + z)


def _cycles(u: float, duration: float, base_hz: float, tempo: float) -> float:
    return math.sin(u * duration * base_hz * max(0.2, tempo) * 2 * math.pi)


# ── arm families ─────────────────────────────────────────────────────


def arm_raise(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Arm(s) straight up — 'YES!', 'over here'."""
    pose: Pose = {}
    for s, sign in _mirror(side):
        _add(pose, f"{s}UpperArm", x=-0.2 * amp, z=sign * 1.55 * amp)
    return pose


def wave(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Hand by the head, side-to-side sweep."""
    pose: Pose = {}
    osc = _cycles(u, d, 1.1, tempo)
    for s, sign in _mirror(side):
        _add(pose, f"{s}UpperArm", x=-0.8 * amp, y=-sign * osc * 0.5 * amp, z=sign * 1.55 * amp)
        _add(pose, f"{s}LowerArm", z=-sign * 0.95 * amp)
        _add(pose, f"{s}Hand", z=-sign * osc * 0.4 * amp)
    return pose


def point(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Arm straight forward — pointing at the viewer / a teammate."""
    pose: Pose = {}
    for s, sign in _mirror(side):
        _add(pose, f"{s}UpperArm", x=-1.25 * amp, z=sign * 0.15 * amp)
        _add(pose, f"{s}LowerArm", z=-sign * 0.1 * amp)
    _add(pose, "head", x=-0.04 * amp)
    return pose


def fist_pump(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Bent arm pumping up and down."""
    pose: Pose = {}
    pump = 0.5 + 0.5 * _cycles(u, d, 1.6, tempo)
    for s, sign in _mirror(side):
        _add(pose, f"{s}UpperArm", x=-(0.3 + 0.5 * pump) * amp, z=sign * 1.1 * amp)
        _add(pose, f"{s}LowerArm", z=-sign * (0.9 + 0.5 * pump) * amp)
    _add(pose, "chest", x=-0.05 * amp * pump)
    return pose


def hand_on_chin(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Classic 'hmm' — forearm up to the chin with a head tilt."""
    pose: Pose = {}
    sides = _mirror("right" if side == "both" else side)
    for s, sign in sides:
        _add(pose, f"{s}UpperArm", x=-0.55 * amp, z=sign * 0.85 * amp)
        _add(pose, f"{s}LowerArm", z=-sign * 1.0 * amp)
        _add(pose, "head", z=-sign * 0.08 * amp)
    return pose


def facepalm(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    pose: Pose = {}
    sides = _mirror("right" if side == "both" else side)
    for s, sign in sides:
        _add(pose, f"{s}UpperArm", x=-1.05 * amp, z=sign * 0.45 * amp)
        _add(pose, f"{s}LowerArm", z=-sign * 1.55 * amp)
    _add(pose, "head", x=0.16 * amp)
    _add(pose, "chest", x=0.05 * amp)
    return pose


def clap(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Both hands meet in front of the chest, repeatedly."""
    pose: Pose = {}
    beat = 0.5 + 0.5 * _cycles(u, d, 2.2, tempo)
    for s, sign in _mirror("both"):
        _add(
            pose,
            f"{s}UpperArm",
            x=-0.85 * amp,
            y=sign * (0.15 + 0.35 * beat) * amp,
            z=sign * 0.35 * amp,
        )
        _add(pose, f"{s}LowerArm", z=-sign * 1.0 * amp)
    return pose


def cheer(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Both arms up, alternating pumps — crowd energy."""
    pose: Pose = {}
    osc = _cycles(u, d, 1.4, tempo)
    for s, sign in _mirror("both"):
        alt = osc if s == "right" else -osc
        _add(pose, f"{s}UpperArm", x=-(0.2 + 0.15 * alt) * amp, z=sign * (1.45 + 0.1 * alt) * amp)
        _add(pose, f"{s}LowerArm", z=-sign * 0.35 * amp)
    _add(pose, "chest", x=-0.04 * amp)
    return pose


def hands_on_hips(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Elbows out, hands at the waist — confident / mischievous."""
    pose: Pose = {}
    for s, sign in _mirror("both"):
        _add(pose, f"{s}UpperArm", x=0.15 * amp, y=sign * 0.35 * amp, z=sign * 0.4 * amp)
        _add(pose, f"{s}LowerArm", y=sign * 0.4 * amp, z=-sign * 1.25 * amp)
    _add(pose, "chest", x=-0.05 * amp)
    return pose


def shrug(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    pose: Pose = {}
    for s, sign in _mirror("both"):
        _add(pose, f"{s}Shoulder", z=sign * 0.3 * amp)
        _add(pose, f"{s}UpperArm", x=-0.1 * amp, y=sign * 0.5 * amp, z=sign * 0.3 * amp)
        _add(pose, f"{s}LowerArm", z=-sign * 1.0 * amp)
    _add(pose, "head", z=0.1 * amp)
    return pose


# ── head / torso families ────────────────────────────────────────────


def nod(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    bob = 0.5 - 0.5 * math.cos(u * d * max(0.2, tempo) * 2.0 * 2 * math.pi)
    return {"head": (0.14 * amp * bob, 0.0, 0.0), "chest": (0.02 * amp * bob, 0.0, 0.0)}


def head_shake(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    return {"head": (0.0, 0.2 * amp * _cycles(u, d, 1.6, tempo), 0.0)}


def head_tilt(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    sign = 1.0 if side == "left" else -1.0
    return {"head": (0.02 * amp, 0.0, sign * 0.14 * amp)}


def lean(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    """Torso lean: side ``left``/``right`` tilt, ``both`` → forward; use
    ``bow`` for a deeper forward bend and ``lean_back`` for recoil."""
    if side == "left":
        return {"chest": (0.0, 0.0, 0.12 * amp), "head": (0.0, 0.0, 0.05 * amp)}
    if side == "right":
        return {"chest": (0.0, 0.0, -0.12 * amp), "head": (0.0, 0.0, -0.05 * amp)}
    return {"chest": (0.18 * amp, 0.0, 0.0), "head": (-0.03 * amp, 0.0, 0.0)}


def lean_back(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    return {"chest": (-0.16 * amp, 0.0, 0.0), "head": (-0.08 * amp, 0.0, 0.0)}


def bow(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    return {"chest": (0.3 * amp, 0.0, 0.0), "head": (0.12 * amp, 0.0, 0.0)}


def sway(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    osc = _cycles(u, d, 0.6, tempo)
    return {"spine": (0.0, 0.0, 0.05 * amp * osc), "head": (0.0, 0.0, -0.04 * amp * osc)}


def look_around(u: float, d: float, amp: float, tempo: float, side: str) -> Pose:
    turn = _cycles(u, d, 0.5, tempo)
    return {"head": (0.0, 0.45 * amp * turn, 0.0), "chest": (0.0, 0.1 * amp * turn, 0.0)}


FAMILIES: dict[str, FamilyFn] = {
    "arm_raise": arm_raise,
    "wave": wave,
    "point": point,
    "fist_pump": fist_pump,
    "hand_on_chin": hand_on_chin,
    "facepalm": facepalm,
    "clap": clap,
    "cheer": cheer,
    "hands_on_hips": hands_on_hips,
    "shrug": shrug,
    "nod": nod,
    "head_shake": head_shake,
    "head_tilt": head_tilt,
    "lean": lean,
    "lean_back": lean_back,
    "bow": bow,
    "sway": sway,
    "look_around": look_around,
}

FAMILY_DESCRIPTIONS: dict[str, str] = {
    "arm_raise": "arm(s) straight up — 'yes!', 'over here' (side: right|left|both)",
    "wave": "hand by the head, side-to-side sweep; tempo = wave speed",
    "point": "arm straight forward at the viewer or a teammate",
    "fist_pump": "bent arm pumping; tempo = pump speed",
    "hand_on_chin": "forearm up to the chin with a head tilt — 'hmm'",
    "facepalm": "palm to the face, head dips — exasperation",
    "clap": "both hands meet at the chest repeatedly; tempo = clap speed",
    "cheer": "both arms up, alternating pumps — crowd energy",
    "hands_on_hips": "elbows out, hands at the waist — confident, cheeky",
    "shrug": "shoulders up, forearms out, head tilt — 'dunno'",
    "nod": "head bobs down; tempo = number of nods per second-ish",
    "head_shake": "head turns side to side — 'no'",
    "head_tilt": "head tilts toward side (left|right)",
    "lean": "torso leans: side left|right tilts, both = forward",
    "lean_back": "torso recoils backward — surprise, 'whoa'",
    "bow": "deep forward bend with head dip — thanks, apology",
    "sway": "gentle side-to-side body sway — relaxed, humming",
    "look_around": "head turns around curiously; tempo = speed",
}
