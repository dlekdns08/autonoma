"""Tests for ``autonoma.memory.memoir`` — Feature #3 (memoir compaction).

We seed the ``characters`` and ``agent_journal`` tables directly via the
SQLAlchemy core API rather than through the registry so we can keep the
fixtures small and focused on the compaction loop.

A fake LLM client returns a canned string. Compaction is gated on the
character-count threshold from ``settings.memoir_compact_min_journal_chars``
so each test uses a known-good chunk size.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert

from autonoma.config import settings
from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import agent_journal, characters
from autonoma.event_bus import bus
from autonoma.memory.memoir import (
    MemoirRecord,
    compact_memoir,
    get_latest_memoir,
    list_memoir_versions,
    should_compact,
)

# ── Fakes / helpers ───────────────────────────────────────────────────


class FakeLLM:
    """Canned-response stub. Records every prompt for assertions."""

    def __init__(self, response: str = "I learned, I tried, I grew, I remember.") -> None:
        self._response = response
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response


async def _seed_character(
    *,
    name: str = "Zara",
    role: str = "coder",
) -> str:
    """Insert a minimal ``characters`` row and return its uuid."""
    await init_db()
    cuuid = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(characters).values(
                character_uuid=cuuid,
                seed_hash="seed-" + cuuid[:8],
                name=name,
                role=role,
                species="fox",
                species_emoji="🦊",
                catchphrase="kon",
                rarity="common",
                level=1,
                total_xp_earned=0,
                stats_json=json.dumps({}),
                traits_json=json.dumps([]),
                is_alive=1,
            )
        )
    return cuuid


async def _add_journal(
    character_uuid: str,
    text: str,
    *,
    kind: str = "diary",
    mood: str = "",
    round_number: int = 1,
) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(agent_journal).values(
                character_uuid=character_uuid,
                project_uuid=None,
                kind=kind,
                round_number=round_number,
                mood=mood,
                text=text,
            )
        )


# ── Tests ─────────────────────────────────────────────────────────────


async def test_compact_with_no_journal_returns_none(fresh_db: Path) -> None:
    """A character with zero journal rows can't be compacted."""
    cuuid = await _seed_character()
    fake = FakeLLM()

    result = await compact_memoir(cuuid, fake)
    assert result is None
    assert fake.prompts == []  # LLM never called

    text, version = await get_latest_memoir(cuuid)
    assert text == ""
    assert version == 0


