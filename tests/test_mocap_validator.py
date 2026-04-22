"""Unit tests for ``autonoma.mocap.validator.validate_payload``.

Covers every ``MocapValidationError`` code plus happy paths including
the new finger bones. Pure-function tests — no DB, no async.
"""

from __future__ import annotations

import base64
import gzip
import json
import math

import pytest

from autonoma.mocap.validator import (
    MAX_PAYLOAD_SIZE_BYTES,
    MocapValidationError,
    validate_payload,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _build_clip(**overrides) -> dict:
    """Minimal valid clip dict (version=1, 1 bone + 1 expression).

    Override any top-level key via kwargs. ``bones`` / ``expressions``
    are replaced wholesale if supplied so tests can shape them freely.
    """
    fps = overrides.pop("fps", 30)
    duration_s = overrides.pop("durationS", 1.0)
    # Lazy defaults — Python evaluates the ``dict.pop`` default argument
    # eagerly, which would crash with ``round(1.0 * "30") + 1`` in the
    # negative-fps tests where callers intentionally pass a non-numeric
    # fps to exercise the validator.
    if "frameCount" in overrides:
        frame_count = overrides.pop("frameCount")
    else:
        frame_count = round(duration_s * fps) + 1

    if "bones" in overrides:
        bones = overrides.pop("bones")
    else:
        bones = {"hips": {"data": [0.0, 0.0, 0.0, 1.0] * frame_count}}
    if "expressions" in overrides:
        expressions = overrides.pop("expressions")
    else:
        expressions = {"happy": {"data": [0.0] * frame_count}}
    clip = {
        "version": 1,
        "fps": fps,
        "durationS": duration_s,
        "frameCount": frame_count,
        "bones": bones,
        "expressions": expressions,
    }
    clip.update(overrides)
    return clip


def _gzip_b64(data: dict | bytes | str) -> str:
    """Serialize → gzip → base64, matching the frontend pipeline."""
    if isinstance(data, dict):
        raw = json.dumps(data).encode("utf-8")
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = data
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def _call(
    clip: dict | None = None,
    *,
    payload_b64: str | None = None,
    name: str = "clip",
    source_vrm: str = "midori.vrm",
    expected_size_bytes: int | None = None,
):
    if payload_b64 is None:
        assert clip is not None
        payload_b64 = _gzip_b64(clip)
    return validate_payload(
        payload_b64,
        name=name,
        source_vrm=source_vrm,
        expected_size_bytes=expected_size_bytes,
    )


# ── Happy paths ──────────────────────────────────────────────────────


def test_happy_minimal_valid_clip() -> None:
    clip = _build_clip()
    result = _call(clip)
    assert result.name == "clip"
    assert result.source_vrm == "midori.vrm"
    assert result.fps == 30
    assert result.frame_count == 31
    assert result.duration_s == 1.0
    assert result.decoded["version"] == 1
    # payload_gz matches re-compression round-trip by content, not byte-equal
    assert gzip.decompress(result.payload_gz) == json.dumps(clip).encode("utf-8")


def test_happy_includes_new_finger_bones() -> None:
    fps, dur = 30, 1.0
    frames = round(dur * fps) + 1
    bones = {
        name: {"data": [0.0, 0.0, 0.0, 1.0] * frames}
        for name in (
            "leftIndexIntermediate",
            "leftThumbDistal",
            "rightMiddleDistal",
            "rightLittleIntermediate",
        )
    }
    clip = _build_clip(bones=bones)
    result = _call(clip)
    assert set(result.decoded["bones"].keys()) == set(bones.keys())


def test_happy_name_is_stripped() -> None:
    clip = _build_clip()
    result = _call(clip, name="  padded  ")
    assert result.name == "padded"


# ── invalid_name ─────────────────────────────────────────────────────


def test_invalid_name_empty() -> None:
    clip = _build_clip()
    with pytest.raises(MocapValidationError) as exc:
        _call(clip, name="")
    assert exc.value.code == "invalid_name"


def test_invalid_name_too_long() -> None:
    clip = _build_clip()
    with pytest.raises(MocapValidationError) as exc:
        _call(clip, name="x" * 129)
    assert exc.value.code == "invalid_name"


# ── invalid_source_vrm ───────────────────────────────────────────────


def test_invalid_source_vrm_missing_suffix() -> None:
    clip = _build_clip()
    with pytest.raises(MocapValidationError) as exc:
        _call(clip, source_vrm="midori")
    assert exc.value.code == "invalid_source_vrm"


# ── missing_payload ──────────────────────────────────────────────────


def test_missing_payload_empty_string() -> None:
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64="")
    assert exc.value.code == "missing_payload"


