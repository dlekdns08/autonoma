"""Validation rules layered on top of ``HarnessPolicyContent``.

Pydantic handles shape/type/range. This module adds two orthogonal
checks that can't be expressed with per-field constraints:

- **Dangerous combinations** — individual field values are legal but
  the combination disables every safety net (e.g. ``safety.code_execution
  = disabled`` *and* ``action.harness_enforcement = off``). Rejected
  for everyone.
- **Admin-only values/ranges** — individual field values that are
  *intentionally* dangerous and may only be set by an administrator
  (e.g. ``safety.enforcement_level = off``, or very large
  ``loop.max_rounds``). Rejected for non-admin callers.

Both are enforced at the two ingress points where policy is shaped by a
user: ``_resolve_start_policy`` (per-run overrides) and the preset
create/update HTTP endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from autonoma.harness.policy import HarnessPolicyContent


@dataclass(frozen=True)
class ValidationIssue:
    """One reason the policy was rejected.

    ``path`` is a dotted section.field locator (``"safety.code_execution"``)
    for field-level issues, or ``"combo"`` for cross-section bans.
    ``admin_only`` distinguishes "nobody may do this" (False) from
    "only an admin may do this" (True) — the caller uses it to decide
    whether to return 403 vs 422.
    """

    path: str
    message: str
    admin_only: bool = False


# ── Dangerous combinations ─────────────────────────────────────────────
#
# Each entry is (message, predicate). Predicate returns True when the
# combination is present in ``content``.

DangerousComboPredicate = Callable[[HarnessPolicyContent], bool]

DANGEROUS_COMBOS: list[tuple[str, DangerousComboPredicate]] = [
    (
        "code_execution=disabled + harness_enforcement=off disables both "
        "layers of the safety net — refusing.",
        lambda c: (
            c.safety.code_execution == "disabled"
            and c.action.harness_enforcement == "off"
        ),
    ),
    (
        "spawn.approval_mode=automatic + safety.enforcement_level=off "
        "removes every gate on uncontrolled agent growth — refusing.",
        lambda c: (
            c.spawn.approval_mode == "automatic"
            and c.safety.enforcement_level == "off"
        ),
    ),
]


# ── Admin-only values ──────────────────────────────────────────────────
#
# Each entry is (path, message, predicate). Predicate returns True when
# the content trips the rule. Surfaced to the caller with
# ``admin_only=True`` so the API layer can render a 403.

AdminOnlyPredicate = Callable[[HarnessPolicyContent], bool]

ADMIN_ONLY_RULES: list[tuple[str, str, AdminOnlyPredicate]] = [
    (
        "safety.enforcement_level",
        "Turning enforcement off at runtime requires admin privileges.",
        lambda c: c.safety.enforcement_level == "off",
    ),
    (
        "action.harness_enforcement",
        "Disabling harness enforcement requires admin privileges.",
        lambda c: c.action.harness_enforcement == "off",
    ),
    (
        "safety.code_execution",
        "Disabling sandboxed code execution requires admin privileges.",
        lambda c: c.safety.code_execution == "disabled",
    ),
    (
        "loop.max_rounds",
        "max_rounds above 200 requires admin privileges (resource cap).",
        lambda c: c.loop.max_rounds > 200,
    ),
    (
        "spawn.max_agents",
        "max_agents above 16 requires admin privileges (resource cap).",
        lambda c: c.spawn.max_agents > 16,
    ),
]


def check_content(
    content: HarnessPolicyContent, *, is_admin: bool
) -> list[ValidationIssue]:
    """Return the list of issues, or an empty list if the policy is OK.

    Dangerous combinations are always surfaced. Admin-only rules are
    surfaced only when ``is_admin`` is False so admins see a clean list
    even when they *do* intentionally set those fields.
    """
    issues: list[ValidationIssue] = []
    for msg, predicate in DANGEROUS_COMBOS:
        if predicate(content):
            issues.append(ValidationIssue(path="combo", message=msg))
    if not is_admin:
        for path, msg, predicate in ADMIN_ONLY_RULES:
            if predicate(content):
                issues.append(
                    ValidationIssue(path=path, message=msg, admin_only=True)
                )
    return issues
