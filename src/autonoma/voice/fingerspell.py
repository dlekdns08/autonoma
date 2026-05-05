"""KSL fingerspelling fallback — feature #17.

The phrase-book translator in :mod:`autonoma.routers.sign` covers
words that the operator has authored a clip for. Anything outside the
book gets fingerspelled, but the existing fallback only emits an
opaque ``ksl_letter:<char>`` clip id and the *front-end* has no way to
synthesise a hand pose for an arbitrary Hangul syllable on the fly.

This module bridges that gap with **jamo-level decomposition** — for
each precomposed Hangul syllable in U+AC00..U+D7A3 we recover its
initial consonant, medial vowel and (optional) final consonant using
the standard arithmetic identity::

    code - 0xAC00 = (initial * 588) + (medial * 28) + final

…which lets us drop one frame per jamo into the pose-player. Each
jamo gets a sentinel pose id (``ksl-finger-<jamo>``) so the artist can
later author the actual hand-keyframes per jamo without changing this
file.

Pure stdlib — no jamo libraries, no external Hangul deps. The router
in :mod:`autonoma.routers.fingerspell` exposes the planner over HTTP.
"""

from __future__ import annotations

from typing import Any

# ── Jamo tables ──────────────────────────────────────────────────────
#
# Standard Korean Unicode decomposition tables. The sizes (19 / 21 / 28)
# are wired into the syllable-block arithmetic; do NOT add or remove
# entries without updating ``decompose_hangul``. Order matters — the
# index of each jamo is its position in the decomposition formula.
#
# Reference: Unicode 15.1 Hangul Syllables block (U+AC00..U+D7A3).

# 19 initial (choseong / 초성) consonants.
JAMO_INITIAL: list[str] = [
    "ㄱ",
    "ㄲ",
    "ㄴ",
    "ㄷ",
    "ㄸ",
    "ㄹ",
    "ㅁ",
    "ㅂ",
    "ㅃ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅉ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
]

# 21 medial (jungseong / 중성) vowels.
JAMO_MEDIAL: list[str] = [
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅒ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅙ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅞ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
]

# 28 final (jongseong / 종성) consonants — index 0 is the empty final
# (no batchim). The remaining 27 cover single + cluster finals.
JAMO_FINAL: list[str] = [
    "",
    "ㄱ",
    "ㄲ",
    "ㄳ",
    "ㄴ",
    "ㄵ",
    "ㄶ",
    "ㄷ",
    "ㄹ",
    "ㄺ",
    "ㄻ",
    "ㄼ",
    "ㄽ",
    "ㄾ",
    "ㄿ",
    "ㅀ",
    "ㅁ",
    "ㅂ",
    "ㅄ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
]

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_MEDIAL_COUNT = 21
_FINAL_COUNT = 28
# Block size per initial consonant: 21 * 28 = 588.
_INITIAL_STRIDE = _MEDIAL_COUNT * _FINAL_COUNT


def decompose_hangul(syllable: str) -> tuple[str, str, str | None]:
    """Decompose one precomposed Hangul syllable into its three jamo.

    For a syllable in U+AC00..U+D7A3, returns
    ``(initial, medial, final_or_None)`` where ``final_or_None`` is
    ``None`` when the syllable has no batchim (closed-syllable final
    consonant). For any other character — including a bare jamo from
    the U+1100 / U+3130 blocks, ASCII, or punctuation — returns
    ``(syllable, "", None)`` so callers can still pass through.

    Raises ``ValueError`` if more than one character is supplied; the
    function is deliberately single-syllable so that mass calls like
    :func:`text_to_jamo` keep their pass-through semantics obvious.
    """
    if len(syllable) != 1:
        raise ValueError("decompose_hangul expects a single character")
    code = ord(syllable)
    if code < _HANGUL_BASE or code > _HANGUL_LAST:
        return (syllable, "", None)
    offset = code - _HANGUL_BASE
    initial_idx = offset // _INITIAL_STRIDE
    medial_idx = (offset % _INITIAL_STRIDE) // _FINAL_COUNT
    final_idx = offset % _FINAL_COUNT
    initial = JAMO_INITIAL[initial_idx]
    medial = JAMO_MEDIAL[medial_idx]
    final = JAMO_FINAL[final_idx] if final_idx else None
    return (initial, medial, final)


def text_to_jamo(text: str) -> list[str]:
    """Flatten a string into per-jamo entries.

    Hangul syllables expand into their 2 or 3 jamo, in order. Anything
    else passes through unchanged — one element per character —
    including spaces, punctuation, ASCII letters and pre-decomposed
    jamo. Callers can then map each entry through
    :data:`JAMO_TO_HANDPOSE` (or skip non-jamo entries) to build a
    pose plan.
    """
    out: list[str] = []
    for ch in text:
        initial, medial, final = decompose_hangul(ch)
        if medial:
            # Hangul syllable — emit jamo in onset/nucleus/coda order.
            out.append(initial)
            out.append(medial)
            if final:
                out.append(final)
        else:
            out.append(initial)
    return out


# ── Hand-pose binding table ──────────────────────────────────────────
#
# Each jamo gets a stub pose dict that *mirrors the shape* of an entry
# in ``web/src/lib/poseEditor/fingerPresets.ts`` — ``id`` / ``name`` /
# ``curls`` keys — so the front-end pose-player can consume these
# without an extra adapter. The actual ``curls`` are intentionally
# left empty: the artist will fill them in per jamo, and until then
# the pose-player just renders the resting hand. This still gives the
# back end (and tests) a stable token to bind clips to.

