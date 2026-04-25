"""Tests for the diary search index (Phase 0-D)."""

from __future__ import annotations

from autonoma.world.diary_search import DiarySearchIndex


def _idx_with_entries() -> DiarySearchIndex:
    idx = DiarySearchIndex()
    idx.add_entry(
        agent="Alice",
        round_number=1,
        mood="curious",
        content="Started exploring the new authentication module today.",
    )
    idx.add_entry(
        agent="Alice",
        round_number=3,
        mood="frustrated",
        content="Auth bug — the JWT token signing path keeps timing out.",
    )
    idx.add_entry(
        agent="Bob",
        round_number=2,
        mood="happy",
        content="Refactored the database migration runner. So clean now!",
    )
    idx.add_entry(
        agent="Alice",
        round_number=5,
        mood="proud",
        content="Authentication module shipped! No more timeouts.",
    )
    return idx


def test_search_returns_relevant_entry():
    idx = _idx_with_entries()
    hits = idx.search("authentication")
    assert hits, "expected at least one hit"
    # Both Alice entries that mention "authentication" should rank ahead
    # of Bob's database entry, which never mentions auth.
    top_authors = {e.agent for e, _ in hits[:2]}
    assert top_authors == {"Alice"}


def test_filter_by_agent():
    idx = _idx_with_entries()
    hits = idx.search("module", agent="Bob")
    assert all(e.agent == "Bob" for e, _ in hits)


def test_search_text_format():
    idx = _idx_with_entries()
    lines = idx.search_text("authentication", top_n=2)
    assert lines, "expected formatted lines"
    assert lines[0].startswith("R")
    assert "(" in lines[0] and ")" in lines[0]


def test_recency_boost_breaks_ties():
    idx = DiarySearchIndex()
    idx.add_entry(agent="A", round_number=1, mood="x", content="dragon spotted")
    idx.add_entry(agent="A", round_number=10, mood="x", content="dragon spotted")
    base = idx.search("dragon", recency_boost=0.0)
    assert {e.round_number for e, _ in base} == {1, 10}
    boosted = idx.search("dragon", recency_boost=10.0)
    # With a non-trivial boost the newer entry wins ranking.
    assert boosted[0][0].round_number == 10


def test_empty_query_returns_nothing():
    idx = _idx_with_entries()
    assert idx.search("") == []
    assert idx.search("   ") == []


def test_no_matching_token_returns_nothing():
    idx = _idx_with_entries()
    assert idx.search("xyloplankton-supernova") == []


def test_filter_by_mood():
    idx = _idx_with_entries()
    out = idx.filter_by_mood("frustrated")
    assert len(out) == 1
    assert out[0].agent == "Alice"


def test_clear():
    idx = _idx_with_entries()
    assert len(idx) > 0
    idx.clear()
    assert len(idx) == 0
    assert idx.search("authentication") == []
