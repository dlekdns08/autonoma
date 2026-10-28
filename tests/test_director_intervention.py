"""Director LLM interventions on stalls + advisor tool + deep-work extras."""

from __future__ import annotations

from typing import Any

import pytest

from autonoma.agents import director as director_mod
from autonoma.agents.director import DirectorAgent
from autonoma.llm import BaseLLMClient, LLMConfig, LLMResponse, ToolCall
from autonoma.models import AgentPersona, ProjectState, Task, TaskPriority, TaskStatus


class StubClient(BaseLLMClient):
    def __init__(self, calls: list[tuple[str, dict[str, Any]] | None]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._calls = list(calls)

    async def create(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        nxt = self._calls.pop(0)
        if nxt is None:
            return LLMResponse(text="hmm", input_tokens=1, output_tokens=1)
        name, inp = nxt
        return LLMResponse(
            text="",
            input_tokens=5,
            output_tokens=3,
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="c1", name=name, input=inp)],
        )


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event: str, **data: Any) -> None:
        self.events.append((event, data))


def _project() -> tuple[ProjectState, Task, Task]:
    t1 = Task(
        title="Design schema",
        description="d",
        priority=TaskPriority.HIGH,
        created_by="Director",
        assigned_to="Ada",
        status=TaskStatus.IN_PROGRESS,
    )
    t2 = Task(
        title="Write tests",
        description="t",
        priority=TaskPriority.MEDIUM,
        created_by="Director",
        assigned_to="Ada",
        status=TaskStatus.REVIEW,
    )
    project = ProjectState(
        name="demo",
        description="a demo",
        tasks=[t1, t2],
        agents=[
            AgentPersona(name="Ada", emoji="a", role="coder", skills=["python"], color="red"),
            AgentPersona(name="Bo", emoji="b", role="tester", skills=["pytest"], color="blue"),
        ],
        files=[],
    )
    return project, t1, t2


@pytest.fixture
def fake_bus(monkeypatch: pytest.MonkeyPatch) -> FakeBus:
    b = FakeBus()
    monkeypatch.setattr(director_mod, "bus", b)
    monkeypatch.setattr(director_mod.settings, "director_llm_interventions", True)
    monkeypatch.setattr(director_mod.settings, "director_intervention_cooldown_rounds", 0)
    return b


def _director(calls) -> tuple[DirectorAgent, StubClient]:
    d = DirectorAgent(
        llm_config=LLMConfig(provider="anthropic", api_key="k", model="claude-sonnet-5-5")
    )
    stub = StubClient(calls)
    d._client = stub
    d._round_number = 7
    return d, stub


@pytest.mark.asyncio
async def test_reassign_directive_moves_task_and_notifies(fake_bus: FakeBus) -> None:
    project, t1, _ = _project()
    d, stub = _director(
        [("reassign_task", {"task_id": t1.id, "agent": "Bo", "reason": "Ada is swamped"})]
    )
    d.add_viewer_hint("alice", "give it to Bo")

    directive = await d._consult_on_stall(project, available_agents=["Bo"])

    assert directive and directive["action"] == "reassign_task"
    assert t1.assigned_to == "Bo" and t1.status == TaskStatus.ASSIGNED
    assert project.messages[-1].recipient == "Bo"
    req = stub.requests[0]
    assert req["model"] == "claude-sonnet-5-5" and req["effort"] == "high"
    assert [t.name for t in req["tools"]] == [
        "nudge_agent",
        "reassign_task",
        "spawn_agent",
        "approve_review",
        "wait",
    ]
    assert (
        req["messages"][-1]["role"] == "system"
        and "give it to Bo" in req["messages"][-1]["content"]
    )
    assert not d._viewer_hints  # drained into the operator note
    assert (
        "director.intervention",
        {
            "agent": "Director",
            "directive": "reassign_task",
            "applied": True,
            "reason": "Ada is swamped",
            "target": "Bo",
        },
    ) in fake_bus.events
    assert d._last_intervention_round == 7


@pytest.mark.asyncio
async def test_nudge_and_approve_and_spawn(fake_bus: FakeBus) -> None:
    project, _, t2 = _project()
    d, _ = _director(
        [
            ("nudge_agent", {"agent": "Ada", "message": "ship the schema", "reason": "r"}),
            ("approve_review", {"task_id": t2.id, "reason": "nobody reviews"}),
            (
                "spawn_agent",
                {"name": "Cy", "role": "writer", "skills": ["docs"], "reason": "need docs"},
            ),
            ("nudge_agent", {"agent": "Ghost", "message": "x", "reason": "r"}),
        ]
    )
    assert (await d._consult_on_stall(project, available_agents=[]))["action"] == "nudge_agent"
    assert "[DIRECTOR] ship the schema" in project.messages[-1].content
    assert (await d._consult_on_stall(project, available_agents=[]))["action"] == "approve_review"
    assert t2.status == TaskStatus.DONE
    assert (await d._consult_on_stall(project, available_agents=[]))["action"] == "spawn_agent"
    spawn = [x for n, x in fake_bus.events if n == "agent.spawn_requested"]
    assert spawn and spawn[0]["name"] == "Cy" and spawn[0]["role"] == "writer"
    # Unknown agent → not applied, no exception.
    assert await d._consult_on_stall(project, available_agents=[]) is None


