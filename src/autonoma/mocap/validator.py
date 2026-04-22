"""Server-side validation of uploaded motion-capture clip payloads.

The client compresses a ``MocapClip`` (version=1) JSON with gzip and
base64-encodes it before POSTing. We decompress here, re-parse, and
cross-check every shape invariant so malformed or adversarial payloads
cannot slip into the DB and crash the playback runtime at render time.

Checks applied:
- Decompressed size ≤ ``MAX_PAYLOAD_SIZE_BYTES``.
- ``version == 1``.
- ``fps`` is a positive int (1..120) and ``durationS`` > 0.
- ``frameCount`` matches ``round(durationS * fps) + 1`` within a small
  tolerance (±1 frame for FP rounding).
- Every bone track is named in ``ALLOWED_BONES``, its ``data`` array has
  length ``frameCount * 4`` (quaternions), and every quaternion is
  finite + not NaN.
- Every expression track is named in ``ALLOWED_EXPRESSIONS``, its
  ``data`` array has length ``frameCount``, and values are finite.
"""

from __future__ import annotations

import base64
import gzip
import json
import math
from dataclasses import dataclass
from typing import Any


ALLOWED_BONES: frozenset[str] = frozenset(
    [
        "hips",
        "spine",
        "chest",
        "upperChest",
        "neck",
        "head",
        "leftShoulder",
        "rightShoulder",
        "leftUpperArm",
        "rightUpperArm",
        "leftLowerArm",
        "rightLowerArm",
        "leftHand",
        "rightHand",
    ]
)

ALLOWED_EXPRESSIONS: frozenset[str] = frozenset(
    [
        "happy",
        "angry",
        "sad",
        "relaxed",
        "surprised",
        "aa",
        "ih",
        "ou",
        "ee",
        "oh",
        "blink",
        "blinkLeft",
        "blinkRight",
    ]
)

# Decompressed (pre-JSON) size cap. A 10-second clip at 30fps with all
# bones + expressions is ~180 KB un-gzipped. 512 KB gives comfortable
# headroom without letting abusers plant multi-megabyte blobs.
MAX_PAYLOAD_SIZE_BYTES = 512 * 1024


