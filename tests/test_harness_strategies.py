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
    }

    stubs = set(all_stubs())
    slots = set(all_slots())

    assert IMPLEMENTED.isdisjoint(stubs), (
        f"these should be implemented but are still stubs: {IMPLEMENTED & stubs}"
    )
    assert stubs == slots - IMPLEMENTED


def test_stub_raises_not_implemented_when_called() -> None:
    """Pick any slot that's still a stub (implementations land one
    section at a time; pick one from the remaining pool so this test
    doesn't need updating on every section)."""
    from autonoma.harness.strategies import all_stubs, lookup

    stubs = all_stubs()
    assert stubs, "no stubs left — Phase 3 is done; retire this test"
    section, value = stubs[0]
    fn = lookup(section, value)
    with pytest.raises(NotImplementedError, match=f"{section}.*{value}"):
        fn()


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


def test_register_replaces_stub_and_lookup_returns_impl() -> None:
    """Phase 3 uses @register to swap stubs for real implementations.
    Exercise the round-trip with a throwaway slot.

    The registry is a module-level singleton so we restore the original
    entry at the end to avoid bleed into other tests."""
    from autonoma.harness import strategies as s

    # Pick any still-stubbed slot — that way this test doesn't need
    # updating every time a section gets implemented.
    stubs = s.all_stubs()
    assert stubs, "no stubs left — pick an implemented slot or retire this test"
    slot = stubs[0]
    original = s.lookup(*slot)
    try:
        @s.register(*slot)
        def _impl(label: str = "hit") -> str:
            return label

        got = s.lookup(*slot)
        assert got("OK") == "OK"
        assert s.is_stub(*slot) is False
    finally:
        # restore stub so this test doesn't poison the completeness check
        s._REGISTRY[slot] = original
        assert s.is_stub(*slot) is True
