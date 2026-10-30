"""Deep work: Agent SDK delegation with a fake SDK (no network, no CLI)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autonoma.agents import base as base_mod
from autonoma.agents import deepwork
from autonoma.agents.base import AutonomousAgent
from autonoma.llm import BaseLLMClient, LLMResponse, ToolSpec
from autonoma.models import AgentPersona, ProjectState, Task, TaskPriority, TaskStatus


class _UnusableLLMClient(BaseLLMClient):
    """LLM client that fails loudly if anything tries to call it."""

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        cache_system_prompt: bool = False,
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
        advisor_model: str | None = None,
    ) -> LLMResponse:
        raise AssertionError("LLM must not be called in this round")


# ── a stand-in for claude_agent_sdk ──────────────────────────────────


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AssistantMessage:
    content: list[Any]


@dataclass
class ResultMessage:
    result: str = ""
    total_cost_usd: float = 0.0
    num_turns: int = 0
    is_error: bool = False
    subtype: str = "success"


@dataclass
class ClaudeAgentOptions:
    kwargs: dict[str, Any] = field(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeSDK:
    """Replays a scripted session and writes files into ``cwd`` on the way."""

    TextBlock = TextBlock
    ToolUseBlock = ToolUseBlock
    AssistantMessage = AssistantMessage
    ResultMessage = ResultMessage
    ClaudeAgentOptions = ClaudeAgentOptions

    def __init__(self, *, fail: bool = False, hang: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.hang = hang

    async def query(self, *, prompt: str, options: ClaudeAgentOptions):
        self.calls.append({"prompt": prompt, "options": options.kwargs})
        if self.hang:
            await asyncio.sleep(3600)
        cwd = Path(options.kwargs["cwd"])
        yield AssistantMessage([TextBlock("Let me look around first.")])
        yield AssistantMessage([ToolUseBlock("u1", "Read", {"file_path": "README.md"})])
        (cwd / "src").mkdir(parents=True, exist_ok=True)
        (cwd / "src" / "app.py").write_text("print('hello')\n")
        yield AssistantMessage([ToolUseBlock("u2", "Write", {"file_path": "src/app.py"})])
        if self.fail:
            raise RuntimeError("CLI exploded")
        yield ResultMessage(result="Built src/app.py", total_cost_usd=0.03, num_turns=4)


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> FakeSDK:
    sdk = FakeSDK()
    monkeypatch.setattr(deepwork, "_load_sdk", lambda: sdk)
    return sdk


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **data: Any) -> None:
        self.events.append((name, data))

    def names(self) -> list[str]:
        return [n for n, _ in self.events]


# ── DeepWorkSession ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_streams_and_collects_changed_files(
    fake_sdk: FakeSDK, tmp_path: Path
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("old\n")
    spoken: list[str] = []
    tools: list[tuple[str, dict[str, Any]]] = []

    async def on_text(t: str) -> None:
        spoken.append(t)

    async def on_tool(name: str, inp: dict[str, Any]) -> None:
        tools.append((name, inp))

    session = deepwork.DeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t1",
        task_title="Write the app",
        task_description="A hello world.",
        project_description="demo",
        project_dir=project_dir,
        model="claude-sonnet-5-5",
        effort="medium",
        max_turns=10,
        max_budget_usd=0.5,
        timeout_s=30,
        on_text=on_text,
        on_tool=on_tool,
    )
    result = await session.run()

    assert result.ok
    assert result.summary == "Built src/app.py"
    assert result.files == ["src/app.py"]  # README untouched → not listed
    assert result.file_contents["src/app.py"] == "print('hello')\n"
    assert result.cost_usd == 0.03 and result.turns == 4
    assert spoken == ["Let me look around first."]
    assert [n for n, _ in tools] == ["Read", "Write"]

    opts = fake_sdk.calls[0]["options"]
    assert opts["cwd"] == str(project_dir)
    # OS sandbox on by default → edits auto-accepted, bash only when sandboxed.
    assert opts["permission_mode"] == "acceptEdits"
    assert opts["sandbox"]["enabled"] is True
    assert opts["setting_sources"] == []
    assert opts["model"] == "claude-sonnet-5-5"
    assert opts["effort"] == "medium"
    assert opts["max_budget_usd"] == 0.5
    assert "Bash" in opts["allowed_tools"]
    assert "WebFetch" in opts["disallowed_tools"]
    assert "Write the app" in fake_sdk.calls[0]["prompt"]
    assert "README.md" in fake_sdk.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_session_failure_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sdk = FakeSDK(fail=True)
    monkeypatch.setattr(deepwork, "_load_sdk", lambda: sdk)
    session = deepwork.DeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t",
        task_title="x",
        task_description="",
        project_description="",
        project_dir=tmp_path / "p",
        timeout_s=30,
    )
    result = await session.run()
    assert not result.ok
    assert "CLI exploded" in result.error
    # Files written before the crash are still surfaced.
    assert result.files == ["src/app.py"]


@pytest.mark.asyncio
async def test_session_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sdk = FakeSDK(hang=True)
    monkeypatch.setattr(deepwork, "_load_sdk", lambda: sdk)
    session = deepwork.DeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t",
        task_title="x",
        task_description="",
        project_description="",
        project_dir=tmp_path / "p",
        timeout_s=0.05,
    )
    result = await session.run()
    assert not result.ok and "timed out" in result.error


def test_is_available_false_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise ImportError("no sdk")

    monkeypatch.setattr(deepwork, "_load_sdk", boom)
    assert deepwork.is_available() is False


# ── Agent integration ─────────────────────────────────────────────────


def _project_with_task(agent_name: str) -> tuple[ProjectState, Task]:
    task = Task(
        title="Build the CLI",
        description="argparse entrypoint",
        priority=TaskPriority.HIGH,
        created_by="Director",
        assigned_to=agent_name,
        status=TaskStatus.ASSIGNED,
    )
    project = ProjectState(
        name="demo-proj", description="a demo", tasks=[task], agents=[], files=[]
    )
    return project, task


@pytest.fixture
def agent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_sdk: FakeSDK) -> FakeBus:
    bus = FakeBus()
    monkeypatch.setattr(base_mod, "bus", bus)
    monkeypatch.setattr(base_mod.settings, "worker_backend", "agent_sdk")
    monkeypatch.setattr(base_mod.settings, "output_dir", tmp_path / "output")
    monkeypatch.setattr(base_mod.settings, "deep_work_timeout_s", 30.0)
    # workspace manager reads the same settings object
    from autonoma.workspace import manager as ws_mod

    monkeypatch.setattr(ws_mod.settings, "output_dir", tmp_path / "output")
    return bus


@pytest.mark.asyncio
async def test_work_on_task_delegates_and_completes_task(agent_env: FakeBus) -> None:
    agent = AutonomousAgent(
        AgentPersona(name="Ada", emoji="x", role="coder", skills=["python"], color="red")
    )
    project, task = _project_with_task("Ada")

    result = await agent._action_work({"target_task_id": task.id}, project)
    assert result["deep_work"] == "started"
    assert task.status == TaskStatus.IN_PROGRESS

    # While the session runs, rounds don't hit the LLM.
    busy = await agent.think_and_act(project)
    assert busy["action"] == "deep_work" and busy["in_progress"] is True

    assert agent._deep_work is not None
    await agent._deep_work

    assert task.status == TaskStatus.DONE
    assert task.output == "Built src/app.py"
    assert [f.path for f in project.files] == ["src/app.py"]
    assert project.files[0].created_by == "Ada"
    assert "src/app.py" in task.artifacts
    assert agent.total_cost_usd == pytest.approx(0.03)
    assert agent.stats.tasks_completed == 1

    names = agent_env.names()
    assert "deepwork.started" in names
    assert "agent.tool_use" in names
    assert "file.created" in names
    assert "deepwork.finished" in names
    assert "task.completed" in names
    tool_events = [d for n, d in agent_env.events if n == "agent.tool_use"]
    assert tool_events[0]["tool"] == "Read" and tool_events[0]["detail"] == "README.md"

    # Next round is a normal one again (slot cleared).
    agent._client = _UnusableLLMClient()  # would blow up if the LLM were called
    agent._deep_work_enabled = lambda: False  # type: ignore[method-assign]
    assert agent._deep_work is not None
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_native_backend_does_not_delegate(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: FakeSDK
) -> None:
    monkeypatch.setattr(base_mod, "bus", FakeBus())
    monkeypatch.setattr(base_mod.settings, "worker_backend", "native")
    agent = AutonomousAgent(
        AgentPersona(name="Ada", emoji="x", role="coder", skills=[], color="red")
    )
    project, task = _project_with_task("Ada")
    result = await agent._action_work({"target_task_id": task.id}, project)
    assert "deep_work" not in result
    assert agent._deep_work is None
    assert fake_sdk.calls == []


@pytest.mark.asyncio
async def test_cancel_stops_deep_work(agent_env: FakeBus, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = FakeSDK(hang=True)
    monkeypatch.setattr(deepwork, "_load_sdk", lambda: sdk)
    agent = AutonomousAgent(
        AgentPersona(name="Ada", emoji="x", role="coder", skills=[], color="red")
    )
    project, task = _project_with_task("Ada")
    await agent._action_work({"target_task_id": task.id}, project)
    assert agent._deep_work is not None
    await asyncio.sleep(0.01)
    agent.cancel("shutdown")
    with pytest.raises(asyncio.CancelledError):
        await agent._deep_work
    assert "deepwork.cancelled" in agent_env.names()