# ── bad_base64 ───────────────────────────────────────────────────────


def test_bad_base64() -> None:
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64="this is not base64 @@@!!!")
    assert exc.value.code == "bad_base64"


# ── payload_too_large ─────────────────────────────────────────────────


def test_payload_too_large_pre_inflate() -> None:
    # Arbitrary base64 payload > MAX; doesn't need to be valid gzip because
    # the size check runs before decompression.
    blob = b"\x00" * (MAX_PAYLOAD_SIZE_BYTES + 1)
    b64 = base64.b64encode(blob).decode("ascii")
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64=b64)
    assert exc.value.code == "payload_too_large"


def test_payload_too_large_post_inflate() -> None:
    # Compressible repeating bytes so the gzip stays small but the
    # inflated payload exceeds the cap.
    raw = b"A" * (MAX_PAYLOAD_SIZE_BYTES + 1)
    b64 = base64.b64encode(gzip.compress(raw)).decode("ascii")
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64=b64)
    assert exc.value.code == "payload_too_large"


# ── bad_gzip ─────────────────────────────────────────────────────────


def test_bad_gzip_valid_base64_but_not_gzipped() -> None:
    # Valid base64 wrapping plain JSON (no gzip magic bytes).
    b64 = base64.b64encode(b'{"hello":1}').decode("ascii")
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64=b64)
    assert exc.value.code == "bad_gzip"


# ── size_mismatch ────────────────────────────────────────────────────


def test_size_mismatch() -> None:
    clip = _build_clip()
    raw = json.dumps(clip).encode("utf-8")
    # Declare a size off by >128 bytes.
    bogus = len(raw) + 1024
    with pytest.raises(MocapValidationError) as exc:
        _call(clip, expected_size_bytes=bogus)
    assert exc.value.code == "size_mismatch"


def test_size_mismatch_small_drift_accepted() -> None:
    clip = _build_clip()
    raw = json.dumps(clip).encode("utf-8")
    # Off by 1 byte — below the 128-byte tolerance.
    result = _call(clip, expected_size_bytes=len(raw) + 1)
    assert result.size_bytes == len(raw)


# ── bad_json ─────────────────────────────────────────────────────────


def test_bad_json() -> None:
    # Valid gzip of broken JSON.
    b64 = _gzip_b64("{not valid json")
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64=b64)
    assert exc.value.code == "bad_json"


# ── bad_shape ────────────────────────────────────────────────────────


def test_bad_shape_not_a_dict() -> None:
    b64 = _gzip_b64(json.dumps([1, 2, 3]))
    with pytest.raises(MocapValidationError) as exc:
        _call(payload_b64=b64)
    assert exc.value.code == "bad_shape"


# ── unsupported_version ──────────────────────────────────────────────


def test_unsupported_version() -> None:
    clip = _build_clip(version=2)
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "unsupported_version"


# ── bad_fps ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, 121, "30"])
def test_bad_fps(bad) -> None:
    # Supply a concrete frameCount + explicit tracks so the helper
    # doesn't attempt arithmetic with non-numeric fps values.
    clip = _build_clip(
        fps=bad,
        frameCount=31,
        bones={"hips": {"data": [0.0, 0.0, 0.0, 1.0] * 31}},
        expressions={"happy": {"data": [0.0] * 31}},
    )
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bad_fps"


# ── bad_duration ─────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, float("inf")])
def test_bad_duration(bad) -> None:
    # Supply a concrete frameCount so _build_clip's default computation
    # doesn't try round(inf * fps).
    clip = _build_clip(durationS=bad, frameCount=31)
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bad_duration"


