"""Unit coverage for the KSL fingerspelling fallback (feature #17).

Pure-stdlib jamo-level tests — no FastAPI / DB fixtures needed.
The router is exercised separately; here we pin the decomposition
arithmetic and the plan shape so the front-end binding contract
stays stable.
"""

from __future__ import annotations

import pytest

from autonoma.voice.fingerspell import (
    JAMO_FINAL,
    JAMO_INITIAL,
    JAMO_MEDIAL,
    JAMO_TO_HANDPOSE,
    decompose_hangul,
    fingerspell_plan,
    text_to_jamo,
)


# ── Jamo table sizes ────────────────────────────────────────────────


def test_jamo_set_sizes() -> None:
    """Wired into the syllable-block arithmetic; do not change."""
    assert len(JAMO_INITIAL) == 19
    assert len(JAMO_MEDIAL) == 21
    assert len(JAMO_FINAL) == 28
    # Index 0 of FINAL is the empty (no batchim) slot.
    assert JAMO_FINAL[0] == ""


def test_jamo_table_no_duplicates() -> None:
    """Decomposition is a bijection — duplicate entries would silently
    misalign the index math."""
    assert len(set(JAMO_INITIAL)) == len(JAMO_INITIAL)
    assert len(set(JAMO_MEDIAL)) == len(JAMO_MEDIAL)
    # FINAL contains "" at index 0; the rest must be unique.
    non_empty_finals = [f for f in JAMO_FINAL if f]
    assert len(set(non_empty_finals)) == len(non_empty_finals)


# ── decompose_hangul ────────────────────────────────────────────────


def test_decompose_han() -> None:
    """한 = ㅎ + ㅏ + ㄴ (closed syllable with batchim)."""
    assert decompose_hangul("한") == ("ㅎ", "ㅏ", "ㄴ")


def test_decompose_geul() -> None:
    """글 = ㄱ + ㅡ + ㄹ."""
    assert decompose_hangul("글") == ("ㄱ", "ㅡ", "ㄹ")


def test_decompose_open_syllable() -> None:
    """나 has no batchim — final is None, not empty string."""
    assert decompose_hangul("나") == ("ㄴ", "ㅏ", None)


def test_decompose_ascii_passthrough() -> None:
    """Non-Hangul characters pass through with empty medial / None final."""
    assert decompose_hangul("a") == ("a", "", None)
    assert decompose_hangul(" ") == (" ", "", None)
    assert decompose_hangul("?") == ("?", "", None)


def test_decompose_rejects_multi_char() -> None:
    with pytest.raises(ValueError):
        decompose_hangul("한글")
    with pytest.raises(ValueError):
        decompose_hangul("")


# ── text_to_jamo ────────────────────────────────────────────────────


def test_text_to_jamo_mixed() -> None:
    """한글 → ㅎ ㅏ ㄴ ㄱ ㅡ ㄹ; spaces / ASCII pass through."""
    assert text_to_jamo("한글") == ["ㅎ", "ㅏ", "ㄴ", "ㄱ", "ㅡ", "ㄹ"]
    assert text_to_jamo("나 a") == ["ㄴ", "ㅏ", " ", "a"]


# ── fingerspell_plan ────────────────────────────────────────────────


def test_plan_open_syllable_two_frames() -> None:
    """나 → 2 frames (ㄴ, ㅏ). No batchim, so no third frame."""
    plan = fingerspell_plan("나")
    assert len(plan) == 2
    assert [f["jamo"] for f in plan] == ["ㄴ", "ㅏ"]
    assert all(f["kind"] == "jamo" for f in plan)


def test_plan_closed_syllable_three_frames() -> None:
    """강 → 3 frames (ㄱ, ㅏ, ㅇ). Has batchim ㅇ."""
    plan = fingerspell_plan("강")
    assert len(plan) == 3
    assert [f["jamo"] for f in plan] == ["ㄱ", "ㅏ", "ㅇ"]


def test_plan_pose_ids_match_handpose_table() -> None:
    """Each jamo frame's ``pose`` matches the binding table."""
    plan = fingerspell_plan("한")
    for frame in plan:
        jamo = frame["jamo"]
        assert frame["pose"] == JAMO_TO_HANDPOSE[jamo]["id"]
        assert frame["pose"] == f"ksl-finger-{jamo}"


def test_plan_timing_passthrough_and_clamp() -> None:
    """Custom hold / transition values are preserved on each frame.
    Negative values clamp to 0 (don't crash the player)."""
    plan = fingerspell_plan("나", hold_ms=500, transition_ms=200)
    assert all(f["hold_ms"] == 500 for f in plan)
    assert all(f["transition_ms"] == 200 for f in plan)

    plan = fingerspell_plan("나", hold_ms=-1, transition_ms=-5)
    assert all(f["hold_ms"] == 0 for f in plan)
    assert all(f["transition_ms"] == 0 for f in plan)


def test_plan_handles_space_and_punct() -> None:
    """Spaces/punct emit their own kinds rather than getting dropped —
    the front-end can render a beat or caption."""
    plan = fingerspell_plan("나 ?")
    kinds = [f["kind"] for f in plan]
    # ㄴ, ㅏ, space, punct
    assert kinds == ["jamo", "jamo", "space", "punct"]
    assert plan[2]["pose"] is None
    assert plan[3]["pose"] is None


def test_handpose_table_covers_every_jamo() -> None:
    """Every jamo in the canonical sets must have a pose binding —
    otherwise ``fingerspell_plan`` would KeyError on real text."""
    for j in JAMO_INITIAL:
        assert j in JAMO_TO_HANDPOSE
    for j in JAMO_MEDIAL:
        assert j in JAMO_TO_HANDPOSE
    for j in JAMO_FINAL:
        if j:  # skip the empty no-batchim slot
            assert j in JAMO_TO_HANDPOSE
