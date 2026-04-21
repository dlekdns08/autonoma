"""Tests for the harness strategy registry.

Focus:
- Every Literal value in ``HarnessPolicyContent`` has a registry entry
  (completeness). This guards against adding an enum value to the
  policy without giving it an implementation.
- Placeholder stubs fail loudly when called — Phase 3 will replace
  them, but a silent no-op at runtime is worse than an explicit error.
- ``register`` catches typos in slot names instead of silently creating
  a new, never-reached entry.
"""

from __future__ import annotations

from typing import Literal, get_args, get_origin

import pytest


def test_registry_has_entry_for_every_literal_value() -> None:
    from autonoma.harness.policy import HarnessPolicyContent
    from autonoma.harness.strategies import all_slots

    expected: set[tuple[str, str]] = set()
    for section_name, section_field in HarnessPolicyContent.model_fields.items():
        sub_model = section_field.annotation
        for field_name, field_info in sub_model.model_fields.items():
            annot = field_info.annotation
            if get_origin(annot) is Literal:
                for value in get_args(annot):
                    expected.add((f"{section_name}.{field_name}", value))

    actual = set(all_slots())
    assert expected == actual, (
        f"registry drift: missing {expected - actual}, extra {actual - expected}"
    )


def test_phase3_implemented_slots_are_no_longer_stubs() -> None:
    """Phase 3 replaces stubs section-by-section. Each section landed
    here should appear in ``IMPLEMENTED`` and be absent from
    ``all_stubs()``. Grow this set as more sections land; once it equals
    ``all_slots()`` the registry is fully implemented."""
    from autonoma.harness.strategies import all_slots, all_stubs

    IMPLEMENTED: set[tuple[str, str]] = {
        ("routing.strategy", "priority"),
        ("routing.strategy", "round_robin"),
        ("routing.strategy", "broadcast"),
        ("loop.exit_condition", "all_tasks_done"),
        ("loop.exit_condition", "director_decides"),
        ("loop.exit_condition", "max_rounds_only"),
        ("safety.code_execution", "sandbox"),
        ("safety.code_execution", "disabled"),
        ("loop.stall_policy", "auto_unblock"),
        ("loop.stall_policy", "wait"),
        ("loop.stall_policy", "escalate_to_director"),
        ("spawn.approval_mode", "director_only"),
        ("spawn.approval_mode", "peer_vote"),
        ("spawn.approval_mode", "automatic"),
        ("action.json_extraction", "direct"),
        ("action.json_extraction", "fenced_first"),
        ("action.json_extraction", "fallback_chain"),
        ("decision.on_parse_failure", "skip_turn"),
        ("decision.on_parse_failure", "force_idle"),
        ("decision.on_parse_failure", "abort"),
        ("decision.message_priority", "urgency_ordered"),
        ("decision.message_priority", "fifo"),
        ("decision.message_priority", "round_robin"),
        ("action.llm_error_handling", "backoff"),
        ("action.llm_error_handling", "rate_limit_sleep"),
        ("action.llm_error_handling", "abort"),
        ("action.harness_enforcement", "strict"),
        ("action.harness_enforcement", "permissive"),
        ("action.harness_enforcement", "off"),
        ("memory.summarization", "none"),
        ("memory.summarization", "tail_window"),
        ("memory.summarization", "rolling_summary"),
        ("safety.enforcement_level", "strict"),
        ("safety.enforcement_level", "permissive"),
        ("safety.enforcement_level", "off"),
        ("mood.transition_strategy", "sticky"),
        ("mood.transition_strategy", "reactive"),
        ("mood.transition_strategy", "random_walk"),
        ("system.prompt_variant", "balanced"),
        ("system.prompt_variant", "concise"),
        ("system.prompt_variant", "elaborate"),
        ("cache.provider_cache", "enabled"),
        ("cache.provider_cache", "disabled"),
        ("budget.enforcement", "soft_warn"),
        ("budget.enforcement", "hard_stop"),
        ("budget.enforcement", "off"),
        ("checkpoint.include_full_state", "on"),
        ("checkpoint.include_full_state", "off"),
    }

    stubs = set(all_stubs())
    slots = set(all_slots())

    assert IMPLEMENTED.isdisjoint(stubs), (
        f"these should be implemented but are still stubs: {IMPLEMENTED & stubs}"
    )
    assert stubs == slots - IMPLEMENTED


def test_no_stubs_remain() -> None:
    """Phase 3 is complete — every registry slot must resolve to a
    real implementation. Any stub that sneaks back in via a new policy
    enum value with no matching @register will fail here."""
    from autonoma.harness.strategies import all_stubs

    assert all_stubs() == []


def test_lookup_missing_slot_raises_keyerror() -> None:
    from autonoma.harness.strategies import lookup

    with pytest.raises(KeyError):
        lookup("not.a.real.section", "nope")


def test_register_unknown_slot_raises() -> None:
    from autonoma.harness.strategies import register

    with pytest.raises(KeyError):
        @register("imaginary.section", "value")
        def _impl() -> None:
            return None


def test_register_replaces_entry_and_lookup_returns_new_impl() -> None:
    """@register must overwrite the current registry entry and lookup
    returns the new callable. Uses an arbitrary slot and restores the
    original after, so this test doesn't bleed state into others."""
    from autonoma.harness import strategies as s

    slot = s.all_slots()[0]
    original = s.lookup(*slot)
    try:
        @s.register(*slot)
        def _impl(*args, **kwargs) -> str:
            return "swapped"

        assert s.lookup(*slot) is _impl
    finally:
        s._REGISTRY[slot] = original
        assert s.lookup(*slot) is original