# ── clip_too_long ────────────────────────────────────────────────────


def test_clip_too_long() -> None:
    # Duration beyond 60s cap — frameCount kept consistent so we hit
    # the duration check before the frame_count check.
    fps = 30
    dur = 61.0
    frames = round(dur * fps) + 1
    clip = _build_clip(
        durationS=dur,
        frameCount=frames,
        fps=fps,
        bones={"hips": {"data": [0.0, 0.0, 0.0, 1.0] * frames}},
        expressions={},
    )
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "clip_too_long"


# ── bad_frame_count ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, 1, "31"])
def test_bad_frame_count(bad) -> None:
    # Supply bones/expressions explicitly so _build_clip doesn't try to
    # multiply a list by a non-int frameCount like "31".
    clip = _build_clip(
        frameCount=bad,
        bones={"hips": {"data": [0.0, 0.0, 0.0, 1.0]}},
        expressions={"happy": {"data": [0.0]}},
    )
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bad_frame_count"


# ── frame_count_mismatch ─────────────────────────────────────────────


def test_frame_count_mismatch() -> None:
    # fps=30 dur=1.0 → expected 31; declare 100 to blow the ±1 tolerance.
    clip = _build_clip(
        frameCount=100,
        bones={"hips": {"data": [0.0, 0.0, 0.0, 1.0] * 100}},
        expressions={"happy": {"data": [0.0] * 100}},
    )
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "frame_count_mismatch"


# ── bad_bones_map ────────────────────────────────────────────────────


def test_bad_bones_map_not_dict() -> None:
    clip = _build_clip(bones=[1, 2, 3])
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bad_bones_map"


# ── unknown_bone ─────────────────────────────────────────────────────


def test_unknown_bone() -> None:
    clip = _build_clip(
        bones={"tail": {"data": [0.0, 0.0, 0.0, 1.0] * 31}},
    )
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "unknown_bone"


# ── bone_length_mismatch ─────────────────────────────────────────────


def test_bone_length_mismatch() -> None:
    # frame_count 31 → expected data length 124; provide 120.
    clip = _build_clip(
        bones={"hips": {"data": [0.0] * 120}},
    )
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bone_length_mismatch"


# ── bone_bad_value ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_bone_bad_value(bad) -> None:
    frames = 31
    data = [0.0, 0.0, 0.0, 1.0] * frames
    data[5] = bad
    # NaN/Infinity aren't JSON-legal by default but json.dumps(allow_nan=True)
    # emits them. We round-trip via Python's serializer to make sure
    # json.loads on the server side still produces NaN/Inf so the
    # validator's finite-check fires.
    clip = _build_clip(bones={"hips": {"data": data}})
    # json.dumps allow_nan defaults True — good; json.loads default also
    # accepts NaN/Infinity tokens, so this round-trips cleanly.
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bone_bad_value"
    assert math.isnan(bad) or math.isinf(bad)


# ── bad_expressions_map ──────────────────────────────────────────────


def test_bad_expressions_map_not_dict() -> None:
    clip = _build_clip(expressions=[1, 2])
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "bad_expressions_map"


# ── unknown_expression ───────────────────────────────────────────────


def test_unknown_expression() -> None:
    clip = _build_clip(expressions={"tail_wag": {"data": [0.0] * 31}})
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "unknown_expression"


# ── expression_length_mismatch ───────────────────────────────────────


def test_expression_length_mismatch() -> None:
    clip = _build_clip(expressions={"happy": {"data": [0.0] * 10}})
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "expression_length_mismatch"


# ── expression_bad_value ─────────────────────────────────────────────


def test_expression_bad_value() -> None:
    data = [0.0] * 31
    data[4] = float("nan")
    clip = _build_clip(expressions={"happy": {"data": data}})
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "expression_bad_value"


# ── empty_clip ───────────────────────────────────────────────────────


def test_empty_clip_no_bones_no_expressions() -> None:
    clip = _build_clip(bones={}, expressions={})
    with pytest.raises(MocapValidationError) as exc:
        _call(clip)
    assert exc.value.code == "empty_clip"