_JAMO_NAME = {
    # initials
    "ㄱ": "기역",
    "ㄲ": "쌍기역",
    "ㄴ": "니은",
    "ㄷ": "디귿",
    "ㄸ": "쌍디귿",
    "ㄹ": "리을",
    "ㅁ": "미음",
    "ㅂ": "비읍",
    "ㅃ": "쌍비읍",
    "ㅅ": "시옷",
    "ㅆ": "쌍시옷",
    "ㅇ": "이응",
    "ㅈ": "지읒",
    "ㅉ": "쌍지읒",
    "ㅊ": "치읓",
    "ㅋ": "키읔",
    "ㅌ": "티읕",
    "ㅍ": "피읖",
    "ㅎ": "히읗",
    # medials
    "ㅏ": "아",
    "ㅐ": "애",
    "ㅑ": "야",
    "ㅒ": "얘",
    "ㅓ": "어",
    "ㅔ": "에",
    "ㅕ": "여",
    "ㅖ": "예",
    "ㅗ": "오",
    "ㅘ": "와",
    "ㅙ": "왜",
    "ㅚ": "외",
    "ㅛ": "요",
    "ㅜ": "우",
    "ㅝ": "워",
    "ㅞ": "웨",
    "ㅟ": "위",
    "ㅠ": "유",
    "ㅡ": "으",
    "ㅢ": "의",
    "ㅣ": "이",
    # cluster finals not in the initial list
    "ㄳ": "기역시옷",
    "ㄵ": "니은지읒",
    "ㄶ": "니은히읗",
    "ㄺ": "리을기역",
    "ㄻ": "리을미음",
    "ㄼ": "리을비읍",
    "ㄽ": "리을시옷",
    "ㄾ": "리을티읕",
    "ㄿ": "리을피읖",
    "ㅀ": "리을히읗",
    "ㅄ": "비읍시옷",
}


def _make_pose(jamo: str) -> dict[str, Any]:
    """Sentinel pose-preset entry for one jamo.

    Shape mirrors ``FingerPreset`` from the front-end:
        { "id": str, "name": str, "curls": {} }
    The empty ``curls`` is intentional — see the file-level note.
    """
    return {
        "id": f"ksl-finger-{jamo}",
        "name": _JAMO_NAME.get(jamo, jamo),
        "jamo": jamo,
        "curls": {},
    }


# ``JAMO_TO_HANDPOSE`` — every jamo (initial ∪ medial ∪ final\{""}) gets
# one entry. Built once at import time; key ordering follows the
# canonical lists above.
JAMO_TO_HANDPOSE: dict[str, dict[str, Any]] = {}
for _j in JAMO_INITIAL:
    JAMO_TO_HANDPOSE.setdefault(_j, _make_pose(_j))
for _j in JAMO_MEDIAL:
    JAMO_TO_HANDPOSE.setdefault(_j, _make_pose(_j))
for _j in JAMO_FINAL:
    if _j:
        JAMO_TO_HANDPOSE.setdefault(_j, _make_pose(_j))
del _j


# ── Plan builder ─────────────────────────────────────────────────────


def fingerspell_plan(
    text: str,
    hold_ms: int = 320,
    transition_ms: int = 120,
) -> list[dict[str, Any]]:
    """Build a per-jamo pose plan suitable for the front-end player.

    Each frame is a dict::

        {
          "kind": "jamo" | "space" | "punct",
          "pose": "ksl-finger-<jamo>" | None,
          "jamo": "<jamo>" | None,
          "surface": "<original char>",
          "hold_ms": int,
          "transition_ms": int,
        }

    * ``kind="jamo"`` — a Hangul jamo with a bound pose.
    * ``kind="space"`` — a whitespace pause (``pose`` is ``None``;
      the player is expected to insert a beat / lower the hands).
    * ``kind="punct"`` — anything else that came through pass-through
      (ASCII letters, numbers, punctuation). The player can render
      these as captions or skip them.

    ``hold_ms`` / ``transition_ms`` are clamped to non-negative ints
    so a hostile caller can't poison the player loop with negatives.
    """
    hold = max(0, int(hold_ms))
    transition = max(0, int(transition_ms))

    plan: list[dict[str, Any]] = []
    for ch in text:
        initial, medial, final = decompose_hangul(ch)
        if medial:
            # Hangul syllable → 2 or 3 jamo frames.
            for jamo in (initial, medial, *(("" if final is None else final),)):
                if not jamo:
                    continue
                plan.append(
                    {
                        "kind": "jamo",
                        "pose": JAMO_TO_HANDPOSE[jamo]["id"],
                        "jamo": jamo,
                        "surface": ch,
                        "hold_ms": hold,
                        "transition_ms": transition,
                    }
                )
            continue
        # Pass-through char — could be a bare jamo, a space, or punct.
        if ch in JAMO_TO_HANDPOSE:
            plan.append(
                {
                    "kind": "jamo",
                    "pose": JAMO_TO_HANDPOSE[ch]["id"],
                    "jamo": ch,
                    "surface": ch,
                    "hold_ms": hold,
                    "transition_ms": transition,
                }
            )
        elif ch.isspace():
            plan.append(
                {
                    "kind": "space",
                    "pose": None,
                    "jamo": None,
                    "surface": ch,
                    "hold_ms": hold,
                    "transition_ms": transition,
                }
            )
        else:
            plan.append(
                {
                    "kind": "punct",
                    "pose": None,
                    "jamo": None,
                    "surface": ch,
                    "hold_ms": hold,
                    "transition_ms": transition,
                }
            )
    return plan


__all__ = [
    "JAMO_INITIAL",
    "JAMO_MEDIAL",
    "JAMO_FINAL",
    "JAMO_TO_HANDPOSE",
    "decompose_hangul",
    "text_to_jamo",
    "fingerspell_plan",
]
