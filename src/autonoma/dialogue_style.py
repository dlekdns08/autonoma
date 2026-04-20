"""Per-character speech styling.

The goal is sonic personality without LLM cost: every utterance an
agent emits goes through ``style_speech`` first, which applies a
deterministic transform derived from the agent's bones (rarity +
traits + species) and current mood.

This is intentionally light — we add suffixes, light reshaping (caps
for legendary, lowercase + ellipses for calm), and the species
catchphrase as an occasional tag. Aggressive paraphrasing belongs in
prompt engineering, not here.

Why a separate module? ``base.AutonomousAgent._say`` is hot-path code
and shouldn't grow more conditionals. Pulling the transform out lets
us unit-test the styling in isolation and tune it without touching the
agent loop.
"""

from __future__ import annotations

import hashlib
from typing import Optional

# Lazy-import ``AgentBones``/``Trait`` lazily inside the function to
# keep this module dependency-light (it'd otherwise pull half of
# ``world.py`` into anything that imports the styler).


def _seed_int(name: str, text: str) -> int:
    """Combine the speaker name with the utterance for deterministic
    flavor decisions (so the same line from the same character always
    styles the same way, but different lines vary)."""
    return int(hashlib.md5(f"{name}|{text}".encode()).hexdigest()[:8], 16)


def _ends_with_punct(text: str) -> bool:
    return bool(text) and text.rstrip()[-1] in ".!?…~♪♥"


def style_speech(
    *,
    name: str,
    text: str,
    bones: object | None,
    mood: str = "",
) -> str:
    """Apply a deterministic style transform.

    Parameters are kw-only so callers can't accidentally pass them in
    the wrong order — there's no good positional convention for "agent
    name" vs "spoken text".

    The transform is allowed to mutate the text but never to change its
    *meaning*. Truncation/expansion is fine; paraphrasing is not.
    """
    if not text:
        return text

    rarity = getattr(bones, "rarity", "") if bones else ""
    traits = [t.value for t in getattr(bones, "traits", [])] if bones else []
    catchphrase = getattr(bones, "catchphrase", "") if bones else ""

    out = text.strip()
    seed = _seed_int(name, out)

    # ── Tone shaping by rarity ─────────────────────────────────────
    if rarity == "legendary":
        # Legendary characters speak with weight — short lines stay
        # ALL CAPS, longer lines just get an emphatic period.
        if len(out) <= 40:
            out = out.upper()
        elif not _ends_with_punct(out):
            out = out + "."

    # ── Trait flavor ──────────────────────────────────────────────
    if "calm" in traits:
        # Soften: lowercase plus a trailing ellipsis on every fourth
        # line (selected via the per-utterance seed so it's varied
        # but reproducible).
        if seed % 4 == 0 and not _ends_with_punct(out):
            out = out.lower() + "..."

    if "bold" in traits and not _ends_with_punct(out):
        # Bold = decisive. End with an emphatic mark when the line
        # didn't already pick one.
        out = out + "!"

    if "creative" in traits and seed % 5 == 0:
        # Occasional flourish — a leading sparkle.
        out = "✦ " + out

    if "friendly" in traits and seed % 6 == 0:
        # Throw a "♪" so reading the chat feels chipper.
        out = out + " ♪"

    # ── Mood overlays (small, used sparingly) ─────────────────────
    if mood == "tired" and seed % 3 == 0:
        out = out + " (yawn)"
    if mood == "frustrated" and seed % 4 == 0:
        out = out + " ugh."

    # ── Catchphrase tag (rare, only on long lines) ────────────────
    # We don't want every utterance to drag the catchphrase along —
    # 1-in-12 keeps it as a recognizable signature without grating.
    if catchphrase and len(out) > 25 and seed % 12 == 0:
        out = f"{out} — {catchphrase}"

    return out


def funeral_lines(
    *,
    deceased_name: str,
    survivors: list[tuple[str, float]],
) -> list[tuple[str, str]]:
    """Build per-survivor eulogy lines, ordered by trust strength.

    ``survivors`` is a list of ``(survivor_name, trust)`` pairs — we
    take the top three (positive trust only) and render a templated
    line per survivor. The lines are intentionally short and templated
    so the funeral plays even on a flaky/no-LLM run.

    Returned as ``(speaker, text)`` so the caller controls *how* to
    speak (which is normally just calling ``agent._say``).
    """
    # Filter to positives, sort by trust desc, take 3.
    ranked = sorted(
        ((s, t) for s, t in survivors if t > 0),
        key=lambda x: x[1],
        reverse=True,
    )[:3]
    if not ranked:
        return []

    templates = [
        "I'll miss {deceased}. We had something rare.",
        "{deceased}... you carried us when we couldn't carry ourselves.",
        "Goodbye, {deceased}. The HQ feels colder.",
    ]
    out: list[tuple[str, str]] = []
    for i, (survivor, _trust) in enumerate(ranked):
        line = templates[i % len(templates)].format(deceased=deceased_name)
        out.append((survivor, line))
    return out
