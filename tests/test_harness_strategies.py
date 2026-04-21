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


def test_all_entries_start_as_phase2_stubs() -> None:
    """At the end of Phase 2, nothing is implemented yet — every slot
    is still a stub. This test flips to listing *remaining* stubs once
    Phase 3 starts landing real implementations."""
    from autonoma.harness.strategies import all_slots, all_stubs

    assert set(all_slots()) == set(all_stubs())


def test_stub_raises_not_implemented_when_called() -> None:
    from autonoma.harness.strategies import lookup

    fn = lookup("routing.strategy", "priority")
    with pytest.raises(NotImplementedError, match="routing.strategy.*priority"):
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

    The registry is a module-level singleton so we restore the stub at
    the end to avoid bleed into other tests."""
    from autonoma.harness import strategies as s

    slot = ("routing.strategy", "round_robin")
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
