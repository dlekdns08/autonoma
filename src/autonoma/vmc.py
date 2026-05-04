"""VMC/OSC bridge for Autonoma.

Publishes the same bone/blendshape stream that drives the web stage to
external receivers (VRChat, VMC4U, NeosVR) over UDP using the VMC
Protocol — a thin convention layered on OSC 1.0 bundles. Stdlib only;
we hand-roll the OSC encoder rather than pull ``python-osc``.

OSC addresses emitted (Feature #16):

* ``/VMC/Ext/Root/Pos``   — type tag ``sfffffff`` (name + pos + rot quat)
* ``/VMC/Ext/Bone/Pos``   — type tag ``sfffffff``
* ``/VMC/Ext/Blend/Val``  — type tag ``sf``
* ``/VMC/Ext/Blend/Apply`` — no args

Subscribed bus events (when :meth:`start_listening_to_bus` is running):

* ``mocap.frame``         — payload ``{bones, blendshapes, root?}``
* ``agent.mood_changed``  — payload ``{mood}`` mapped to VMC presets
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Any, Iterable

from autonoma.config import settings
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OSC 1.0 encoder
# ---------------------------------------------------------------------------


def _pad4(buf: bytes) -> bytes:
    """Pad ``buf`` with NULs up to a 4-byte boundary (OSC alignment)."""
    rem = len(buf) % 4
    if rem == 0:
        return buf
    return buf + b"\x00" * (4 - rem)


def _encode_string(s: str) -> bytes:
    """OSC string: UTF-8, NUL-terminated, padded to 4-byte boundary."""
    return _pad4(s.encode("utf-8") + b"\x00")


def encode_osc_message(addr: str, *args: Any) -> bytes:
    """Encode a single OSC 1.0 message.

    Supported type tags:
      * ``s`` — :class:`str`
      * ``f`` — :class:`float` (32-bit big-endian IEEE-754)
      * ``i`` — :class:`int`   (32-bit big-endian, signed)

    Both the address and the type-tag string are NUL-terminated and
    padded to a 4-byte boundary. Returns a byte string whose length is
    always a multiple of 4.
    """
    addr_bytes = _encode_string(addr)
    tags = ","
    arg_bytes: list[bytes] = []
    for a in args:
        if isinstance(a, bool):
            # bool is a subclass of int — guard so True doesn't sneak in
            # as an int32. VMC doesn't use OSC ``T``/``F`` tags, so the
            # safe fallback is treat it as int32 0/1.
            tags += "i"
            arg_bytes.append(struct.pack(">i", int(a)))
        elif isinstance(a, float):
            tags += "f"
            arg_bytes.append(struct.pack(">f", a))
        elif isinstance(a, int):
            tags += "i"
            arg_bytes.append(struct.pack(">i", a))
        elif isinstance(a, str):
            tags += "s"
            arg_bytes.append(_encode_string(a))
        else:
            raise TypeError(f"unsupported OSC argument type: {type(a).__name__}")
    tag_bytes = _encode_string(tags)
    return addr_bytes + tag_bytes + b"".join(arg_bytes)


def encode_osc_bundle(messages: list[bytes], timetag_ns: int = 1) -> bytes:
    """Encode an OSC bundle.

    Layout: ``"#bundle\\0"`` (8 bytes) + 8-byte timetag + per-message
    ``int32(length) + payload``. ``timetag_ns=1`` means "execute
    immediately" per the OSC 1.0 spec.
    """
    header = b"#bundle\x00"
    # OSC timetag is two uint32s (sec since 1900 + fractional). VMC
    # receivers conventionally accept the immediate-tag (0,1).
    if timetag_ns == 1:
        timetag = struct.pack(">II", 0, 1)
    else:
        # Treat caller value as raw 64-bit; split high/low.
        timetag = struct.pack(">Q", timetag_ns & 0xFFFFFFFFFFFFFFFF)
    parts = [header, timetag]
    for msg in messages:
        parts.append(struct.pack(">i", len(msg)))
        parts.append(msg)
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Mood -> blendshape preset mapping
# ---------------------------------------------------------------------------

# VMC4U / VRM standard "preset" blendshape names. We expose the full set
# even when only one is non-zero so receivers reset the others — without
# explicit zeros a previous mood's expression would linger on the avatar.
MOOD_BLENDSHAPE_PRESETS: tuple[str, ...] = (
    "Neutral",
    "Joy",
    "Sorrow",
    "Angry",
    "Surprised",
    "Fun",
)

# Map Autonoma mood strings to a {preset: weight} dict. Keys here mirror
# values produced by ``autonoma.agents`` Mood enum (lowercased). Any
# unknown mood collapses to ``Neutral``.
_MOOD_MAP: dict[str, dict[str, float]] = {
    "happy":     {"Joy": 1.0},
    "joyful":    {"Joy": 1.0},
    "excited":   {"Joy": 0.7, "Fun": 0.6},
    "fun":       {"Fun": 1.0},
    "sad":       {"Sorrow": 1.0},
    "sorrow":    {"Sorrow": 1.0},
    "angry":     {"Angry": 1.0},
    "frustrated": {"Angry": 0.7},
    "surprised": {"Surprised": 1.0},
    "shocked":   {"Surprised": 1.0},
    "neutral":   {"Neutral": 1.0},
    "calm":      {"Neutral": 1.0},
}


def mood_to_blendshapes(mood: str | None) -> dict[str, float]:
    """Return a full preset dict (zeros included) for ``mood``."""
    base = {name: 0.0 for name in MOOD_BLENDSHAPE_PRESETS}
    if not mood:
        base["Neutral"] = 1.0
        return base
    weights = _MOOD_MAP.get(mood.lower())
    if not weights:
        base["Neutral"] = 1.0
        return base
    base.update(weights)
    return base


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class VMCBridge:
    """Stateful UDP sender that speaks VMC protocol.

    Construct once per process; call :meth:`start` before sending and
    :meth:`stop` to release the socket. Both are idempotent so the
    lifecycle plays nicely with FastAPI startup/shutdown hooks.
    """

    def __init__(
        self,
        host: str,
        port: int,
        model_name: str = "autonoma",
    ) -> None:
        self.host = host
        self.port = port
        self.model_name = model_name
        self._sock: socket.socket | None = None

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._sock is not None:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Non-blocking send so a backed-up receiver can't stall the loop.
        self._sock.setblocking(False)

    def stop(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        finally:
            self._sock = None

    # ---- low-level send -------------------------------------------------

    def _ensure_sock(self) -> socket.socket:
        if self._sock is None:
            self.start()
        assert self._sock is not None
        return self._sock

    def flush_bundle(self, messages: Iterable[bytes]) -> None:
        """Wrap ``messages`` in an OSC bundle and send one UDP datagram."""
        msgs = list(messages)
        if not msgs:
            return
        sock = self._ensure_sock()
        bundle = encode_osc_bundle(msgs)
        try:
            sock.sendto(bundle, (self.host, self.port))
        except (BlockingIOError, OSError) as exc:
            # UDP send shouldn't normally block, but treat all socket
            # errors as recoverable — losing a frame is preferable to
            # crashing the mocap pipeline.
            logger.debug("vmc send dropped: %s", exc)

    # ---- VMC message builders ------------------------------------------

    def _bone_msg(
        self,
        addr: str,
        name: str,
        pos: tuple[float, float, float],
        rot: tuple[float, float, float, float],
    ) -> bytes:
        px, py, pz = pos
        rx, ry, rz, rw = rot
        return encode_osc_message(
            addr,
            name,
            float(px), float(py), float(pz),
            float(rx), float(ry), float(rz), float(rw),
        )

    def send_bone(
        self,
        name: str,
        pos: tuple[float, float, float],
        rot: tuple[float, float, float, float],
    ) -> None:
        """Send a single ``/VMC/Ext/Bone/Pos`` as its own bundle."""
        msg = self._bone_msg("/VMC/Ext/Bone/Pos", name, pos, rot)
        self.flush_bundle([msg])

    def send_root(
        self,
        pos: tuple[float, float, float],
        rot: tuple[float, float, float, float],
    ) -> None:
        """Send ``/VMC/Ext/Root/Pos`` with the configured model name."""
        msg = self._bone_msg("/VMC/Ext/Root/Pos", "root", pos, rot)
        self.flush_bundle([msg])

    def send_blendshape(self, name: str, value: float) -> None:
        msg = encode_osc_message("/VMC/Ext/Blend/Val", name, float(value))
        self.flush_bundle([msg])

    def apply(self) -> None:
        """Tell the receiver to commit pending blendshape values."""
        msg = encode_osc_message("/VMC/Ext/Blend/Apply")
        self.flush_bundle([msg])

    # ---- batched senders used by the bus listener ----------------------

    def send_frame(
        self,
        bones: dict[str, dict[str, Any]] | None,
        blendshapes: dict[str, float] | None,
        root: dict[str, Any] | None = None,
    ) -> None:
        """Send a full mocap frame as one OSC bundle (one UDP datagram).

        Each bone entry is ``{"pos": (x,y,z), "rot": (x,y,z,w)}``.
        Blendshape ``Apply`` is appended automatically when there are
        any blendshape values in the frame.
        """
        msgs: list[bytes] = []
        if root:
            pos = tuple(root.get("pos", (0.0, 0.0, 0.0)))
            rot = tuple(root.get("rot", (0.0, 0.0, 0.0, 1.0)))
            msgs.append(self._bone_msg("/VMC/Ext/Root/Pos", "root", pos, rot))  # type: ignore[arg-type]
        if bones:
            for bname, bdata in bones.items():
                pos = tuple(bdata.get("pos", (0.0, 0.0, 0.0)))
                rot = tuple(bdata.get("rot", (0.0, 0.0, 0.0, 1.0)))
                msgs.append(self._bone_msg("/VMC/Ext/Bone/Pos", bname, pos, rot))  # type: ignore[arg-type]
        if blendshapes:
            for sname, value in blendshapes.items():
                msgs.append(
                    encode_osc_message("/VMC/Ext/Blend/Val", sname, float(value))
                )
            msgs.append(encode_osc_message("/VMC/Ext/Blend/Apply"))
        self.flush_bundle(msgs)


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------


_bridge: VMCBridge | None = None


def get_bridge() -> VMCBridge | None:
    """Return the lazily-constructed bridge, or ``None`` if disabled."""
    global _bridge
    if not getattr(settings, "vmc_bridge_enabled", False):
        return None
    if _bridge is None:
        _bridge = VMCBridge(
            host=settings.vmc_host,
            port=settings.vmc_port,
        )
        _bridge.start()
    return _bridge


def reset_bridge_for_tests() -> None:
    """Drop the singleton (test helper; callers also call ``stop()``)."""
    global _bridge
    if _bridge is not None:
        _bridge.stop()
    _bridge = None


# ---------------------------------------------------------------------------
# Bus listener
# ---------------------------------------------------------------------------


async def start_listening_to_bus() -> None:
    """Wire VMC bridge to the event bus until cancelled.

    Subscribes to ``mocap.frame`` and ``agent.mood_changed``. Returns
    immediately if VMC is disabled. The coroutine sleeps forever
    afterwards so callers can ``asyncio.create_task(...)`` and then
    ``task.cancel()`` cleanly during shutdown — at which point we
    unsubscribe and close the UDP socket.
    """
    bridge = get_bridge()
    if bridge is None:
        # Bridge disabled — nothing to do, but stay alive so the caller
        # can treat the task uniformly.
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return
        return

    async def _on_mocap_frame(
        bones: dict[str, Any] | None = None,
        blendshapes: dict[str, float] | None = None,
        root: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        try:
            bridge.send_frame(bones, blendshapes, root)
        except Exception:
            logger.exception("vmc: failed to send mocap frame")

    async def _on_mood_changed(mood: str | None = None, **_: Any) -> None:
        weights = mood_to_blendshapes(mood)
        msgs = [
            encode_osc_message("/VMC/Ext/Blend/Val", name, float(value))
            for name, value in weights.items()
        ]
        msgs.append(encode_osc_message("/VMC/Ext/Blend/Apply"))
        try:
            bridge.flush_bundle(msgs)
        except Exception:
            logger.exception("vmc: failed to send mood blendshapes")

    bus.on("mocap.frame", _on_mocap_frame)
    bus.on("agent.mood_changed", _on_mood_changed)
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise
    finally:
        bus.off("mocap.frame", _on_mocap_frame)
        bus.off("agent.mood_changed", _on_mood_changed)
        bridge.stop()
