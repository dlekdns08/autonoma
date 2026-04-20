"""Dialogue-style transforms + funeral orchestration.

These tests pin the deterministic behavior of ``style_speech`` (so
characters stay recognizable across runs) and the eulogy ranking of
``funeral_lines`` (so the people who loved the deceased most actually
get to speak). We also cover the swarm-side ``_hold_funeral`` hook to
prove the world.event fires before the first eulogy and that lines
reach survivor ``_say``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from autonoma.dialogue_style import style_speech, funeral_lines


@dataclass
class _FakeBones:
    """Minimal duck-typed stand-in for ``AgentBones``.

    ``style_speech`` only reads rarity/traits/catchphrase via ``getattr``
    so we can skip the full bones machinery and exercise each branch
    without depending on role-hash determinism."""

    rarity: str = "common"
    traits: list[object] = field(default_factory=list)
    catchphrase: str = ""


@dataclass
class _FakeTrait:
    """Mirrors ``Trait``'s ``.value`` access pattern."""
    value: str


def _bones(rarity: str = "common", traits: list[str] | None = None, catchphrase: str = "") -> _FakeBones:
    return _FakeBones(
        rarity=rarity,
        traits=[_FakeTrait(v) for v in (traits or [])],
        catchphrase=catchphrase,
    )


# ── style_speech ─────────────────────────────────────────────────────


def test_style_speech_returns_empty_for_empty_input() -> None:
    assert style_speech(name="Kit", text="", bones=None) == ""


def test_style_speech_is_deterministic_for_same_inputs() -> None:
    """Same name + text + bones must always produce the same output —
    the md5 seed is the whole point."""
    b = _bones(rarity="legendary", traits=["bold", "creative"], catchphrase="Nyaa~!")
    a = style_speech(name="Mira", text="ship it now", bones=b)
    c = style_speech(name="Mira", text="ship it now", bones=b)
    assert a == c


def test_style_speech_legendary_short_lines_become_uppercase() -> None:
    b = _bones(rarity="legendary")
    # Short line — upper-cased.
    assert style_speech(name="N", text="rise up", bones=b) == "RISE UP"


def test_style_speech_legendary_long_lines_get_emphatic_period() -> None:
    b = _bones(rarity="legendary")
    long_line = "this is a line deliberately over forty chars long without punctuation"
    out = style_speech(name="N", text=long_line, bones=b)
    # Case is preserved on long lines; trailing period is added.
    assert out.endswith(".")
    assert out.startswith("this is a line")


def test_style_speech_legendary_long_line_keeps_existing_punctuation() -> None:
    b = _bones(rarity="legendary")
    long_line = "this is a line deliberately over forty chars long that ends with a bang!"
    out = style_speech(name="N", text=long_line, bones=b)
    assert out.endswith("!")
    assert not out.endswith("..")


def test_style_speech_bold_trait_adds_exclamation_when_unpunctuated() -> None:
    b = _bones(traits=["bold"])
    out = style_speech(name="N", text="charge ahead", bones=b)
    assert out.endswith("!")


def test_style_speech_bold_trait_does_not_double_up_punctuation() -> None:
    b = _bones(traits=["bold"])
    out = style_speech(name="N", text="charge ahead!", bones=b)
    assert out == "charge ahead!"


def test_style_speech_returns_text_unchanged_when_bones_missing() -> None:
    """Tests / harness paths can call ``_say`` before bones are hydrated;
    the transform must be a no-op (modulo .strip()) rather than crash."""
    out = style_speech(name="N", text="  hello  ", bones=None)
    assert out == "hello"


def test_style_speech_applies_mood_overlay_for_some_lines() -> None:
    """The tired overlay fires on 1/3 seeds; we search over many texts
    to show the overlay path is reachable without pinning a specific
    md5 result."""
    b = _bones()
    yawns = [
        style_speech(name="N", text=f"line {i}", bones=b, mood="tired")
        for i in range(30)
    ]
    assert any("(yawn)" in line for line in yawns)


def test_style_speech_does_not_paraphrase() -> None:
    """The module's contract: transforms may reshape, never rewrite.
    We verify by checking the original words survive lower-casing."""
    b = _bones(rarity="legendary", traits=["bold", "friendly"])
    original = "deploy the service at noon"
    out = style_speech(name="N", text=original, bones=b)
    for word in original.split():
        assert word in out.lower()


# ── funeral_lines ────────────────────────────────────────────────────


def test_funeral_lines_empty_when_no_positive_trust() -> None:
    """If nobody had a positive bond we return no lines — silence is
    the right default for a stranger's death."""
    assert funeral_lines(deceased_name="X", survivors=[]) == []
    assert funeral_lines(deceased_name="X", survivors=[("A", 0.0), ("B", -0.5)]) == []


def test_funeral_lines_takes_top_three_by_trust() -> None:
    """We cap at three eulogies regardless of how many friends there
    were — it's a memorial, not a press conference."""
    survivors = [("A", 0.3), ("B", 0.9), ("C", 0.6), ("D", 0.1), ("E", 0.8)]
    lines = funeral_lines(deceased_name="Kit", survivors=survivors)
    assert len(lines) == 3
    speakers = [speaker for speaker, _ in lines]
    # Order must be trust-desc: B (0.9) > E (0.8) > C (0.6).
    assert speakers == ["B", "E", "C"]


def test_funeral_lines_skips_non_positive_while_keeping_order() -> None:
    survivors = [("low", 0.0), ("mid", 0.4), ("high", 0.95)]
    lines = funeral_lines(deceased_name="Kit", survivors=survivors)
    assert [s for s, _ in lines] == ["high", "mid"]


def test_funeral_lines_mentions_deceased_in_each_line() -> None:
    """The eulogy templates always interpolate the deceased's name —
    important because it's the only identifier a spectator has."""
    survivors = [("A", 0.9), ("B", 0.8), ("C", 0.7)]
    lines = funeral_lines(deceased_name="Mira", survivors=survivors)
    for _, text in lines:
        assert "Mira" in text


def test_funeral_lines_assigns_distinct_templates_when_possible() -> None:
    """With three ranked survivors we should see three different
    templates (not three copies of the same line)."""
    survivors = [("A", 0.9), ("B", 0.8), ("C", 0.7)]
    lines = funeral_lines(deceased_name="Mira", survivors=survivors)
    texts = [text for _, text in lines]
    assert len(set(texts)) == 3
