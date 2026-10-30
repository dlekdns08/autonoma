"""List-price cost accounting, cache-hit tracking, and the swarm dollar cap."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autonoma.agents.base import AutonomousAgent
from autonoma.agents.swarm import AgentSwarm
from autonoma.llm import LLMConfig, LLMResponse
from autonoma.models import AgentPersona
from autonoma.pricing import cost_usd, price_for


def test_prices_pick_the_longest_prefix() -> None:
    assert price_for("claude-opus-5-5") == (4.0, 20.0)
    assert price_for("claude-opus-5") == (5.0, 25.0)
    assert price_for("claude-haiku-5-5") == (0.10, 0.50)
    assert price_for("gpt-5.6") is None


def test_cost_counts_cache_reads_at_a_tenth() -> None:
    # 1M uncached in + 1M cached in + 1M out on Sonnet 5.5 ($2 / $10)
    c = cost_usd(
        "claude-sonnet-5-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert c == pytest.approx(2.0 + 0.2 + 10.0)
    assert cost_usd("vllm-local", input_tokens=10, output_tokens=10) is None


def _agent(name: str = "Ada") -> AutonomousAgent:
    cfg = LLMConfig(
        provider="anthropic",
        api_key="k",
        model="claude-sonnet-5-5",
        worker_model="claude-haiku-5-5",
    )
    return AutonomousAgent(
        AgentPersona(name=name, emoji="x", role="coder", skills=[], color="red"), llm_config=cfg
    )


def test_account_tracks_tokens_cost_and_cache() -> None:
    a = _agent()
    resp = LLMResponse(
        text="", input_tokens=2_000, output_tokens=500, cache_read_input_tokens=8_000
    )
    a._account(resp, "claude-haiku-5-5")
    assert a.total_tokens == 10_500
    assert a.prompt_tokens == 10_000 and a.cache_read_tokens == 8_000
    expected = (2_000 * 0.10 + 8_000 * 0.10 * 0.1 + 500 * 0.50) / 1_000_000
    assert a.total_cost_usd == pytest.approx(expected)
    a._account(LLMResponse(text="", input_tokens=10, output_tokens=10), "local-llama")
    assert a._unpriced_calls == 1


def test_cost_summary_aggregates_agents() -> None:
    a, b = _agent("A"), _agent("B")
    a._account(
        LLMResponse(text="", input_tokens=1_000, output_tokens=0, cache_read_input_tokens=3_000),
        "claude-sonnet-5-5",
    )
    b._total_cost_usd += 0.25  # e.g. a deep-work session
    fake = SimpleNamespace(agents={"A": a, "B": b})
    summary = AgentSwarm.cost_summary(fake)  # type: ignore[arg-type]
    assert summary["total_usd"] == pytest.approx(0.25 + (1_000 * 2 + 3_000 * 0.2) / 1e6)
    assert summary["cache_hit_ratio"] == 0.75
    assert set(summary["by_agent"]) == {"A", "B"}


@pytest.mark.asyncio
async def test_swarm_stops_at_the_dollar_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.agents import swarm as swarm_mod
    from autonoma.models import ProjectState

    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, **data: Any) -> None:
        events.append((event, data))

    monkeypatch.setattr(swarm_mod.bus, "emit", emit)
    monkeypatch.setattr(swarm_mod.settings, "swarm_budget_usd", 0.01)
    swarm = AgentSwarm()
    # Pretend the Director already spent past the cap.
    swarm.director._total_cost_usd = 0.02
    project = ProjectState(name="p", description="d", tasks=[], agents=[], files=[])
    await swarm.run(project, max_rounds=5)
    stops = [d for n, d in events if n == "swarm.budget_stop"]
    assert stops and stops[0]["cap_usd"] == 0.01 and stops[0]["cost_usd"] >= 0.02
    assert any(n == "swarm.cost" for n, _ in events)
    assert swarm._last_exit_reason == "budget_usd_exceeded"
