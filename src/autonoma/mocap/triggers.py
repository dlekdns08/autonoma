"""Trigger whitelist shared with the frontend.

Mirrors the VRMCharacter playback pipeline so a binding cannot reference
a trigger the renderer won't fire:

- ``mood``  — keys of ``MOOD_MAP`` in ``web/.../VRMCharacter.tsx``.
- ``emote`` — icon strings in ``EMOTE_GESTURE_MAP`` (backend emits these
  in ``agent.emote`` events; see ``autonoma/agents/base.py`` MOOD_EMOTE).
- ``state`` — agent lifecycle states the renderer reads.
- ``manual``— user-defined slugs fired via the debug trigger UI.

Kept as plain data so the frontend can import the same list via a
JSON endpoint (``GET /api/mocap/triggers``) without duplication drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MOOD_TRIGGERS: tuple[str, ...] = (
    "idle",
    "happy",
    "excited",
    "proud",
    "frustrated",
    "worried",
    "relaxed",
    "determined",
    "focused",
    "curious",
    "tired",
    "nostalgic",
    "inspired",
    "mischievous",
    "friendly",
)

# Matches EMOTE_GESTURE_MAP keys in VRMCharacter.tsx.
EMOTE_TRIGGERS: tuple[str, ...] = (
    "✦",
    "★",
    "‼",
    "💡",
    "♪",
    "?",
    "•",
    "💧",
    "💤",
    "💢",
    "✧",
    "～",
    "✿",
)

STATE_TRIGGERS: tuple[str, ...] = (
    "idle",
    "working",
    "talking",
    "thinking",
    "celebrating",
    "spawning",
    "error",
)

ALLOWED_TRIGGER_KINDS: tuple[str, ...] = ("mood", "emote", "state", "manual")

MANUAL_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def _load_vrm_files() -> frozenset[str] | None:
    """Authoritative list of .vrm filenames; mirrors ``vrmCatalog.json``.

    We don't want to duplicate the JSON — read the same file the frontend
    reads so adding a new .vrm to ``public/vrm/`` + the catalog
    automatically makes new bindings targetable without a code change
    on the server.

    Returns ``None`` (sentinel for "catalog unreachable") on I/O errors
    or JSON decode errors so callers can distinguish that from a catalog
    that loaded successfully but contains no entries.
    """
    import json

    here = Path(__file__).resolve()
    candidate = (
        here.parents[3]
        / "web"
        / "src"
        / "components"
        / "vtuber"
        / "vrmCatalog.json"
    )
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # Defensive fallback for odd deployment layouts — fall open
        # below rather than rejecting every binding.
        return None
    if not isinstance(raw, dict):
        return None
    return frozenset(k for k in raw.keys() if isinstance(k, str) and k.endswith(".vrm"))


# ``None`` means the catalog file couldn't be read — callers fall open.
# An empty frozenset means the file loaded but declares zero characters,
# which is itself a legitimate "no bindings allowed" state.
_VRM_FILES: frozenset[str] | None = _load_vrm_files()


def is_known_vrm(vrm_file: str) -> bool:
    """``True`` iff ``vrm_file`` is registered in ``vrmCatalog.json``.

    When the catalog couldn't be loaded (exotic deploy layout), we fall
    open and accept anything ending in ``.vrm`` rather than rejecting
    every binding. A catalog that *loaded* but contains zero entries is
    honoured — no bindings are targetable.
    """
    if _VRM_FILES is None:
        return isinstance(vrm_file, str) and vrm_file.endswith(".vrm")
    return vrm_file in _VRM_FILES


def validate_trigger(kind: str, value: str) -> str | None:
    """Return ``None`` if valid, else a short error code."""
    if kind not in ALLOWED_TRIGGER_KINDS:
        return "invalid_kind"
    if not isinstance(value, str) or not value:
        return "invalid_value"
    if kind == "mood" and value not in MOOD_TRIGGERS:
        return "unknown_mood"
    if kind == "emote" and value not in EMOTE_TRIGGERS:
        return "unknown_emote"
    if kind == "state" and value not in STATE_TRIGGERS:
        return "unknown_state"
    if kind == "manual" and not MANUAL_SLUG_RE.match(value):
        return "invalid_manual_slug"
    return None


def trigger_catalog() -> dict[str, Any]:
    """Serializable whitelist + shared limits for the frontend."""
    # Local import to avoid a circular dependency at module import time
    # (``autonoma.mocap.__init__`` imports from both modules).
    from autonoma.mocap.validator import MAX_CLIP_DURATION_S

    return {
        "mood": list(MOOD_TRIGGERS),
        "emote": list(EMOTE_TRIGGERS),
        "state": list(STATE_TRIGGERS),
        "max_clip_duration_s": MAX_CLIP_DURATION_S,
    }
