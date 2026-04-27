"""Contract test: every Literal field in HarnessPolicyContent must be
consumed by at least one ``lookup`` / ``_strategy_lookup`` call in the
runtime code.

Why this exists
---------------
Without this guard, a policy field can be defined, registered with
strategy implementations, surfaced in the UI, and still do nothing —
because no call site reads it. Three fields drifted into that state
before this test landed (``system.prompt_variant``, ``cache.provider_cache``,
``budget.enforcement``) — the user could change the value but runtime
behavior was identical.

How it works
------------
1. Walk ``HarnessPolicyContent`` for every ``Literal[...]`` field; build
   the set of declared section paths (e.g. ``"loop.exit_condition"``).
2. Statically scan ``src/autonoma`` for ``lookup("section.field"`` /
   ``_strategy_lookup("section.field"`` calls; build the consumed set.
3. Assert ``declared == consumed`` modulo a small explicitly-allowed
   exception set for sections still mid-wiring. Removing an entry from
   the exception set is the natural ratchet — once a section is wired
   it shouldn't appear here again.

The scan is static (no AST) — it greps the source. That's deliberate:
detection should not depend on import-time side effects, since the
whole point of dead-policy drift is that the call site never runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, get_args, get_origin

from autonoma.harness.policy import HarnessPolicyContent


SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autonoma"


# Sections that are declared in the policy model but not yet wired into
# any runtime call site. Each entry is a known gap with a tracking note.
# As each section is wired, remove its entry from this set; the test
# will then prevent regressions.
KNOWN_UNWIRED_SECTIONS: set[str] = {
    "budget.enforcement",      # Phase 3 wiring pending — needs token accumulator
}


def _declared_literal_sections() -> set[str]:
    """Section paths (e.g. ``"loop.exit_condition"``) for every Literal
    field in HarnessPolicyContent.

    Numeric / bool / string fields aren't included — they aren't backed
    by the strategy registry and have no ``lookup`` to wire."""
    sections: set[str] = set()
    for section_name, section_field in HarnessPolicyContent.model_fields.items():
        sub_model = section_field.annotation
        sub_fields = getattr(sub_model, "model_fields", None)
        if not sub_fields:
            continue
        for field_name, field_info in sub_fields.items():
            if get_origin(field_info.annotation) is Literal:
                sections.add(f"{section_name}.{field_name}")
    return sections


_LOOKUP_CALL_RE = re.compile(
    r"""(?:_strategy_lookup|\blookup)\s*\(\s*["']([a-z_]+\.[a-z_]+)["']""",
    re.MULTILINE,
)


def _consumed_sections() -> set[str]:
    """All ``"section.field"`` literals appearing as the first argument
    of a ``lookup`` / ``_strategy_lookup`` call anywhere under
    ``src/autonoma``, excluding the strategy implementation files
    themselves (those use ``register``, not ``lookup``)."""
    excluded_dir = SRC_ROOT / "harness"
    excluded_files = {
        excluded_dir / "strategies.py",
        excluded_dir / "__init__.py",
    }
    excluded_files.update(p for p in excluded_dir.glob("*_strategies.py"))

    consumed: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        if path in excluded_files:
            continue
        text = path.read_text(encoding="utf-8")
        consumed.update(_LOOKUP_CALL_RE.findall(text))
    return consumed


def test_every_consumed_section_is_declared():
    """A typo in a ``lookup("loop.exit_conditon", ...)`` would silently
    raise KeyError at runtime — catch it at parse time instead."""
    declared = _declared_literal_sections()
    consumed = _consumed_sections()
    unknown = consumed - declared
    assert not unknown, (
        f"lookup() called with section paths that don't exist in "
        f"HarnessPolicyContent: {sorted(unknown)}. Either add the field "
        f"to the policy model or fix the lookup string."
    )


def test_no_dead_policy_drift():
    """Every Literal field in HarnessPolicyContent must be consumed by
    at least one runtime call site, except those listed in
    KNOWN_UNWIRED_SECTIONS.

    If this test fails with a section newly missing from the consumed
    set, you've regressed a previously-wired policy. If it fails with a
    section the test "didn't expect to see consumed," you've wired a
    formerly-dead policy — congratulations, remove it from
    KNOWN_UNWIRED_SECTIONS.
    """
    declared = _declared_literal_sections()
    consumed = _consumed_sections()
    actually_unwired = declared - consumed
    assert actually_unwired == KNOWN_UNWIRED_SECTIONS, (
        f"Dead-policy drift detected.\n"
        f"  Declared but not consumed: {sorted(actually_unwired)}\n"
        f"  Expected unwired set:     {sorted(KNOWN_UNWIRED_SECTIONS)}\n"
        f"  Newly unwired (regression): "
        f"{sorted(actually_unwired - KNOWN_UNWIRED_SECTIONS)}\n"
        f"  Newly wired (remove from KNOWN_UNWIRED_SECTIONS): "
        f"{sorted(KNOWN_UNWIRED_SECTIONS - actually_unwired)}"
    )
