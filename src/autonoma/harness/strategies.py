"""Strategy registry — maps every enum-valued knob in ``HarnessPolicy`` to
a callable implementation.

Design:

- Every ``Literal[...]`` field in ``HarnessPolicyContent`` describes a
  slot of algorithmic choice (routing, stall policy, …). Each slot value
  must resolve to a callable.
- To keep drift impossible, the registry is *auto-seeded* from the
  Pydantic model at import time: we introspect every Literal annotation
  across the sub-policies and plant a ``NotImplementedError``-raising
  placeholder for each value. The placeholder is a safety net; every
  slot ships replaced by a real implementation registered via
  ``@register(section, value)`` from one of the ``*_strategies.py``
  modules. ``is_stub`` and ``all_stubs`` exist to surface any slot that
  ever falls back to the placeholder.

Two invariants are enforced by tests:

1. ``tests/test_harness_*.py`` — for each section, every Literal value
   has a registered (non-stub) implementation.
2. ``tests/test_harness_policy_consumed.py`` — every section is read at
   runtime via ``lookup()`` somewhere under ``src/autonoma``. This
   prevents "dead policy" drift: a knob the user can change in the UI
   but the runtime never consults.

The callable signature is intentionally loose (``Callable[..., Any]``)
because the correct shape varies per section — see each ``*_strategies.py``
module for its concrete signature.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, get_args, get_origin

from autonoma.harness.policy import HarnessPolicyContent

StrategyFn = Callable[..., Any]
SlotKey = tuple[str, str]  # (dotted_path, enum_value)


# ── registry state ────────────────────────────────────────────────────
_REGISTRY: dict[SlotKey, StrategyFn] = {}


def _enum_slots_from_model() -> list[SlotKey]:
    """Yield every ``(dotted_path, value)`` pair for Literal fields in
    ``HarnessPolicyContent``.

    Single source of truth — no hardcoded mirror list to drift against
    the policy model.
    """
    slots: list[SlotKey] = []
    for section_name, section_field in HarnessPolicyContent.model_fields.items():
        sub_model = section_field.annotation
        sub_fields = getattr(sub_model, "model_fields", None)
        if not sub_fields:  # pragma: no cover — every section is a BaseModel
            continue
        for field_name, field_info in sub_fields.items():
            annot = field_info.annotation
            if get_origin(annot) is Literal:
                dotted = f"{section_name}.{field_name}"
                for value in get_args(annot):
                    slots.append((dotted, value))
    return slots


def _stub(section: str, value: str) -> StrategyFn:
    """Build a placeholder that fails loudly if no ``@register`` ever
    replaces it. All shipped slots are registered; this exists only as
    a safety net so a missing import (e.g. forgetting to add a new
    ``*_strategies.py`` to ``harness/__init__.py``) raises at the call
    site instead of silently no-op'ing."""

    def impl(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"harness strategy '{section}.{value}' has no implementation. "
            "Either add an @register(...) for it, or import the module "
            "that does in autonoma.harness.__init__."
        )

    impl.__name__ = f"stub__{section.replace('.', '_')}__{value}"
    return impl


def _seed_registry() -> None:
    for section, value in _enum_slots_from_model():
        _REGISTRY[(section, value)] = _stub(section, value)


_seed_registry()


# ── public helpers ────────────────────────────────────────────────────


def register(section: str, value: str) -> Callable[[StrategyFn], StrategyFn]:
    """Decorator — replace a registry slot with a real implementation.

    Raises ``KeyError`` if the slot isn't known (catches typos early)."""

    def deco(fn: StrategyFn) -> StrategyFn:
        if (section, value) not in _REGISTRY:
            raise KeyError(
                f"unknown strategy slot: '{section}.{value}' "
                "(check HarnessPolicyContent for typos)"
            )
        _REGISTRY[(section, value)] = fn
        return fn

    return deco


def lookup(section: str, value: str) -> StrategyFn:
    """Return the callable registered for ``section.value``.

    Raises ``KeyError`` if nothing is registered (including stubs). If a
    Phase 2 stub is still in place, the returned callable raises
    ``NotImplementedError`` when invoked.
    """
    try:
        return _REGISTRY[(section, value)]
    except KeyError as e:
        raise KeyError(f"no strategy registered for '{section}.{value}'") from e


def is_stub(section: str, value: str) -> bool:
    """True iff the registered entry is still the unimplemented placeholder."""
    fn = _REGISTRY.get((section, value))
    if fn is None:
        return False
    return fn.__name__.startswith("stub__")


def all_slots() -> list[SlotKey]:
    """Snapshot of every (section, value) the registry currently carries."""
    return list(_REGISTRY.keys())


def all_stubs() -> list[SlotKey]:
    """Slots that still resolve to the unimplemented placeholder.
    Should be empty in shipped builds — non-empty means a strategy
    module is missing from ``harness/__init__.py`` or a new policy
    value was added without an implementation."""
    return [key for key in _REGISTRY if is_stub(*key)]
