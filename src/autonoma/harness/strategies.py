"""Strategy registry — maps every enum-valued knob in ``HarnessPolicy`` to
a callable implementation.

Design:

- Every ``Literal[...]`` field in ``HarnessPolicyContent`` describes a
  slot of algorithmic choice (routing, stall policy, …). Each slot value
  must resolve to a callable.
- To keep drift impossible, the registry is *auto-seeded* from the
  Pydantic model at import time: we introspect every Literal annotation
  across the sub-policies and plant a ``NotImplementedError``-raising
  stub for each value.
- Phase 3 replaces the stubs with real implementations using
  ``@register(section, value)`` — calls that still land on a stub throw
  a clear "fill me in" error instead of silently no-op'ing.

The callable signature is intentionally loose (``Callable[..., Any]``)
because the correct shape depends on the call site, which only becomes
concrete during the Phase 3 runtime refactor. The registry's job here is
*completeness*, not shape.
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
    """Build a placeholder that fails loudly if called before Phase 3
    plugs a real implementation in."""

    def impl(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"harness strategy '{section}.{value}' has no implementation yet "
            "(Phase 2 stub — fill in during Phase 3 runtime refactor)"
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
    """True iff the registered entry is still the Phase 2 placeholder."""
    fn = _REGISTRY.get((section, value))
    if fn is None:
        return False
    return fn.__name__.startswith("stub__")


def all_slots() -> list[SlotKey]:
    """Snapshot of every (section, value) the registry currently carries."""
    return list(_REGISTRY.keys())


def all_stubs() -> list[SlotKey]:
    """Slots that still use a Phase 2 stub. Phase 3 PRs should shrink
    this list to zero."""
    return [key for key in _REGISTRY if is_stub(*key)]
