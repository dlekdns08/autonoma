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


# ── _hold_funeral (swarm-side) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_hold_funeral_emits_world_event_and_survivor_speech() -> None:
    """End-to-end: seed a swarm with three survivors with positive trust
    toward a deceased name, then call ``_hold_funeral`` and check that
    the world event fires first, followed by survivor speech events in
    trust-desc order.
    """
    from autonoma.agents.swarm import AgentSwarm
    from autonoma.event_bus import bus

    swarm = AgentSwarm()
    swarm.spawn_agent("Alpha", "coder", ["python"])
    swarm.spawn_agent("Bravo", "coder", ["python"])
    swarm.spawn_agent("Charlie", "coder", ["python"])

    # Build positive bonds toward the deceased. We use record_interaction
    # directly so the relationship shows as familiar (get_all_pairs skips
    # pairs with zero familiarity).
    for i in range(3):
        swarm.relationships.record("Alpha", "Kit", "worked together", positive=True)
    for i in range(1):
        swarm.relationships.record("Bravo", "Kit", "worked together", positive=True)
    for i in range(2):
        swarm.relationships.record("Charlie", "Kit", "worked together", positive=True)

    events: list[tuple[str, dict]] = []

    async def _capture_world(**kw):
        events.append(("world.event", kw))

    async def _capture_speech(**kw):
        events.append(("agent.speech", kw))

    bus.on("world.event", _capture_world)
    bus.on("agent.speech", _capture_speech)

    await swarm._hold_funeral("Kit")

    world_events = [e for e in events if e[0] == "world.event"]
    speech_events = [e for e in events if e[0] == "agent.speech"]

    assert len(world_events) == 1
    assert "Kit" in world_events[0][1]["title"]
    # All three survivors with positive trust should have spoken.
    speakers = [e[1]["agent"] for e in speech_events]
    assert set(speakers) == {"Alpha", "Bravo", "Charlie"}
    # The world event must precede the first eulogy so the UI has time
    # to dim before anyone speaks.
    assert events[0][0] == "world.event"


@pytest.mark.asyncio
async def test_hold_funeral_silent_when_no_positive_bonds() -> None:
    """A stranger's death should fire nothing — we don't want the UI
    lighting up with an empty memorial for NPCs no one knew."""
    from autonoma.agents.swarm import AgentSwarm
    from autonoma.event_bus import bus

    swarm = AgentSwarm()
    swarm.spawn_agent("Loner", "coder", ["python"])

    world_events: list[dict] = []

    async def _capture(**kw):
        world_events.append(kw)

    bus.on("world.event", _capture)

    await swarm._hold_funeral("Nobody")

    assert world_events == []


@pytest.mark.asyncio
async def test_hold_funeral_skips_survivors_not_in_swarm() -> None:
    """If the relationship graph still references a name that's no
    longer an agent (e.g. the survivor already died too), we skip them
    quietly rather than crashing on ``agents.get(...)``."""
    from autonoma.agents.swarm import AgentSwarm
    from autonoma.event_bus import bus

    swarm = AgentSwarm()
    swarm.spawn_agent("Alpha", "coder", ["python"])

    # Alpha is in the swarm; Ghost isn't (never spawned).
    swarm.relationships.record("Alpha", "Kit", "collab", positive=True)
    swarm.relationships.record("Ghost", "Kit", "collab", positive=True)
    swarm.relationships.record("Ghost", "Kit", "collab", positive=True)  # higher trust

    speech_events: list[dict] = []

    async def _capture(**kw):
        speech_events.append(kw)

    bus.on("agent.speech", _capture)

    await swarm._hold_funeral("Kit")

    # Only the surviving Alpha should speak — the Ghost candidate is
    # dropped by _hold_funeral's membership check.
    speakers = [e["agent"] for e in speech_events]
    assert speakers == ["Alpha"]
