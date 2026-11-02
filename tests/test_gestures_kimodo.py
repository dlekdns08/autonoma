"""AMASS (Kimodo SMPL-X) → motion-server contract conversion."""

from __future__ import annotations

import math

import pytest

from autonoma.gestures import kimodo
from autonoma.gestures.motion import SMPL22, retarget
from autonoma.gestures.validate import validate_clip


def _rot_vec(q: list[float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Rotate vector ``v`` by quaternion ``q`` (x, y, z, w)."""
    qv = [v[0], v[1], v[2], 0.0]
    x, y, z, w = q
    conj = [-x, -y, -z, w]
    r = kimodo.quat_mul(kimodo.quat_mul(q, qv), conj)
    return (round(r[0], 6), round(r[1], 6), round(r[2], 6))


def test_axis_angle_round_trip_and_identity() -> None:
    assert kimodo.axis_angle_to_quat([0, 0, 0]) == [0.0, 0.0, 0.0, 1.0]
    q = kimodo.axis_angle_to_quat([0, 0, math.pi / 2])  # 90° about z
    assert _rot_vec(q, (1, 0, 0)) == (0.0, 1.0, 0.0)


def test_world_fix_maps_amass_axes_onto_three_vrm_axes() -> None:
    # AMASS z-up → y-up, AMASS +y forward → +z forward.
    assert _rot_vec(kimodo.WORLD_FIX, (0, 0, 1)) == (0.0, 1.0, 0.0)
    assert _rot_vec(kimodo.WORLD_FIX, (0, 1, 0)) == (0.0, 0.0, 1.0)
    # Right-handed: x stays x only up to the 180° turn, which mirrors it.
    assert _rot_vec(kimodo.WORLD_FIX, (1, 0, 0)) == (-1.0, 0.0, 0.0)


def test_frame_to_quats_keeps_smpl_order_and_fixes_only_the_root() -> None:
    body = [0.0] * 63
    body[3 * 15 : 3 * 15 + 3] = [0.3, 0.0, 0.0]  # joint 16 = left_shoulder in SMPL22 (0-based)
    quats = kimodo.frame_to_quats([0.0, 0.0, 0.0], body)
    assert len(quats) == len(SMPL22) == 22
    assert quats[0] == pytest.approx(kimodo.WORLD_FIX)
    assert quats[16] == pytest.approx(kimodo.axis_angle_to_quat([0.3, 0.0, 0.0]))
    assert all(q == [0.0, 0.0, 0.0, 1.0] for i, q in enumerate(quats) if i not in (0, 16))


def test_resample_slerps_between_frames() -> None:
    a = [[0.0, 0.0, 0.0, 1.0]]
    b = [kimodo.axis_angle_to_quat([0, 0, math.pi / 2])]
    frames = kimodo.resample([a, b], src_fps=1, dst_fps=2)
    assert len(frames) == 3
    mid = frames[1][0]
    assert mid == pytest.approx(kimodo.axis_angle_to_quat([0, 0, math.pi / 4]), abs=1e-6)
    # Same rate → untouched; 60 → 30 halves the count.
    assert kimodo.resample([a, b], 30, 30) == [a, b]
    assert len(kimodo.resample([a] * 61, 60, 30)) == 31


def test_amass_to_contract_round_trips_through_retarget() -> None:
    # 2 s at 20 fps of a small, smooth arm raise.
    n = 41
    root = [[0.0, 0.0, 0.0] for _ in range(n)]
    body = []
    for i in range(n):
        frame = [0.0] * 63
        frame[3 * 16] = 0.6 * i / (n - 1)  # right_shoulder (joint 17) lifts over the clip
        body.append(frame)
    payload = kimodo.amass_to_contract(root, body, 20, fps=30)
    assert payload["skeleton"] == "smpl22" and payload["fps"] == 30
    assert len(payload["rotations"]) == 61 and len(payload["rotations"][0]) == 22
    clip = retarget(payload, name="raise")
    validate_clip(clip)
    assert "rightUpperArm" in clip["bones"] and "leftUpperLeg" not in clip["bones"]
    assert clip["durationS"] == pytest.approx(2.0)


def test_amass_to_contract_trims_to_max_duration_and_validates_shape() -> None:
    n = 10 * 30 + 1  # 10 s at 30 fps
    payload = kimodo.amass_to_contract([[0, 0, 0]] * n, [[0.0] * 63] * n, 30)
    assert len(payload["rotations"]) == int(kimodo.MAX_DURATION_S * 30) + 1
    with pytest.raises(ValueError):
        kimodo.amass_to_contract([[0, 0, 0]], [[0.0] * 63], 30)
    with pytest.raises(ValueError):
        kimodo.frame_to_quats([0, 0], [0.0] * 63)
