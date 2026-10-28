"""Agent decision loop on the tool-use protocol: tool calls become
decisions, speech streams from tool input, history is append-only with
``tool_result`` turns, operator notes travel as ``role: system``, and
refusals never dispatch an action."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from autonoma.agents import base as base_mod
from autonoma.agents.base import AutonomousAgent
from autonoma.agents.director import DirectorAgent
from autonoma.agents.swarm import AgentSwarm
from autonoma.llm import BaseLLMClient, LLMConfig, LLMResponse, StreamEvent, ToolCall
from autonoma.models import AgentPersona


class StubClient(BaseLLMClient):
    """Replays canned responses, streaming tool input like the real SDK."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)

    async def create(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return self._responses.pop(0)

    async def stream_events(self, **kwargs: Any):  # type: ignore[override]
        self.calls.append(kwargs)
        resp = self._responses.pop(0)
        for call in resp.tool_calls:
            payload = json.dumps(call.input)
            # Fragment like eager input streaming does.
            for i in range(0, len(payload), 7):
                yield StreamEvent("tool_input", text=payload[i : i + 7])
        if resp.text:
            yield StreamEvent("text", text=resp.text)
        yield StreamEvent("final", response=resp)


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **data: Any) -> None:
        self.events.append((name, data))


def _tool_response(name: str, inp: dict[str, Any], *, tool_id: str = "t1") -> LLMResponse:
    return LLMResponse(
        text="",
        input_tokens=20,
        output_tokens=5,
        stop_reason="tool_use",
        tool_calls=[ToolCall(id=tool_id, name=name, input=inp)],
        raw_content=[{"type": "tool_use", "id": tool_id, "name": name, "input": inp}],
    )


def _agent(
    responses: list[LLMResponse], *, protocol: str = "auto", provider: str = "anthropic"
) -> tuple[AutonomousAgent, StubClient]:
    cfg = LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        api_key="k",
        model="claude-sonnet-5-5",
        worker_model="claude-haiku-5-5",
        effort="high",
        worker_effort="low",
        decision_protocol=protocol,  # type: ignore[arg-type]
    )
    persona = AgentPersona(name="Ada", emoji="🔧", role="coder", skills=["python"], color="cyan")
    agent = AutonomousAgent(persona, llm_config=cfg)
    stub = StubClient(responses)
    agent._client = stub
    return agent, stub


@pytest.fixture
def fake_bus(monkeypatch: pytest.MonkeyPatch) -> FakeBus:
    bus = FakeBus()
    monkeypatch.setattr(base_mod, "bus", bus)
    return bus


@pytest.mark.asyncio
async def test_tool_call_becomes_decision_and_uses_worker_tier(fake_bus: FakeBus) -> None:
    agent, stub = _agent(
        [_tool_response("work_on_task", {"speech": "On it!", "target_task_id": "abc"})]
    )
    decision = await agent._decide("SITUATION")

    assert decision == {"action": "work_on_task", "speech": "On it!", "target_task_id": "abc"}
    call = stub.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["effort"] == "low"
    assert call["single_tool_call"] is True
    names = [t.name for t in call["tools"]]
    assert "work_on_task" in names and "idle" in names
    assert "exactly one tool" in call["system"]
    assert call["messages"][-1] == {"role": "user", "content": "SITUATION"}
    # Real usage counts are used when the provider reports them.
    assert agent.total_tokens == 25


@pytest.mark.asyncio
async def test_speech_streams_from_tool_input(fake_bus: FakeBus) -> None:
    agent, _ = _agent([_tool_response("idle", {"speech": "Taking a breather"})])
    await agent._decide("S")
    done = [d for n, d in fake_bus.events if n == "agent.speech_token" and d.get("done")]
    assert done and done[0]["text"] == "Taking a breather"


@pytest.mark.asyncio
async def test_history_is_append_only_with_tool_result(fake_bus: FakeBus) -> None:
    agent, stub = _agent(
        [
            _tool_response("work_on_task", {"speech": "go", "target_task_id": "a"}, tool_id="t1"),
            _tool_response("idle", {"speech": "ok"}, tool_id="t2"),
        ]
    )
    await agent._decide("ROUND 1")
    assert agent._pending_tool_use_id == "t1"
    assert len(agent._history) == 2

    agent._last_action_result = '{"action": "work_on_task", "task": "a"}'
    agent._last_action_error = False
    await agent._decide("ROUND 2")

    messages = stub.calls[1]["messages"]
    assert messages[0] == {"role": "user", "content": "ROUND 1"}
    assert messages[1]["role"] == "assistant"
    user2 = messages[2]
    assert user2["role"] == "user"
    assert user2["content"][0]["type"] == "tool_result"
    assert user2["content"][0]["tool_use_id"] == "t1"
    assert user2["content"][0]["is_error"] is False
    assert user2["content"][1] == {"type": "text", "text": "ROUND 2"}
    assert agent._pending_tool_use_id == "t2"
    assert len(agent._history) == 4


