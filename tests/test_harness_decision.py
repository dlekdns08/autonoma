"""Tests for ``decision.on_parse_failure`` strategies.

These cover the fallback-decision behavior when ``_extract_json`` fails.
``skip_turn`` preserves pre-harness behavior byte-for-byte, ``force_idle``
is the silent variant, and ``abort`` re-raises so strict-mode callers can
surface broken LLM output instead of swallowing it.
"""

from __future__ import annotations

import pytest

from autonoma.harness import decision_strategies  # noqa: F401 — ensures registration
from autonoma.harness.strategies import is_stub, lookup


def test_strategies_registered() -> None:
    for v in ("skip_turn", "force_idle", "abort"):
        assert is_stub("decision.on_parse_failure", v) is False, v


def test_skip_turn_returns_pre_harness_dict() -> None:
    """Regression: the dict shape must match the pre-harness hardcoded
    fallback so existing downstream code (UI bubbles, telemetry) keeps
    working unchanged."""
    fn = lookup("decision.on_parse_failure", "skip_turn")
    got = fn(ValueError("bad json"), "Architect")
    assert got == {
        "action": "idle",
        "speech": "Couldn't parse my thoughts...",
        "thinking": "parse_error",
    }


def test_force_idle_is_silent() -> None:
    fn = lookup("decision.on_parse_failure", "force_idle")
    got = fn(ValueError("bad json"), "Coder")
    assert got["action"] == "idle"
    assert got["speech"] == ""
    assert got["thinking"] == "force_idle"


def test_abort_reraises_original_exception() -> None:
    fn = lookup("decision.on_parse_failure", "abort")
    exc = ValueError("malformed")
    with pytest.raises(ValueError) as info:
        fn(exc, "Reviewer")
    assert info.value is exc


def test_abort_preserves_exception_type() -> None:
    """Different parse errors should propagate with their original type
    so a debugger/tracer can distinguish JSONDecodeError from ValueError."""
    import json as _json

    fn = lookup("decision.on_parse_failure", "abort")
    exc = _json.JSONDecodeError("nope", "doc", 0)
    with pytest.raises(_json.JSONDecodeError):
        fn(exc, "Tester")


def test_strategies_ignore_agent_name_field() -> None:
    """Agent name is passed for future observability hooks; current
    strategies shouldn't vary output on it. If a future strategy wants
    to, that's fine, but the defaults shouldn't."""
    skip = lookup("decision.on_parse_failure", "skip_turn")
    force = lookup("decision.on_parse_failure", "force_idle")
    assert skip(ValueError(), "A") == skip(ValueError(), "B")
    assert force(ValueError(), "A") == force(ValueError(), "B")
