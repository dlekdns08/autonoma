"""Live mocap ingest WebSocket → re-emit on the bus as ``mocap.frame``.

The browser (``/mocap`` page) computes humanoid bone quaternions and VRM
blendshape weights from a webcam feed and streams them here. We perform
cheap shape validation, clamp blendshape weights to ``[0, 1]``, throttle
to a hard ceiling of 60 frames/sec, then re-publish each accepted frame
on the in-process event bus as ``mocap.frame``. The VMC bridge
(``autonoma.vmc.start_listening_to_bus``) — and any future consumer —
subscribes to that event and forwards to VRChat / VMC4U over UDP.

This router intentionally does *no* persistence and *no* business logic.
It's a thin adapter: WS frame → bus event. The browser is the source of
truth for "what is the avatar doing right now".

JSON envelope (text frames only)::

    {
      "bones":       {"<boneName>": {"pos": [x,y,z], "rot": [x,y,z,w]}, ...},
      "blendshapes": {"<name>": <number 0..1>, ...},
      "root":        {"pos": [x,y,z], "rot": [x,y,z,w]},
      "t":           <client-side timestamp, ms>
    }

All four members are optional. ``bones`` keys are arbitrary strings
(VRM/Mixamo bone names — opaque to this router). Validation is per-entry
and any malformed entry drops the whole frame (logged at DEBUG, counted
toward the per-connection drop counter).

Bus events emitted::

    mocap.frame
        vrm_file: str        — query param ``?vrm=...``
        bones: dict|None
        blendshapes: dict|None
        root: dict|None

    mocap.frame.session_ended
        vrm_file: str
        frames_accepted: int
        frames_dropped:  int
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from autonoma.auth import SESSION_COOKIE_NAME, read_session_token
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mocap"])


# ── Validation / throttle constants ───────────────────────────────────

# Hard ceiling: at most this many frames forwarded to the bus per second
# per connection. Excess frames are silently dropped (token bucket). The
# /mocap page targets 30 Hz; 60 gives 2× headroom while still protecting
# subscribers from a runaway client.
MAX_FRAMES_PER_SECOND: int = 60

# Largest single text frame we will parse. A typical full-body humanoid
# frame with ~55 bones + ~50 blendshapes serializes to ~6–8 KB, so 32 KB
# is generous. Anything bigger is malformed or hostile and is dropped
# without parsing.
MAX_FRAME_BYTES: int = 32 * 1024


# ── Helpers ───────────────────────────────────────────────────────────


def _is_number(x: Any) -> bool:
    """Accept ints/floats but reject ``bool`` (it's an int subclass)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _coerce_vec(seq: Any, length: int) -> list[float] | None:
    """Return ``seq`` as ``list[float]`` of exactly ``length``, else None."""
    if not isinstance(seq, list) or len(seq) != length:
        return None
    out: list[float] = []
    for v in seq:
        if not _is_number(v):
            return None
        out.append(float(v))
    return out


def _validate_bones(raw: Any) -> dict[str, dict[str, list[float]]] | None:
    """Validate the ``bones`` map. Returns the cleaned dict or ``None`` on any
    malformed entry — caller drops the whole frame in that case.
    """
    if not isinstance(raw, dict):
        return None
    cleaned: dict[str, dict[str, list[float]]] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            return None
        pos = _coerce_vec(entry.get("pos"), 3)
        rot = _coerce_vec(entry.get("rot"), 4)
        if pos is None or rot is None:
            return None
        cleaned[name] = {"pos": pos, "rot": rot}
    return cleaned


def _validate_root(raw: Any) -> dict[str, list[float]] | None:
    """Validate ``root``. Returns the cleaned dict or ``None`` if malformed.

    Distinct from "missing": absence is fine, the loop checks for that
    before calling us. A *present-but-broken* root is treated like a
    malformed bone — it sinks the entire frame.
    """
    if not isinstance(raw, dict):
        return None
    pos = _coerce_vec(raw.get("pos"), 3)
    rot = _coerce_vec(raw.get("rot"), 4)
    if pos is None or rot is None:
        return None
    return {"pos": pos, "rot": rot}


def _clamp_blendshapes(raw: Any) -> dict[str, float] | None:
    """Clamp every blendshape value to ``[0, 1]``. Returns ``None`` on a
    non-numeric entry (caller drops the frame).
    """
    if not isinstance(raw, dict):
        return None
    cleaned: dict[str, float] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not _is_number(value):
            return None
        v = float(value)
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        cleaned[name] = v
    return cleaned


# ── WebSocket endpoint ────────────────────────────────────────────────


@router.websocket("/api/mocap/live")
async def mocap_live(
    ws: WebSocket,
    vrm: str = Query(default=""),
) -> None:
    """Ingest live mocap frames from the browser and re-emit on the bus.

    Auth: same cookie-based session as HTTP. We accept first so we can
    return a structured close code (``4401``) rather than a TCP RST,
    matching the ``/api/voice/stream`` pattern.

    Lifecycle:

    1. ``ws.accept()``
    2. Validate the session cookie. No session → close ``4401``.
    3. Loop over text frames; parse + validate + emit ``mocap.frame``.
    4. On disconnect (or any unrecoverable error), emit
       ``mocap.frame.session_ended`` with accept/drop counters.
    """
    await ws.accept()

    # ── Cookie session check ──────────────────────────────────────
    cookie_token = ws.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_token(cookie_token or "")
    if not user_id:
        try:
            await ws.close(code=4401)
        except Exception:
            # Connection may already be torn down — best effort only.
            pass
        return

    vrm_file = (vrm or "").strip()

    # ── Per-connection counters / token bucket ────────────────────
    frames_accepted = 0
    frames_dropped = 0

    # Simple token bucket: refill ``MAX_FRAMES_PER_SECOND`` tokens per
    # second; each accepted frame costs one. ``time.monotonic()`` so a
    # wall-clock jump can't grant a free burst.
    bucket_tokens: float = float(MAX_FRAMES_PER_SECOND)
    bucket_last: float = time.monotonic()
    bucket_capacity: float = float(MAX_FRAMES_PER_SECOND)
    refill_per_sec: float = float(MAX_FRAMES_PER_SECOND)

    def _take_token() -> bool:
        """Return True iff a token was available (frame may pass)."""
        nonlocal bucket_tokens, bucket_last
        now = time.monotonic()
        elapsed = now - bucket_last
        if elapsed > 0:
            bucket_tokens = min(
                bucket_capacity, bucket_tokens + elapsed * refill_per_sec
            )
            bucket_last = now
        if bucket_tokens >= 1.0:
            bucket_tokens -= 1.0
            return True
        return False

    try:
        while True:
            try:
                text = await ws.receive_text()
            except WebSocketDisconnect:
                # Normal client close — exit the loop quietly.
                break
            except Exception:
                # Any other receive error: log and stop. We don't try to
                # keep the socket alive past a transport-level fault.
                logger.warning(
                    "[mocap_live] receive failed; closing", exc_info=True
                )
                break

            try:
                # ── Size guard (pre-parse) ──────────────────────
                # Length-check the UTF-8 string in *bytes* so we match the
                # documented 32 KB limit regardless of multibyte content.
                # Rough upper bound first via len(text) avoids paying the
                # encode for obviously-fine frames.
                if len(text) > MAX_FRAME_BYTES:
                    # Cheap path: ASCII upper bound already exceeds limit.
                    frames_dropped += 1
                    logger.debug(
                        "[mocap_live] frame too large (>%d chars), dropped",
                        MAX_FRAME_BYTES,
                    )
                    continue
                if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
                    frames_dropped += 1
                    logger.debug(
                        "[mocap_live] frame too large (>%d bytes), dropped",
                        MAX_FRAME_BYTES,
                    )
                    continue

                # ── Parse JSON ──────────────────────────────────
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    frames_dropped += 1
                    logger.debug("[mocap_live] non-JSON frame dropped")
                    continue
                if not isinstance(payload, dict):
                    frames_dropped += 1
                    logger.debug("[mocap_live] non-object frame dropped")
                    continue

                # ── Validate sub-objects (drop entire frame on
                #    *any* malformed entry; matches the spec) ─────
                bones_raw = payload.get("bones")
                blends_raw = payload.get("blendshapes")
                root_raw = payload.get("root")

                bones: dict[str, dict[str, list[float]]] | None = None
                if bones_raw is not None:
                    bones = _validate_bones(bones_raw)
                    if bones is None:
                        frames_dropped += 1
                        logger.debug("[mocap_live] malformed bones, frame dropped")
                        continue

                blendshapes: dict[str, float] | None = None
                if blends_raw is not None:
                    blendshapes = _clamp_blendshapes(blends_raw)
                    if blendshapes is None:
                        frames_dropped += 1
                        logger.debug(
                            "[mocap_live] malformed blendshapes, frame dropped"
                        )
                        continue

                root: dict[str, list[float]] | None = None
                if root_raw is not None:
                    root = _validate_root(root_raw)
                    if root is None:
                        frames_dropped += 1
                        logger.debug("[mocap_live] malformed root, frame dropped")
                        continue

                # ── Rate limit (token bucket) ───────────────────
                if not _take_token():
                    frames_dropped += 1
                    # No log spam — this is the steady-state limiter,
                    # firing once per excess frame would flood logs.
                    continue

                # ── Emit on bus ─────────────────────────────────
                await bus.emit(
                    "mocap.frame",
                    vrm_file=vrm_file,
                    bones=bones,
                    blendshapes=blendshapes,
                    root=root,
                )
                frames_accepted += 1
            except Exception:
                # Best-effort policy: a single bad frame must never kill
                # the whole WS. Log with traceback and keep reading.
                frames_dropped += 1
                logger.warning(
                    "[mocap_live] unexpected error processing frame",
                    exc_info=True,
                )
                continue
    finally:
        # Always emit the session-ended event so consumers can clean up
        # per-connection state (e.g. a UI showing "live" indicator).
        try:
            await bus.emit(
                "mocap.frame.session_ended",
                vrm_file=vrm_file,
                frames_accepted=frames_accepted,
                frames_dropped=frames_dropped,
            )
        except Exception:
            logger.warning(
                "[mocap_live] failed to emit session_ended", exc_info=True
            )
        try:
            await ws.close()
        except Exception:
            # Already closed by the client — fine.
            pass
