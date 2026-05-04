"""Unit tests for the VMC/OSC bridge (Feature #16)."""

from __future__ import annotations

import socket
import struct

import pytest

from autonoma.vmc import (
    MOOD_BLENDSHAPE_PRESETS,
    VMCBridge,
    encode_osc_bundle,
    encode_osc_message,
    mood_to_blendshapes,
)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


def test_encode_osc_message_padding_and_layout() -> None:
    """OSC messages must always be a multiple of 4 bytes."""
    pkt = encode_osc_message("/test", 1.0)
    assert len(pkt) % 4 == 0

    # "/test\0" is 6 bytes — padded to 8.
    assert pkt[:8] == b"/test\x00\x00\x00"
    # Type-tag string ",f\0\0" = 4 bytes.
    assert pkt[8:12] == b",f\x00\x00"
    # Payload: big-endian float32 1.0 = 0x3F800000.
    assert struct.unpack(">f", pkt[12:16])[0] == 1.0


def test_encode_osc_message_supports_mixed_types() -> None:
    pkt = encode_osc_message("/x", "name", 1, 2.5)
    # Address "/x\0\0" (4) + tags ",sif\0\0\0\0" (8) + "name\0\0\0\0" (8)
    # + int32 (4) + float32 (4) = 28 bytes, divisible by 4.
    assert len(pkt) == 28
    assert pkt[:4] == b"/x\x00\x00"
    assert pkt[4:12] == b",sif\x00\x00\x00\x00"


def test_encode_osc_message_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        encode_osc_message("/x", object())


def test_encode_osc_bundle_header_and_immediate_timetag() -> None:
    msg = encode_osc_message("/a", 1.0)
    bundle = encode_osc_bundle([msg])
    # "#bundle\0" + 8-byte timetag + int32 size + msg
    assert bundle[:8] == b"#bundle\x00"
    assert bundle[8:16] == struct.pack(">II", 0, 1)  # immediate
    size = struct.unpack(">i", bundle[16:20])[0]
    assert size == len(msg)
    assert bundle[20:20 + size] == msg


def test_encode_osc_bundle_concatenates_multiple_messages() -> None:
    m1 = encode_osc_message("/a", 1.0)
    m2 = encode_osc_message("/b", "x")
    bundle = encode_osc_bundle([m1, m2])
    expected_len = 8 + 8 + (4 + len(m1)) + (4 + len(m2))
    assert len(bundle) == expected_len


# ---------------------------------------------------------------------------
# Mood mapping
# ---------------------------------------------------------------------------


def test_mood_to_blendshapes_has_full_preset_set() -> None:
    weights = mood_to_blendshapes("happy")
    assert set(weights.keys()) == set(MOOD_BLENDSHAPE_PRESETS)
    assert weights["Joy"] == pytest.approx(1.0)
    # Other presets must be explicitly zeroed so receivers reset state.
    assert weights["Sorrow"] == 0.0
    assert weights["Angry"] == 0.0


def test_mood_to_blendshapes_unknown_falls_back_to_neutral() -> None:
    weights = mood_to_blendshapes("definitely-not-a-mood")
    assert weights["Neutral"] == pytest.approx(1.0)
    assert sum(v for k, v in weights.items() if k != "Neutral") == 0.0


def test_mood_to_blendshapes_none() -> None:
    weights = mood_to_blendshapes(None)
    assert weights["Neutral"] == pytest.approx(1.0)


def test_mood_blendshape_presets_expected_keys() -> None:
    assert "Joy" in MOOD_BLENDSHAPE_PRESETS
    assert "Sorrow" in MOOD_BLENDSHAPE_PRESETS
    assert "Angry" in MOOD_BLENDSHAPE_PRESETS
    assert "Surprised" in MOOD_BLENDSHAPE_PRESETS


# ---------------------------------------------------------------------------
# Live UDP round-trip
# ---------------------------------------------------------------------------


@pytest.fixture()
def udp_listener():
    """Bind a UDP socket on an ephemeral port and yield ``(sock, port)``."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    _host, port = sock.getsockname()
    try:
        yield sock, port
    finally:
        sock.close()


def test_send_bone_emits_bundle_to_udp(udp_listener) -> None:
    sock, port = udp_listener
    bridge = VMCBridge(host="127.0.0.1", port=port)
    bridge.start()
    try:
        bridge.send_bone(
            "Hips",
            pos=(0.0, 1.0, 0.0),
            rot=(0.0, 0.0, 0.0, 1.0),
        )
        data, _addr = sock.recvfrom(2048)
    finally:
        bridge.stop()

    # Must be an OSC bundle wrapping one /VMC/Ext/Bone/Pos message.
    assert data[:8] == b"#bundle\x00"
    assert b"/VMC/Ext/Bone/Pos" in data
    assert b"Hips" in data


def test_send_blendshape_then_apply(udp_listener) -> None:
    sock, port = udp_listener
    bridge = VMCBridge(host="127.0.0.1", port=port)
    bridge.start()
    try:
        bridge.send_blendshape("Joy", 0.5)
        data1, _ = sock.recvfrom(2048)
        bridge.apply()
        data2, _ = sock.recvfrom(2048)
    finally:
        bridge.stop()

    assert b"/VMC/Ext/Blend/Val" in data1
    assert b"Joy" in data1
    assert b"/VMC/Ext/Blend/Apply" in data2


def test_start_stop_idempotent() -> None:
    bridge = VMCBridge(host="127.0.0.1", port=1)
    bridge.start()
    bridge.start()  # second call must not raise
    bridge.stop()
    bridge.stop()  # second close must not raise


def test_send_frame_includes_root_bones_and_apply(udp_listener) -> None:
    sock, port = udp_listener
    bridge = VMCBridge(host="127.0.0.1", port=port)
    bridge.start()
    try:
        bridge.send_frame(
            bones={"Hips": {"pos": (0, 1, 0), "rot": (0, 0, 0, 1)}},
            blendshapes={"Joy": 1.0},
            root={"pos": (0, 0, 0), "rot": (0, 0, 0, 1)},
        )
        data, _ = sock.recvfrom(4096)
    finally:
        bridge.stop()

    assert data[:8] == b"#bundle\x00"
    assert b"/VMC/Ext/Root/Pos" in data
    assert b"/VMC/Ext/Bone/Pos" in data
    assert b"/VMC/Ext/Blend/Val" in data
    assert b"/VMC/Ext/Blend/Apply" in data