@pytest.mark.asyncio
async def test_no_tool_call_or_refusal_is_a_noop(fake_bus: FakeBus) -> None:
    project, _, _ = _project()
    d, _ = _director([None])
    assert await d._consult_on_stall(project, available_agents=[]) is None
    assert project.messages == []


def test_cooldown_and_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    d, _ = _director([])
    monkeypatch.setattr(director_mod.settings, "director_llm_interventions", True)
    monkeypatch.setattr(director_mod.settings, "director_intervention_cooldown_rounds", 3)
    d._last_intervention_round = 6
    assert d._intervention_allowed() is False  # 7 - 6 < 3
    d._last_intervention_round = 3
    assert d._intervention_allowed() is True
    monkeypatch.setattr(director_mod.settings, "director_llm_interventions", False)
    assert d._intervention_allowed() is False


@pytest.mark.asyncio
async def test_think_and_act_consults_on_stall(fake_bus: FakeBus) -> None:
    """A stalled round (nothing assignable, nothing in progress) triggers one directive."""
    project, t1, t2 = _project()
    t1.status = TaskStatus.BLOCKED
    t2.status = TaskStatus.BLOCKED
    d, stub = _director([("wait", {"reason": "blocked on deps"})])
    d._stall_counter = 1
    result = await d.think_and_act(project)
    assert result["action"] == "manage"
    assert len(stub.requests) == 1
    assert any(n == "director.intervention" for n, _ in fake_bus.events)


# ── advisor tool ──────────────────────────────────────────────────────


def test_anthropic_request_adds_advisor_tool_and_beta(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from autonoma import llm as llm_module

    monkeypatch.setattr(
        llm_module.settings if hasattr(llm_module, "settings") else director_mod.settings,
        "advisor_max_uses",
        3,
        raising=False,
    )
    client = llm_module.AnthropicLLMClient.__new__(llm_module.AnthropicLLMClient)
    client._client = MagicMock()
    ns, kw = client._build_request(
        model="claude-haiku-5-5",
        max_tokens=10,
        system="s",
        messages=[{"role": "user", "content": "x"}],
        cache_system_prompt=False,
        effort="low",
        tools=None,
        single_tool_call=True,
        streaming=True,
        advisor_model="claude-opus-5-5",
    )
    advisor = [t for t in kw["tools"] if t.get("type") == "advisor_20260301"]
    assert advisor and advisor[0]["model"] == "claude-opus-5-5" and advisor[0]["name"] == "advisor"
    assert advisor[0]["max_uses"] >= 1
    assert "advisor-tool-2026-03-01" in kw["betas"]
    assert ns is client._client.beta.messages
    # Same model as executor → no advisor
    _, kw = client._build_request(
        model="claude-opus-5-5",
        max_tokens=10,
        system="s",
        messages=[{"role": "user", "content": "x"}],
        cache_system_prompt=False,
        effort=None,
        tools=None,
        single_tool_call=True,
        streaming=False,
        advisor_model="claude-opus-5-5",
    )
    assert "tools" not in kw


def test_worker_advisor_comes_from_config() -> None:
    from autonoma.agents.base import AutonomousAgent

    cfg = LLMConfig(provider="anthropic", api_key="k", model="m", advisor_model="claude-opus-5-5")
    w = AutonomousAgent(
        AgentPersona(name="W", emoji="x", role="coder", skills=[], color="red"), llm_config=cfg
    )
    assert w._advisor_model == "claude-opus-5-5"
    cfg.advisor_model = ""
    assert w._advisor_model is None


# ── deep-work Agent SDK extras ────────────────────────────────────────


def test_deepwork_options_include_checkpointing_budget_and_tester(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from dataclasses import dataclass, field

    from autonoma.agents import deepwork

    @dataclass
    class AgentDefinition:
        description: str
        prompt: str
        tools: list[str] = field(default_factory=list)
        model: str = ""

    class Opts:
        def __init__(self, **kw: Any) -> None:
            self.kwargs = kw

    sdk = type("SDK", (), {"AgentDefinition": AgentDefinition, "ClaudeAgentOptions": Opts})
    monkeypatch.setattr(deepwork.settings, "deep_work_checkpointing", True)
    monkeypatch.setattr(deepwork.settings, "deep_work_task_budget_tokens", 5_000)
    monkeypatch.setattr(deepwork.settings, "deep_work_subagents", True)
    session = deepwork.DeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t",
        task_title="x",
        task_description="",
        project_description="",
        project_dir=tmp_path,
    )
    opts = session._options(sdk)
    assert opts.kwargs["enable_file_checkpointing"] is True
    assert opts.kwargs["task_budget"] == {"total": 20_000}  # raised to the API minimum
    assert "tester" in opts.kwargs["agents"]
    assert opts.kwargs["agents"]["tester"].model == "haiku"
    monkeypatch.setattr(deepwork.settings, "deep_work_task_budget_tokens", 0)
    monkeypatch.setattr(deepwork.settings, "deep_work_subagents", False)
    opts = session._options(sdk)
    assert "task_budget" not in opts.kwargs and "agents" not in opts.kwargs
