"""Agent-name → .vrm filename mapping (Python mirror of the frontend).

The frontend's ``vrmFileForAgent`` uses a djb2 hash modulo the VRM
roster size so a given agent name always renders with the same
character. We mirror that exactly here so the TTS worker can resolve
the voice binding for an agent without the frontend being involved.

Any divergence between the two implementations would cause agents to
speak with the wrong character's voice, so the test at
``tests/test_voice_vrm_map.py`` pins the mapping against a small fixture
list from ``vrmCatalog.json``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _vrm_files() -> tuple[str, ...]:
    """Load the VRM catalog once; cache the file list in insertion order.

    JSON object key order is preserved in Python 3.7+ and CPython's JSON
    lib keeps it, matching the ``Object.keys(VRM_CREDITS)`` order the JS
    side uses.
    """
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
        return ()
    return tuple(k for k in raw.keys() if isinstance(k, str) and k.endswith(".vrm"))


def _djb2(s: str) -> int:
    """djb2 string hash, uint32. Matches the JS impl in vrmCredits.ts."""
    h = 5381
    for ch in s:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return h


def vrm_file_for_agent(agent_name: str) -> str:
    """Return the .vrm filename deterministically assigned to an agent.

    Returns "" when the catalog couldn't be loaded — callers should
    treat that as "no voice binding possible" rather than crashing.
    """
    files = _vrm_files()
    if not files:
        return ""
    return files[_djb2(agent_name) % len(files)]


__all__ = ["vrm_file_for_agent"]
