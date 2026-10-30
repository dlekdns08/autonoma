"""Deterministic stand-in LLM for ``--dry-run`` (no network, no cost).

Plans two tasks, then each worker claims a task, writes one Python file,
and completes it — enough to exercise the whole pipeline and the report.
"""

from __future__ import annotations

import json
from typing import Any

from autonoma.llm import BaseLLMClient, LLMResponse, StreamEvent, ToolCall

_PLAN = {
    "analysis": "dry run",
    "agents_needed": [
        {"name": "Coder", "emoji": "🔧", "role": "coder", "skills": ["python"], "color": "cyan"}
    ],
    "tasks": [
        {
            "title": "Write main module",
            "description": "main.py",
            "priority": "high",
            "suggested_agent": "Coder",
            "depends_on_titles": [],
        },
        {
            "title": "Write tests",
            "description": "test_main.py",
            "priority": "medium",
            "suggested_agent": "Coder",
            "depends_on_titles": [],
        },
    ],
}


class StubLLM(BaseLLMClient):
    def __init__(self, name: str) -> None:
        self.name = name
        self.step = 0

    def _usage(self, text: str = "", **kw: Any) -> LLMResponse:
        return LLMResponse(
            text=text,
            input_tokens=1200,
            output_tokens=150,
            cache_read_input_tokens=800,
            stop_reason="end_turn",
            **kw,
        )

    async def create(self, **kwargs: Any) -> LLMResponse:
        tools = kwargs.get("tools") or []
        if not tools:
            # Director planning / synthesis
            return self._usage(json.dumps(_PLAN))
        names = [t.name for t in tools]
        if "wait" in names:  # Director intervention
            return self._call("wait", {"reason": "dry run"})
        return self._decide(kwargs)

    def _call(self, name: str, inp: dict[str, Any]) -> LLMResponse:
        return LLMResponse(
            text="",
            input_tokens=1200,
            output_tokens=150,
            cache_read_input_tokens=800,
            stop_reason="tool_use",
            tool_calls=[ToolCall(id=f"t{self.step}", name=name, input=inp)],
            raw_content=[{"type": "tool_use", "id": f"t{self.step}", "name": name, "input": inp}],
        )

    def _decide(self, kwargs: dict[str, Any]) -> LLMResponse:
        self.step += 1
        situation = json.dumps(kwargs.get("messages", []), ensure_ascii=False)
        import re

        ids = re.findall(r"\[([0-9a-f]{6,})\]", situation)
        task_id = ids[0] if ids else ""
        phase = self.step % 3
        if phase == 1:
            return self._call("work_on_task", {"speech": "on it", "target_task_id": task_id})
        if phase == 2:
            return self._call(
                "create_file",
                {
                    "speech": "writing",
                    "target_task_id": task_id,
                    "file_path": f"{self.name.lower()}_{self.step}.py",
                    "file_content": "def main() -> int:\n    return 0\n",
                    "file_description": "dry-run file",
                },
            )
        return self._call(
            "complete_task", {"speech": "done", "target_task_id": task_id, "task_output": "dry run"}
        )

    async def stream_events(self, **kwargs: Any):  # type: ignore[override]
        resp = await self.create(**kwargs)
        yield StreamEvent("final", response=resp)


def stub_factory(name: str) -> StubLLM:
    return StubLLM(name)