@pytest.mark.asyncio
async def test_operator_note_travels_as_system_message_once(fake_bus: FakeBus) -> None:
    agent, stub = _agent(
        [_tool_response("idle", {"speech": "a"}), _tool_response("idle", {"speech": "b"})]
    )
    agent.receive_operator_note("viewer alice: try sqlite")
    await agent._decide("S1")
    messages = stub.calls[0]["messages"]
    assert messages[-1]["role"] == "system"
    assert "try sqlite" in messages[-1]["content"]
    assert "advisory" in messages[-1]["content"]
    assert messages[-2]["role"] == "user"

    await agent._decide("S2")
    assert all(m["role"] != "system" for m in stub.calls[1]["messages"][-2:])


@pytest.mark.asyncio
async def test_refusal_returns_idle_and_drops_history(fake_bus: FakeBus) -> None:
    agent, _ = _agent(
        [
            LLMResponse(
                text="",
                input_tokens=1,
                output_tokens=0,
                stop_reason="refusal",
                refusal_category="cyber",
                raw_content=[],
            )
        ]
    )
    decision = await agent._decide("S")
    assert decision["action"] == "idle"
    assert decision["refused"] is True
    assert agent._history == []
    assert agent._pending_tool_use_id is None
    assert any(n == "agent.refusal" for n, _ in fake_bus.events)


@pytest.mark.asyncio
async def test_history_resets_at_round_cap(
    fake_bus: FakeBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base_mod.settings, "agent_history_rounds", 2)
    agent, _ = _agent(
        [_tool_response("idle", {"speech": str(i)}, tool_id=f"t{i}") for i in range(3)]
    )
    for i in range(3):
        agent._last_action_result = "ok"
        await agent._decide(f"R{i}")
    # Round 3 hit the cap (2 rounds × 2 msgs) and started a fresh trail.
    assert len(agent._history) == 2


@pytest.mark.asyncio
async def test_json_protocol_keeps_text_contract(fake_bus: FakeBus) -> None:
    agent, stub = _agent(
        [
            LLMResponse(
                text='{"action": "idle", "speech": "resting"}',
                input_tokens=0,
                output_tokens=0,
                raw_content=[{"type": "text", "text": "{...}"}],
            )
        ],
        protocol="json",
    )
    decision = await agent._decide("S")
    assert decision["action"] == "idle"
    assert decision["speech"] == "resting"
    assert stub.calls[0]["tools"] is None
    assert "Respond with JSON" in stub.calls[0]["system"]
    assert agent.total_tokens > 0  # estimate fallback when usage is absent


@pytest.mark.asyncio
async def test_vllm_defaults_to_json_protocol(fake_bus: FakeBus) -> None:
    agent, stub = _agent(
        [LLMResponse(text='{"action": "idle", "speech": "x"}', input_tokens=0, output_tokens=0)],
        provider="vllm",
    )
    await agent._decide("S")
    assert stub.calls[0]["tools"] is None


@pytest.mark.asyncio
async def test_invalid_tool_input_goes_through_parse_failure_path(fake_bus: FakeBus) -> None:
    agent, _ = _agent([_tool_response("review_work", {"speech": "x", "verdict": "MAYBE"})])
    decision = await agent._decide("S")
    # The parse-failure strategy returns a safe idle-style decision.
    assert decision.get("action") in ("idle", "work_on_task")


def test_director_uses_planning_tier_and_workers_the_cheap_tier() -> None:
    cfg = LLMConfig(
        provider="anthropic",
        api_key="k",
        model="claude-sonnet-5-5",
        worker_model="claude-haiku-5-5",
        effort="high",
        worker_effort="low",
    )
    director = DirectorAgent(llm_config=cfg)
    assert director._model == "claude-sonnet-5-5"
    assert director._effort == "high"
    worker = AutonomousAgent(
        AgentPersona(name="W", emoji="x", role="coder", skills=[], color="red"), llm_config=cfg
    )
    assert worker._model == "claude-haiku-5-5"
    assert worker._effort == "low"


def test_swarm_forwards_viewer_hint_to_workers_as_operator_notes() -> None:
    director = DirectorAgent()
    worker = AutonomousAgent(
        AgentPersona(name="W", emoji="x", role="coder", skills=[], color="red")
    )
    fake_swarm = SimpleNamespace(agents={"Director": director, "W": worker})
    assert AgentSwarm.add_viewer_hint(fake_swarm, "alice", "ship it") is True  # type: ignore[arg-type]
    assert worker._operator_notes == ["alice: ship it"]
    assert list(director._viewer_hints) == [("alice", "ship it")]
