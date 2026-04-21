"""Tests for ``action.json_extraction`` strategies.

These cover the three variants and the module-level ``_extract_json``
indirection that ``AutonomousAgent`` and ``DirectorAgent`` share.
"""

from __future__ import annotations

import pytest

from autonoma.agents.base import _extract_json
from autonoma.harness import action_strategies  # noqa: F401 — ensures registration
from autonoma.harness.strategies import is_stub, lookup


def test_strategies_registered() -> None:
    for v in ("direct", "fenced_first", "fallback_chain"):
        assert is_stub("action.json_extraction", v) is False, v


# ── direct ────────────────────────────────────────────────────────────


def test_direct_parses_clean_json() -> None:
    fn = lookup("action.json_extraction", "direct")
    assert fn('{"x": 1}') == {"x": 1}


def test_direct_rejects_fenced_block() -> None:
    fn = lookup("action.json_extraction", "direct")
    with pytest.raises(ValueError, match="Could not extract JSON via direct"):
        fn('```json\n{"x": 1}\n```')


def test_direct_rejects_prose_wrapper() -> None:
    fn = lookup("action.json_extraction", "direct")
    with pytest.raises(ValueError):
        fn('Here is the JSON: {"x": 1} hope that helps')


def test_direct_rejects_list_toplevel() -> None:
    """Policy: strategies return a dict. A top-level JSON array parses
    but isn't a dict, so it's rejected rather than silently returning
    something the caller can't index into."""
    fn = lookup("action.json_extraction", "direct")
    with pytest.raises(ValueError):
        fn("[1, 2, 3]")


# ── fenced_first ──────────────────────────────────────────────────────


def test_fenced_first_prefers_fence() -> None:
    fn = lookup("action.json_extraction", "fenced_first")
    wrapped = 'Thinking...\n```json\n{"x": 2}\n```\nDone.'
    assert fn(wrapped) == {"x": 2}


def test_fenced_first_falls_back_to_direct() -> None:
    fn = lookup("action.json_extraction", "fenced_first")
    assert fn('{"x": 3}') == {"x": 3}


def test_fenced_first_skips_loose_brace_recovery() -> None:
    fn = lookup("action.json_extraction", "fenced_first")
    with pytest.raises(ValueError, match="Could not extract JSON via fenced_first"):
        fn('Sure: {"x": 4} happy now?')


# ── fallback_chain ────────────────────────────────────────────────────


def test_fallback_chain_handles_prose_wrapper() -> None:
    fn = lookup("action.json_extraction", "fallback_chain")
    assert fn('Here you go: {"x": 5} ok?') == {"x": 5}


def test_fallback_chain_handles_fence() -> None:
    fn = lookup("action.json_extraction", "fallback_chain")
    assert fn('```json\n{"x": 6}\n```') == {"x": 6}


def test_fallback_chain_handles_direct() -> None:
    fn = lookup("action.json_extraction", "fallback_chain")
    assert fn('{"x": 7}') == {"x": 7}


def test_fallback_chain_rejects_garbage() -> None:
    fn = lookup("action.json_extraction", "fallback_chain")
    with pytest.raises(ValueError):
        fn("not json at all")


# ── module-level shim ─────────────────────────────────────────────────


def test_extract_json_default_matches_fallback_chain() -> None:
    """The legacy ``_extract_json(text)`` call without a strategy arg
    must continue to behave like pre-harness."""
    assert _extract_json('Prose: {"x": 8} ok') == {"x": 8}


def test_extract_json_respects_strategy_arg() -> None:
    with pytest.raises(ValueError):
        _extract_json('Prose: {"x": 9} ok', strategy="direct")
