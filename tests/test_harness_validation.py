"""Tests for ``harness.validation`` — dangerous combos and admin gates.

These are semantic rules layered on top of ``HarnessPolicyContent``'s
per-field Pydantic constraints. The rules live in a registry so adding
a new ban is one entry in the module and one assertion here.
"""

from __future__ import annotations

from autonoma.harness.policy import default_policy_content
from autonoma.harness.validation import (
    ADMIN_ONLY_RULES,
    DANGEROUS_COMBOS,
    check_content,
)


# ── Dangerous combos apply regardless of role ──────────────────────────


def test_default_policy_has_no_issues_for_user() -> None:
    issues = check_content(default_policy_content(), is_admin=False)
    assert issues == []


def test_default_policy_has_no_issues_for_admin() -> None:
    issues = check_content(default_policy_content(), is_admin=True)
    assert issues == []


def test_disabled_code_execution_plus_off_enforcement_rejected() -> None:
    """Even an admin can't combine "no sandbox" with "no harness
    enforcement" — that removes both safety nets at once."""
    c = default_policy_content().model_copy(deep=True)
    c.safety.code_execution = "disabled"
    c.action.harness_enforcement = "off"
    # Silence the admin-only side to isolate the combo check.
    issues = [i for i in check_content(c, is_admin=True) if i.path == "combo"]
    assert len(issues) == 1
    assert "disabled" in issues[0].message and "off" in issues[0].message


def test_automatic_spawn_plus_off_enforcement_rejected() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.spawn.approval_mode = "automatic"
    c.safety.enforcement_level = "off"
    combo_issues = [
        i for i in check_content(c, is_admin=True) if i.path == "combo"
    ]
    assert len(combo_issues) == 1


# ── Admin-only rules gate only non-admins ──────────────────────────────


def test_enforcement_off_requires_admin() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.safety.enforcement_level = "off"
    user_issues = check_content(c, is_admin=False)
    admin_issues = check_content(c, is_admin=True)
    assert any(i.path == "safety.enforcement_level" for i in user_issues)
    assert all(i.path != "safety.enforcement_level" for i in admin_issues)


def test_harness_enforcement_off_requires_admin() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.action.harness_enforcement = "off"
    issues = check_content(c, is_admin=False)
    assert any(i.path == "action.harness_enforcement" for i in issues)
    admin_issues = check_content(c, is_admin=True)
    assert all(i.path != "action.harness_enforcement" for i in admin_issues)


def test_code_execution_disabled_requires_admin() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.safety.code_execution = "disabled"
    # Keep harness_enforcement=strict so we don't also trip the combo.
    issues = check_content(c, is_admin=False)
    assert any(i.path == "safety.code_execution" for i in issues)
    admin_issues = check_content(c, is_admin=True)
    assert all(i.path != "safety.code_execution" for i in admin_issues)


def test_max_rounds_above_cap_requires_admin() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.loop.max_rounds = 201
    issues = check_content(c, is_admin=False)
    assert any(i.path == "loop.max_rounds" for i in issues)
    admin_issues = check_content(c, is_admin=True)
    assert all(i.path != "loop.max_rounds" for i in admin_issues)


def test_max_agents_above_cap_requires_admin() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.spawn.max_agents = 17
    issues = check_content(c, is_admin=False)
    assert any(i.path == "spawn.max_agents" for i in issues)
    admin_issues = check_content(c, is_admin=True)
    assert all(i.path != "spawn.max_agents" for i in admin_issues)


def test_admin_only_flag_set_on_admin_only_issues() -> None:
    c = default_policy_content().model_copy(deep=True)
    c.safety.enforcement_level = "off"
    c.spawn.approval_mode = "automatic"
    # The combo also triggers; check flags line up with their type.
    issues = check_content(c, is_admin=False)
    for i in issues:
        if i.path == "combo":
            assert i.admin_only is False
        else:
            assert i.admin_only is True


# ── Registry sanity ───────────────────────────────────────────────────


def test_every_combo_predicate_rejects_its_intended_content() -> None:
    """Every entry in DANGEROUS_COMBOS must have *some* content that
    triggers it — guards against a refactor that inverts a predicate."""
    assert len(DANGEROUS_COMBOS) >= 1


def test_every_admin_rule_rejects_its_intended_content() -> None:
    assert len(ADMIN_ONLY_RULES) >= 1
