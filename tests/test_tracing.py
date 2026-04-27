"""Tests for the run-tracing recorder.

Covers the file-on-disk side effects:

  * ``RunRecorder`` writes meta.json on construction
  * ``checkpoint`` writes round-NNNN.json with the project payload
  * ``log_llm_call`` appends one JSON line per call to llm-calls.jsonl
  * ``log_event`` filters skip-listed events but persists the rest
  * ``finalize`` updates meta.json with ``finished_at``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonoma.models import ProjectState, Task
from autonoma.tracing import RunRecorder


@pytest.fixture
def recorder(tmp_path: Path) -> RunRecorder:
    return RunRecorder(run_dir=tmp_path / "run", goal="hello world", model="claude-test")


def test_construction_creates_dir_and_meta(recorder: RunRecorder) -> None:
    assert recorder.run_dir.exists()
    meta_path = recorder.run_dir / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["goal"] == "hello world"
    assert meta["model"] == "claude-test"
    assert meta["finished_at"] is None
    assert meta["llm_calls"] == 0


def test_checkpoint_writes_round_file(recorder: RunRecorder) -> None:
    project = ProjectState(name="proj", description="d")
    project.tasks = [Task(title="t1", description="do it")]

    recorder.checkpoint(round_num=1, project=project)

    out = recorder.run_dir / "round-0001.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["name"] == "proj"
    assert any(t["title"] == "t1" for t in payload["tasks"])

    # Meta should now list the checkpoint.
    meta = json.loads((recorder.run_dir / "meta.json").read_text())
    assert 1 in meta["checkpoints"]


async def test_log_llm_call_appends_jsonl(recorder: RunRecorder) -> None:
    await recorder.log_llm_call({
        "ts": "2026-04-27T12:00:00",
        "agent": "Director",
        "phase": "decide",
        "duration_sec": 0.42,
        "request": {"model": "x", "messages": []},
        "response": {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 2}},
    })
    await recorder.log_llm_call({
        "ts": "2026-04-27T12:00:01",
        "agent": "Coder",
        "phase": "code",
        "duration_sec": 1.5,
        "request": {"model": "x", "messages": []},
        "response": {"text": "done", "usage": {"input_tokens": 5, "output_tokens": 10}},
    })

    path = recorder.run_dir / "llm-calls.jsonl"
    assert path.exists()
    lines = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    # ``seq`` is auto-assigned 1, 2 in order.
    assert [r["seq"] for r in lines] == [1, 2]
    assert lines[0]["agent"] == "Director"
    assert lines[1]["agent"] == "Coder"


async def test_log_event_writes_and_filters(recorder: RunRecorder) -> None:
    # ``agent.state`` is in the SKIP_EVENTS frozenset → not persisted.
    await recorder.log_event("agent.state", {"x": 1})
    await recorder.log_event("agent.speech", {"text": "hi"})
    await recorder.log_event("world.clock", {"t": 0})

    path = recorder.run_dir / "events.jsonl"
    assert path.exists()
    lines = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    # Only the speech event should have made it through.
    assert len(lines) == 1
    assert lines[0]["event"] == "agent.speech"
    assert lines[0]["data"] == {"text": "hi"}


def test_finalize_records_finished_at(recorder: RunRecorder) -> None:
    recorder.finalize()
    meta = json.loads((recorder.run_dir / "meta.json").read_text())
    assert meta["finished_at"] is not None


async def test_traced_messages_create_records_call(
    recorder: RunRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``traced_messages_create`` should append a record when a recorder is active."""
    from autonoma import tracing as tracing_mod
    from autonoma.llm import LLMResponse

    class StubClient:
        async def create(self, **kwargs):
            return LLMResponse(text="hello", input_tokens=3, output_tokens=4, stop_reason="end_turn")

    tracing_mod.set_active_recorder(recorder)
    try:
        response = await tracing_mod.traced_messages_create(
            StubClient(),  # type: ignore[arg-type]
            agent="Director",
            phase="decide",
            model="claude-test",
            max_tokens=100,
            temperature=0.1,
            system="be helpful",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        tracing_mod.set_active_recorder(None)

    assert response.text == "hello"

    path = recorder.run_dir / "llm-calls.jsonl"
    record = json.loads(path.read_text().splitlines()[0])
    assert record["agent"] == "Director"
    assert record["response"]["text"] == "hello"
    assert record["response"]["usage"] == {"input_tokens": 3, "output_tokens": 4}
