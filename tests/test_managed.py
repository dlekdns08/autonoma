"""Managed Agents integration with a fake client (no network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest

from autonoma.managed import deployments, dreams, memory, registry
from autonoma.managed.deepwork import ManagedDeepWorkSession, draft_rubric


class _Stream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e

        return gen()


class _Download:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def write_to_file(self, path: Path) -> None:
        Path(path).write_bytes(self.body)


class FakeClient:
    """Just enough of ``client.beta.*`` for the module under test."""

    def __init__(self, *, events: list[Any] | None = None, files: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._events = events or []
        self._files = files or []
        self._memories: dict[str, list[Any]] = {}
        self._dream_polls = 0
        b = self.beta = NS()
        b.environments = NS(create=self._rec("environments.create", NS(id="env_1")))
        b.agents = NS(create=self._rec("agents.create", NS(id="agent_1", version=2)))
        b.memory_stores = NS(
            create=self._rec("memory_stores.create", NS(id="memstore_1")),
            memories=NS(create=self._mem_create, list=self._mem_list, update=self._mem_update),
        )
        b.sessions = NS(
            create=self._rec("sessions.create", NS(id="sesn_1", status="running")),
            retrieve=self._rec("sessions.retrieve", NS(usage=NS(list_cost=NS(amount="37")))),
            events=NS(stream=self._stream, send=self._rec("events.send", None)),
        )
        b.files = NS(list=self._rec("files.list", NS(data=self._files)), download=self._download)
        b.dreams = NS(
            create=self._rec("dreams.create", NS(id="dream_1", status="running")),
            retrieve=self._dream_retrieve,
        )
        b.deployments = NS(
            create=self._rec(
                "deployments.create",
                NS(
                    id="depl_1",
                    status="active",
                    schedule=NS(upcoming_runs_at=["2026-11-01T00:00:00Z"]),
                ),
            )
        )

    def _rec(self, name: str, result: Any):
        async def fn(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, {"args": args, **kwargs}))
            return result

        return fn

    def _stream(self, *, session_id: str) -> _Stream:
        self.calls.append(("events.stream", {"session_id": session_id}))
        return _Stream(self._events)

    async def _download(self, file_id: str) -> _Download:
        self.calls.append(("files.download", {"id": file_id}))
        return _Download(b"print('hi')\n")

    async def _mem_create(self, store_id: str, *, path: str, content: str) -> Any:
        rows = self._memories.setdefault(store_id, [])
        if any(m.path == path for m in rows):
            raise RuntimeError("409 memory_path_conflict_error")
        m = NS(id=f"mem_{len(rows) + 1}", path=path, content=content)
        rows.append(m)
        return m

    async def _mem_list(self, store_id: str) -> Any:
        return NS(data=list(self._memories.get(store_id, [])))

    async def _mem_update(self, memory_id: str, *, memory_store_id: str, content: str) -> Any:
        for m in self._memories.get(memory_store_id, []):
            if m.id == memory_id:
                m.content = content
                return m
        raise RuntimeError("not found")

    async def _dream_retrieve(self, dream_id: str, *, betas: list[str]) -> Any:
        self._dream_polls += 1
        return NS(id=dream_id, status="completed" if self._dream_polls >= 2 else "running")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.settings, "data_dir", tmp_path)


@pytest.mark.asyncio
async def test_registry_creates_once_and_persists() -> None:
    c = FakeClient()
    assert await registry.ensure_environment(c) == "env_1"
    assert await registry.ensure_environment(c) == "env_1"
    assert await registry.ensure_agent(c) == ("agent_1", 2)
    assert await registry.ensure_memory_store(c, "uuid-a", "Ada") == "memstore_1"
    assert await registry.ensure_memory_store(c, "uuid-a", "Ada") == "memstore_1"
    names = [n for n, _ in c.calls]
    assert names.count("environments.create") == 1
    assert names.count("agents.create") == 1
    assert names.count("memory_stores.create") == 1
    saved = registry.load()
    assert saved["environment_id"] == "env_1" and saved["memory_stores"] == {"uuid-a": "memstore_1"}
    agent_call = [k for n, k in c.calls if n == "agents.create"][0]
    assert agent_call["tools"][0]["configs"][0] == {"name": "web_fetch", "enabled": False}
    assert agent_call["model"]["id"]


@pytest.mark.asyncio
async def test_managed_session_streams_outcome_and_downloads_files(tmp_path: Path) -> None:
    events = [
        NS(type="session.status_running"),
        NS(type="agent.message", content=[NS(type="text", text="Starting the build.")]),
        NS(
            type="agent.tool_use",
            id="ev1",
            name="bash",
            input={"command": "pytest"},
            evaluated_permission="allow",
        ),
        NS(
            type="agent.tool_use",
            id="ev2",
            name="bash",
            input={"command": "rm -rf /"},
            evaluated_permission="ask",
        ),
        NS(type="agent.message", content=[NS(type="text", text="Built src/app.py and ran tests.")]),
        NS(type="session.status_idle", stop_reason=NS(type="end_turn")),
    ]
    files = [
        NS(id="file_1", filename="src/app.py", size_bytes=12),
        NS(id="file_2", filename="../etc/passwd", size_bytes=1),
    ]
    c = FakeClient(events=events, files=files)
    spoken: list[str] = []
    tools: list[str] = []

    async def on_text(t: str) -> None:
        spoken.append(t)

    async def on_tool(n: str, i: dict[str, Any]) -> None:
        tools.append(n)

    project_dir = tmp_path / "proj"
    s = ManagedDeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t1",
        task_title="Build the app",
        task_description="hello world",
        project_description="demo",
        project_dir=project_dir,
        character_uuid="uuid-a",
        max_budget_usd=0.5,
        timeout_s=30,
        on_text=on_text,
        on_tool=on_tool,
        client=c,
    )
    result = await s.run()

    assert result.ok, result.error
    assert result.summary == "Built src/app.py and ran tests."
    assert result.files == ["src/app.py"]
    assert result.file_contents["src/app.py"] == "print('hi')\n"
    assert result.cost_usd == pytest.approx(0.37)
    assert s.session_id == "sesn_1"
    assert spoken == ["Starting the build.", "Built src/app.py and ran tests."]
    assert tools == ["bash", "bash"]
    # The paused ("ask") call was denied, never approved.
    denies = [k for n, k in c.calls if n == "events.send"]
    assert denies and denies[0]["events"][0]["result"] == "deny"
    create = [k for n, k in c.calls if n == "sessions.create"][0]
    assert create["environment_id"] == "env_1"
    assert create["agent"] == {"type": "agent", "id": "agent_1", "version": 2}
    assert create["initial_events"][1]["type"] == "user.define_outcome"
    assert "Build the app" in create["initial_events"][1]["rubric"]["content"]
    assert create["budget"] == {
        "type": "limit",
        "max_list_cost": {"amount": "50", "currency": "USD"},
    }
    assert create["resources"][0]["memory_store_id"] == "memstore_1"
    # Path traversal in output filenames is ignored.
    assert not (tmp_path / "etc").exists()


@pytest.mark.asyncio
async def test_managed_session_failure_is_reported(tmp_path: Path) -> None:
    c = FakeClient()

    async def boom(**kw: Any) -> Any:
        raise RuntimeError("quota exceeded")

    c.beta.sessions.create = boom
    s = ManagedDeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t",
        task_title="x",
        task_description="",
        project_description="",
        project_dir=tmp_path / "p",
        timeout_s=5,
        client=c,
    )
    result = await s.run()
    assert not result.ok and "quota exceeded" in result.error


def test_rubric_mentions_task_and_outputs_dir() -> None:
    r = draft_rubric("Write README", "document the CLI")
    assert "Write README" in r and "/mnt/session/outputs/" in r and "document the CLI" in r


@pytest.mark.asyncio
async def test_memoir_sync_creates_then_updates() -> None:
    c = FakeClient()
    assert await memory.sync_memoir(c, "memstore_1", "v1") == "mem_1"
    assert await memory.sync_memoir(c, "memstore_1", "v2") == "mem_1"
    rows = await memory.read_memories(c, "memstore_1")
    assert rows == [{"id": "mem_1", "path": "/memoir.md", "content": "v2"}]


@pytest.mark.asyncio
async def test_dream_polls_until_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    c = FakeClient()
    dream = await dreams.run_dream(
        c, memory_store_id="memstore_1", session_ids=["sesn_1", "sesn_1", "sesn_2"], poll_s=0.0
    )
    assert dream.status == "completed"
    create = [k for n, k in c.calls if n == "dreams.create"][0]
    assert create["inputs"][0] == {"type": "memory_store", "memory_store_id": "memstore_1"}
    assert create["inputs"][1] == {"type": "sessions", "session_ids": ["sesn_1", "sesn_2"]}
    assert create["output_behavior"] == {"type": "update_existing"}
    assert "dreaming-2026-04-21" in create["betas"]
    with pytest.raises(ValueError):
        await dreams.run_dream(c, memory_store_id="m", session_ids=[])


@pytest.mark.asyncio
async def test_scheduled_deployment_records_id() -> None:
    c = FakeClient()
    dep = await deployments.create_scheduled_run(
        c,
        name="Nightly build",
        goal="Build a todo app",
        cron="0 3 * * *",
        timezone="Asia/Seoul",
        budget_usd=2.0,
    )
    assert dep.id == "depl_1"
    create = [k for n, k in c.calls if n == "deployments.create"][0]
    assert create["schedule"] == {
        "type": "cron",
        "expression": "0 3 * * *",
        "timezone": "Asia/Seoul",
    }
    assert create["budget"]["max_list_cost"]["amount"] == "200"
    assert registry.load()["deployments"][0]["id"] == "depl_1"


@pytest.mark.asyncio
async def test_agent_uses_managed_backend_and_records_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autonoma.agents import base as base_mod
    from autonoma.agents.base import AutonomousAgent
    from autonoma.agents.deepwork import DeepWorkResult
    from autonoma.managed import deepwork as managed_deepwork
    from autonoma.models import AgentPersona, ProjectState, Task, TaskPriority, TaskStatus

    class FakeBus:
        async def emit(self, *a: Any, **k: Any) -> None:
            pass

    captured: dict[str, Any] = {}

    class FakeManaged:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)
            self.session_id = "sesn_42"

        async def run(self) -> DeepWorkResult:
            return DeepWorkResult(ok=True, summary="done", files=[], cost_usd=0.1)

    monkeypatch.setattr(base_mod, "bus", FakeBus())
    monkeypatch.setattr(base_mod.settings, "worker_backend", "managed_agents")
    monkeypatch.setattr(base_mod.settings, "output_dir", tmp_path / "out")
    from autonoma.workspace import manager as ws_mod

    monkeypatch.setattr(ws_mod.settings, "output_dir", tmp_path / "out")
    monkeypatch.setattr(managed_deepwork, "ManagedDeepWorkSession", FakeManaged)
    monkeypatch.setattr("autonoma.managed.client.settings.provider", "anthropic")
    monkeypatch.setattr("autonoma.managed.client.settings.anthropic_api_key", "sk-test")

    agent = AutonomousAgent(
        AgentPersona(name="Ada", emoji="x", role="coder", skills=[], color="red")
    )
    agent.character_uuid = "uuid-a"
    task = Task(
        title="T",
        description="d",
        priority=TaskPriority.HIGH,
        created_by="Director",
        assigned_to="Ada",
        status=TaskStatus.ASSIGNED,
    )
    project = ProjectState(name="p", description="d", tasks=[task], agents=[], files=[])
    res = await agent._action_work({"target_task_id": task.id}, project)
    assert res["deep_work"] == "started"
    assert agent._deep_work is not None
    await agent._deep_work
    assert captured["character_uuid"] == "uuid-a"
    assert agent.managed_session_ids == ["sesn_42"]
    assert task.status == TaskStatus.DONE