class MocapValidationError(ValueError):
    """Raised when a payload fails validation. ``code`` is a short
    machine-readable identifier the API layer maps to an HTTP status."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class ValidatedClip:
    payload_gz: bytes  # original compressed bytes (kept for storage)
    decoded: dict[str, Any]
    size_bytes: int
    duration_s: float
    fps: int
    frame_count: int
    name: str
    source_vrm: str


def _is_finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_quat_track(name: str, track: Any, frame_count: int) -> None:
    if not isinstance(track, dict):
        raise MocapValidationError("bad_bone_track", f"bone '{name}' not an object")
    data = track.get("data")
    if not isinstance(data, list):
        raise MocapValidationError("bad_bone_data", f"bone '{name}' data not a list")
    if len(data) != frame_count * 4:
        raise MocapValidationError(
            "bone_length_mismatch",
            f"bone '{name}' has {len(data)} floats; expected {frame_count * 4}",
        )
    # Sample-check values: check every value but stop early on first fault.
    for i, v in enumerate(data):
        if not _is_finite_number(v):
            raise MocapValidationError(
                "bone_bad_value",
                f"bone '{name}' index {i} is not a finite number",
            )


def _check_scalar_track(name: str, track: Any, frame_count: int) -> None:
    if not isinstance(track, dict):
        raise MocapValidationError(
            "bad_expression_track", f"expression '{name}' not an object"
        )
    data = track.get("data")
    if not isinstance(data, list):
        raise MocapValidationError(
            "bad_expression_data", f"expression '{name}' data not a list"
        )
    if len(data) != frame_count:
        raise MocapValidationError(
            "expression_length_mismatch",
            f"expression '{name}' has {len(data)} values; expected {frame_count}",
        )
    for i, v in enumerate(data):
        if not _is_finite_number(v):
            raise MocapValidationError(
                "expression_bad_value",
                f"expression '{name}' index {i} is not a finite number",
            )


def validate_payload(
    payload_gz_b64: str,
    *,
    name: str,
    source_vrm: str,
    expected_size_bytes: int | None = None,
) -> ValidatedClip:
    """Decode + validate + return the structured clip for storage.

    Caller supplies ``payload_gz_b64`` (base64-encoded gzip of the JSON
    string). On success the original compressed bytes are returned in
    ``ValidatedClip.payload_gz`` so the DB row stores exactly what the
    client uploaded (allows perfect-fidelity re-emit to other clients).
    """
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 128):
        raise MocapValidationError("invalid_name", "name length 1..128 required")
    if not isinstance(source_vrm, str) or not source_vrm.endswith(".vrm"):
        raise MocapValidationError("invalid_source_vrm", "source_vrm must end in .vrm")
    if not isinstance(payload_gz_b64, str) or not payload_gz_b64:
        raise MocapValidationError("missing_payload")

    try:
        payload_gz = base64.b64decode(payload_gz_b64, validate=True)
    except Exception:
        raise MocapValidationError("bad_base64", "payload_gz_b64 is not valid base64")

    # Guard against zip-bomb-style inflation before we inflate.
    if len(payload_gz) > MAX_PAYLOAD_SIZE_BYTES:
        raise MocapValidationError("payload_too_large")

    try:
        raw = gzip.decompress(payload_gz)
    except OSError:
        raise MocapValidationError("bad_gzip")

    if len(raw) > MAX_PAYLOAD_SIZE_BYTES:
        raise MocapValidationError(
            "payload_too_large",
            f"decompressed {len(raw)} bytes exceeds {MAX_PAYLOAD_SIZE_BYTES}",
        )
    if expected_size_bytes is not None and abs(len(raw) - expected_size_bytes) > 128:
        # Small drift is fine (whitespace rounding), large drift is
        # suspicious and likely means a different payload was uploaded.
        raise MocapValidationError("size_mismatch")

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        raise MocapValidationError("bad_json")

    if not isinstance(decoded, dict):
        raise MocapValidationError("bad_shape", "clip must be an object")
    if decoded.get("version") != 1:
        raise MocapValidationError("unsupported_version")

    fps = decoded.get("fps")
    if not isinstance(fps, int) or not (1 <= fps <= 120):
        raise MocapValidationError("bad_fps", "fps must be an integer in 1..120")

    duration_s = decoded.get("durationS")
    if not _is_finite_number(duration_s) or duration_s <= 0:
        raise MocapValidationError("bad_duration")
    if duration_s > 60:
        raise MocapValidationError("clip_too_long", "max duration 60s")

    frame_count = decoded.get("frameCount")
    if not isinstance(frame_count, int) or frame_count < 2:
        raise MocapValidationError("bad_frame_count")
    expected_frames = round(duration_s * fps) + 1
    if abs(frame_count - expected_frames) > 1:
        raise MocapValidationError(
            "frame_count_mismatch",
            f"frameCount {frame_count} inconsistent with {duration_s}s @ {fps}fps",
        )

    bones = decoded.get("bones") or {}
    if not isinstance(bones, dict):
        raise MocapValidationError("bad_bones_map")
    for bone_name, track in bones.items():
        if bone_name not in ALLOWED_BONES:
            raise MocapValidationError("unknown_bone", f"bone '{bone_name}'")
        _check_quat_track(bone_name, track, frame_count)

    expressions = decoded.get("expressions") or {}
    if not isinstance(expressions, dict):
        raise MocapValidationError("bad_expressions_map")
    for expr_name, track in expressions.items():
        if expr_name not in ALLOWED_EXPRESSIONS:
            raise MocapValidationError("unknown_expression", f"expression '{expr_name}'")
        _check_scalar_track(expr_name, track, frame_count)

    if not bones and not expressions:
        raise MocapValidationError("empty_clip", "clip has no tracks")

    return ValidatedClip(
        payload_gz=payload_gz,
        decoded=decoded,
        size_bytes=len(raw),
        duration_s=float(duration_s),
        fps=fps,
        frame_count=frame_count,
        name=name.strip(),
        source_vrm=source_vrm,
    )
