"""Decision tools: capability gating, strict schemas, input validation."""

from __future__ import annotations

import pytest

from autonoma.agents.actions import (
    ACTION_TOOLS,
    build_decision_tools,
    decision_from_tool_call,
    decision_tools_prompt,
    validate_tool_input,
)
from autonoma.llm import ToolCall


def test_every_tool_is_strict_and_fully_required() -> None:
    for name, spec in ACTION_TOOLS.items():
        schema = spec.input_schema
        assert spec.strict, name
        assert schema["additionalProperties"] is False, name
        assert set(schema["required"]) == set(schema["properties"]), name
        assert "speech" in schema["properties"], name


def test_build_decision_tools_filters_by_capability_and_keeps_meta_actions() -> None:
    tools = build_decision_tools(["create_file", "send_message"])
    names = [t.name for t in tools]
    assert names == ["create_file", "send_message", "idle", "celebrate"]


def test_build_decision_tools_is_deterministic() -> None:
    a = [t.name for t in build_decision_tools(["run_code", "work_on_task", "open_pr"])]
    b = [t.name for t in build_decision_tools(["open_pr", "run_code", "work_on_task"])]
    assert a == b


def test_decision_from_tool_call_maps_to_legacy_dict() -> None:
    call = ToolCall(
        id="t1",
        name="create_file",
        input={
            "speech": "Writing it now",
            "target_task_id": "abc",
            "file_path": "src/app.py",
            "file_content": "print(1)",
            "file_description": "entrypoint",
        },
    )
    decision = decision_from_tool_call(call)
    assert decision["action"] == "create_file"
    assert decision["file_path"] == "src/app.py"
    assert decision["speech"] == "Writing it now"


def test_validate_fills_missing_scalars_with_defaults() -> None:
    out = validate_tool_input("spawn_agent", {"speech": "hi"})
    assert out["spawn_name"] == ""
    assert out["spawn_skills"] == []


def test_validate_rejects_bad_enum_and_types() -> None:
    with pytest.raises(ValueError):
        validate_tool_input("review_work", {"speech": "x", "verdict": "MAYBE"})
    with pytest.raises(ValueError):
        validate_tool_input("run_code", {"speech": "x", "code_body": 42})
    with pytest.raises(ValueError):
        validate_tool_input("not_a_tool", {})
    with pytest.raises(ValueError):
        validate_tool_input("idle", "not an object")


def test_prompt_lists_tool_names() -> None:
    prompt = decision_tools_prompt(["work_on_task", "idle"])
    assert "work_on_task, idle" in prompt
    assert "exactly one tool" in prompt