async def test_should_compact_threshold_and_compact_then_dedup(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Threshold gate, full compaction round-trip, dedup on second call."""
    # Lower the threshold so the test stays fast (5000 chars > 1000).
    monkeypatch.setattr(settings, "memoir_compact_min_journal_chars", 1000)

    cuuid = await _seed_character(name="Mira", role="hunter")
    fake = FakeLLM(response="I climbed the tower and learned its silence.")

    # Below threshold → not yet ready, compact returns None.
    await _add_journal(cuuid, "small note", round_number=1)
    assert await should_compact(cuuid) is False
    assert await compact_memoir(cuuid, fake) is None
    assert fake.prompts == []

    # Push past threshold by adding 5000+ chars across several rows.
    chunk = "A" * 600
    for i in range(2, 12):  # ten more rows = 6000 chars
        await _add_journal(cuuid, chunk, round_number=i, mood="focused")

    assert await should_compact(cuuid) is True

    # Track the bus emission.
    seen: list[dict] = []

    async def _capture(**data):
        seen.append(data)

    bus.on("character.memoir_compacted", _capture)

    record = await compact_memoir(cuuid, fake)
    assert isinstance(record, MemoirRecord)
    assert record.version == 1
    assert record.text == "I climbed the tower and learned its silence."
    assert record.journal_id_start >= 1
    assert record.journal_id_end >= record.journal_id_start
    assert record.token_estimate == len(record.text) // 4
    assert len(fake.prompts) == 1
    # Prompt should mention name, role, and embed the journal entries.
    prompt = fake.prompts[0]
    assert "Mira" in prompt
    assert "hunter" in prompt
    assert "Earlier memoir: (none)" in prompt
    assert "AAA" in prompt  # the chunk text leaked through

    # Mirror columns updated.
    text, version = await get_latest_memoir(cuuid)
    assert version == 1
    assert text == record.text

    # Bus event emitted with the right payload shape.
    assert seen and seen[0]["character_uuid"] == cuuid
    assert seen[0]["version"] == 1
    assert seen[0]["length"] == len(record.text)

    # Second call with no new journal rows → dedup, returns None,
    # LLM not invoked again.
    again = await compact_memoir(cuuid, fake)
    assert again is None
    assert len(fake.prompts) == 1  # unchanged

    # ``should_compact`` is also False now because the journal hasn't grown.
    assert await should_compact(cuuid) is False

    # Even a small new entry below threshold shouldn't trigger un-forced.
    await _add_journal(cuuid, "tiny update", round_number=99)
    assert await should_compact(cuuid) is False
    assert await compact_memoir(cuuid, fake) is None
    assert len(fake.prompts) == 1

    # Forcing past the threshold yields v2 and includes the previous
    # memoir in the prompt.
    fake2 = FakeLLM(response="On reflection, I am still climbing.")
    forced = await compact_memoir(cuuid, fake2, force=True)
    assert forced is not None
    assert forced.version == 2
    assert forced.text == "On reflection, I am still climbing."
    assert "Earlier memoir: I climbed the tower" in fake2.prompts[0]

    text2, version2 = await get_latest_memoir(cuuid)
    assert version2 == 2
    assert text2 == forced.text


async def test_list_memoir_versions_orders_oldest_first(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``list_memoir_versions`` returns the full trail, version-ascending."""
    monkeypatch.setattr(settings, "memoir_compact_min_journal_chars", 100)

    cuuid = await _seed_character(name="Sora", role="scribe")

    # Build three compactions, each fed by a fresh batch of journal rows.
    for round_idx, response in enumerate(
        ("First memoir paragraph.", "Second pass.", "Third reflection."),
        start=1,
    ):
        # Add ~600 chars of new journal each round.
        for i in range(5):
            await _add_journal(
                cuuid,
                "Z" * 150,
                round_number=round_idx * 10 + i,
            )
        record = await compact_memoir(cuuid, FakeLLM(response=response))
        assert record is not None
        assert record.version == round_idx

    versions = await list_memoir_versions(cuuid)
    assert [v.version for v in versions] == [1, 2, 3]
    assert versions[0].text == "First memoir paragraph."
    assert versions[1].text == "Second pass."
    assert versions[2].text == "Third reflection."

    # Mirror reflects the latest.
    text, version = await get_latest_memoir(cuuid)
    assert version == 3
    assert text == "Third reflection."

    # Each version's source range is strictly after the previous one's.
    assert versions[0].journal_id_end < versions[1].journal_id_start
    assert versions[1].journal_id_end < versions[2].journal_id_start


async def test_compact_skips_when_llm_raises(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the LLM raises during compaction, no side effects must
    persist: no ``character_memoirs`` row, no ``memoir_version`` bump,
    no ``character.memoir_compacted`` bus event.

    The current contract (see ``compact_memoir``) propagates the
    underlying exception via ``raise``. This test pins that contract
    AND validates the absence of side effects.
    """
    from autonoma.db.schema import character_memoirs

    monkeypatch.setattr(settings, "memoir_compact_min_journal_chars", 1000)

    cuuid = await _seed_character(name="Faulty", role="explorer")

    # Push past threshold so ``should_compact`` is True.
    chunk = "B" * 600
    for i in range(1, 11):  # 6000 chars total
        await _add_journal(cuuid, chunk, round_number=i)
    assert await should_compact(cuuid) is True

    class ExplodingLLM:
        async def complete(self, prompt: str) -> str:
            raise RuntimeError("upstream timeout")

    seen: list[dict] = []

    async def _capture(**data):
        seen.append(data)

    bus.on("character.memoir_compacted", _capture)

    # Contract: the exception propagates.
    with pytest.raises(RuntimeError, match="upstream timeout"):
        await compact_memoir(cuuid, ExplodingLLM())

    # No memoir row was inserted.
    from sqlalchemy import select as _select
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                _select(character_memoirs).where(
                    character_memoirs.c.character_uuid == cuuid
                )
            )
        ).all()
    assert rows == [], "no character_memoirs row should be inserted on LLM failure"

    # ``characters.memoir_version`` was NOT bumped — still 0.
    text, version = await get_latest_memoir(cuuid)
    assert version == 0
    assert text == ""

    # No bus event was emitted.
    assert seen == [], (
        "character.memoir_compacted must NOT fire when the LLM raises"
    )


async def test_get_latest_memoir_for_unknown_character_returns_zero(
    fresh_db: Path,
) -> None:
    """Unknown characters short-circuit to ``("", 0)`` without raising."""
    text, version = await get_latest_memoir("does-not-exist")
    assert text == ""
    assert version == 0
