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


def _load_vrm_files() -> frozenset[str]:
    """Authoritative list of .vrm filenames; mirrors ``vrmCatalog.json``.

    We don't want to duplicate the JSON — read the same file the frontend
    reads so adding a new .vrm to ``public/vrm/`` + the catalog
    automatically makes new bindings targetable without a code change
    on the server.
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
    except FileNotFoundError:
        # Defensive fallback for odd deployment layouts — allow anything
        # ending in .vrm so the server doesn't become the single point of
        # failure if the repo tree shape diverges.
        return frozenset()
    return frozenset(k for k in raw.keys() if isinstance(k, str) and k.endswith(".vrm"))


_VRM_FILES: frozenset[str] = _load_vrm_files()


def is_known_vrm(vrm_file: str) -> bool:
    """``True`` iff ``vrm_file`` is registered in ``vrmCatalog.json``.

    When the catalog couldn't be loaded (exotic deploy layout), we fall
    open and accept anything ending in ``.vrm`` rather than rejecting
    every binding.
    """
    if not _VRM_FILES:
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


def trigger_catalog() -> dict[str, list[str]]:
    """Serializable whitelist for the frontend."""
    return {
        "mood": list(MOOD_TRIGGERS),
        "emote": list(EMOTE_TRIGGERS),
        "state": list(STATE_TRIGGERS),
    }
