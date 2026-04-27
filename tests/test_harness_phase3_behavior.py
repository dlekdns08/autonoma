"""Behavioral tests for the three policies wired in Phase 3:

- ``system.prompt_variant``  →  build_system_prompt suffix
- ``cache.provider_cache``   →  Anthropic ``cache_control`` block
- ``budget.enforcement``     →  swarm round-loop gate

The contract test in ``test_harness_policy_consumed.py`` proves the
sections are *consulted*; these tests prove each variant produces an
*observable* difference. Without them, a regression that locks the
runtime into one variant (ignoring the policy value) would still pass
the contract test.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from autonoma.harness.strategies import lookup


# ── system.prompt_variant ────────────────────────────────────────────


def _build_prompt(variant: str) -> str:
    """Render a minimal AgentHarness with the given prompt suffix."""
    from autonoma.agents.harness import AgentHarness

    harness = AgentHarness(
        name="test",
        role_description="r",
        emoji="🧪",
        color="white",
        allowed_capabilities=[],
    )
    suffix = lookup("system.prompt_variant", variant)()
    return harness.build_system_prompt("Tester", ["python"], prompt_suffix=suffix)


def test_prompt_variant_balanced_adds_no_suffix():
    rendered = _build_prompt("balanced")
    assert not rendered.endswith("step-by-step")
    assert "Respond concisely" not in rendered


def test_prompt_variant_concise_appends_concise_suffix():
    rendered = _build_prompt("concise")
    assert rendered.endswith(
        "Respond concisely. Prefer short, direct sentences over elaboration."
    )


def test_prompt_variant_elaborate_appends_elaborate_suffix():
    rendered = _build_prompt("elaborate")
    assert rendered.rstrip().endswith(
        "Explain reasoning step-by-step when it helps collaborators "
        "understand your choices."
    )


def test_prompt_variants_produce_distinct_prompts():
    """The whole point of the policy is that switching the value
    produces a different prompt — guards against accidentally hardcoding
    one variant on the call site."""
    a = _build_prompt("balanced")
    b = _build_prompt("concise")
    c = _build_prompt("elaborate")
    assert len({a, b, c}) == 3


# ── cache.provider_cache ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_cache_disabled_sends_plain_system_string(monkeypatch):
    """When the policy resolves to disabled, the Anthropic SDK call must
    receive ``system="..."`` as a plain string (no cache_control blocks)."""
    from autonoma import llm as llm_module

    sent: dict = {}

    async def fake_create(**kwargs):
        sent.update(kwargs)
        return MagicMock(
            content=[MagicMock(text="ok")],
            usage=MagicMock(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    client = llm_module.AnthropicLLMClient.__new__(llm_module.AnthropicLLMClient)
    client._client = MagicMock()
    client._client.messages.create = fake_create

    await client.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        temperature=0.5,
        system="STABLE PREAMBLE",
        messages=[{"role": "user", "content": "hi"}],
        cache_system_prompt=False,
    )
    assert sent["system"] == "STABLE PREAMBLE"


@pytest.mark.asyncio
async def test_anthropic_cache_enabled_wraps_system_in_cache_control(monkeypatch):
    """When the policy resolves to enabled, the system prompt must travel
    as a single cache_control block so Anthropic's prompt cache kicks in."""
    from autonoma import llm as llm_module

    sent: dict = {}

    async def fake_create(**kwargs):
        sent.update(kwargs)
        return MagicMock(
            content=[MagicMock(text="ok")],
            usage=MagicMock(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    client = llm_module.AnthropicLLMClient.__new__(llm_module.AnthropicLLMClient)
    client._client = MagicMock()
    client._client.messages.create = fake_create

    await client.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        temperature=0.5,
        system="STABLE PREAMBLE",
        messages=[{"role": "user", "content": "hi"}],
        cache_system_prompt=True,
    )
    assert isinstance(sent["system"], list)
    assert sent["system"] == [
        {
            "type": "text",
            "text": "STABLE PREAMBLE",
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ── budget.enforcement ───────────────────────────────────────────────


def test_budget_off_returns_ok_regardless_of_overage():
    fn = lookup("budget.enforcement", "off")
    assert fn(10**9, 100) == "ok"


def test_budget_soft_warn_returns_warn_only_on_overage():
    fn = lookup("budget.enforcement", "soft_warn")
    assert fn(50, 100) == "ok"
    assert fn(100, 100) == "warn"
    assert fn(500, 100) == "warn"


def test_budget_hard_stop_returns_stop_on_overage():
    fn = lookup("budget.enforcement", "hard_stop")
    assert fn(50, 100) == "ok"
    assert fn(100, 100) == "stop"


def test_budget_treats_zero_cap_as_disabled():
    """``tokens_per_run=0`` is the standard "disable the cap" sentinel
    used elsewhere in the policy. The strategies must honour it."""
    soft = lookup("budget.enforcement", "soft_warn")
    hard = lookup("budget.enforcement", "hard_stop")
    assert soft(10**9, 0) == "ok"
    assert hard(10**9, 0) == "ok"
