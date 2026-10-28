"""Decision tools — the agent action protocol as native tool definitions.

Each action an agent may take (``work_on_task``, ``create_file``, …) is
exposed to the model as one strict tool. The model picks exactly one per
turn; the harness turns the tool call back into the ``decision`` dict the
``_action_*`` handlers in :mod:`autonoma.agents.base` already consume
(``{"action": <tool name>, **tool input}``).

Why tools instead of JSON-in-text:

- the schema is enforced by the provider (``strict: true``), so the
  three-stage JSON extraction fallback is only needed for providers
  without tool support (vLLM);
- capability gating becomes the tool list itself, not a string filter
  in the prompt;
- large inputs (file bodies, code) stream as they are generated.

The ``speech`` field is required on every tool so the stage can keep
rendering what the agent "says" while it acts.
"""

from __future__ import annotations

from typing import Any, Iterable

from autonoma.llm import ToolCall, ToolSpec

_SPEECH = {
    "type": "string",
    "description": "What you say out loud. One short, in-character sentence.",
}
_TASK_ID = {
    "type": "string",
    "description": "Task id from the situation report. Empty string if not applicable.",
}


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    """Strict object schema: every property required, no extras."""
    full = {"speech": _SPEECH, **props}
    return {
        "type": "object",
        "properties": full,
        "required": list(full.keys()),
        "additionalProperties": False,
    }


ACTION_TOOLS: dict[str, ToolSpec] = {
    "work_on_task": ToolSpec(
        name="work_on_task",
        description=(
            "Pick up an unassigned task that matches your skills, or keep working "
            "on the task assigned to you."
        ),
        input_schema=_schema({"target_task_id": _TASK_ID}),
    ),
    "create_file": ToolSpec(
        name="create_file",
        description=(
            "Create or overwrite a file in the shared workspace with its full contents. "
            "Use this whenever your task requires code or written content."
        ),
        input_schema=_schema(
            {
                "target_task_id": _TASK_ID,
                "file_path": {
                    "type": "string",
                    "description": "Workspace-relative path, e.g. src/app.py",
                },
                "file_content": {
                    "type": "string",
                    "description": "The complete file contents.",
                },
                "file_description": {
                    "type": "string",
                    "description": "One line describing what the file is for.",
                },
            }
        ),
    ),
    "send_message": ToolSpec(
        name="send_message",
        description="Send a message to one teammate by name.",
        input_schema=_schema(
            {
                "message_to": {"type": "string", "description": "Recipient agent name."},
                "message_content": {"type": "string"},
            }
        ),
    ),
    "request_help": ToolSpec(
        name="request_help",
        description="Ask the team for help when you are stuck. Say what you are stuck on.",
        input_schema=_schema({"message_content": {"type": "string"}}),
    ),
    "review_work": ToolSpec(
        name="review_work",
        description=(
            "Review a task that is waiting for review and give a verdict. "
            "Reviewers and testers must use this to approve or reject work."
        ),
        input_schema=_schema(
            {
                "target_task_id": _TASK_ID,
                "verdict": {"type": "string", "enum": ["PASS", "FAIL", "PARTIAL"]},
                "task_output": {
                    "type": "string",
                    "description": "Review notes: what passed, what must change.",
                },
            }
        ),
    ),
    "spawn_agent": ToolSpec(
        name="spawn_agent",
        description="Spawn a new specialised teammate when the workload demands it.",
        input_schema=_schema(
            {
                "spawn_name": {"type": "string"},
                "spawn_role": {
                    "type": "string",
                    "description": "e.g. coder, tester, reviewer, designer, writer",
                },
                "spawn_skills": {"type": "array", "items": {"type": "string"}},
            }
        ),
    ),
    "complete_task": ToolSpec(
        name="complete_task",
        description="Mark your current task as done and summarise the result.",
        input_schema=_schema(
            {
                "target_task_id": _TASK_ID,
                "task_output": {"type": "string", "description": "Result summary."},
            }
        ),
    ),
    "run_code": ToolSpec(
        name="run_code",
        description=(
            "Execute a short program in the sandbox and observe its output. "
            "Stdlib only, no network, a few seconds of CPU at most. "
            "Use print() to report results."
        ),
        input_schema=_schema(
            {
                "code_language": {"type": "string", "enum": ["python", "bash", "node"]},
                "code_body": {"type": "string", "description": "The program source."},
            }
        ),
    ),
    "open_pr": ToolSpec(
        name="open_pr",
        description="Commit, push, and open a GitHub pull request from a workspace git repo.",
        input_schema=_schema(
            {
                "repo_path": {
                    "type": "string",
                    "description": "Workspace-relative git repository path.",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to push, e.g. feature/fizzbuzz-ab12",
                },
                "pr_title": {"type": "string", "description": "Short single-line title."},
                "pr_body": {
                    "type": "string",
                    "description": "PR body with What / Why / Test plan sections.",
                },
                "base": {
                    "type": "string",
                    "description": "Base branch. Use 'main' unless told otherwise.",
                },
            }
        ),
    ),
    "idle": ToolSpec(
        name="idle",
        description="Do nothing this turn (waiting on others, or nothing fits your skills).",
        input_schema=_schema({}),
    ),
    "celebrate": ToolSpec(
        name="celebrate",
        description="Celebrate — only when every task is done.",
        input_schema=_schema({}),
    ),
}

# Meta-actions every agent may always take, regardless of harness.
ALWAYS_ALLOWED: tuple[str, ...] = ("idle", "celebrate")


def build_decision_tools(permitted: Iterable[str]) -> list[ToolSpec]:
    """Return the tool list for an agent whose capabilities are ``permitted``.

    Order is deterministic (schema order) so the ``tools`` prefix stays
    byte-identical across rounds — a requirement for prompt caching and
    for preserved-thinking replay.
    """
    allowed = set(permitted) | set(ALWAYS_ALLOWED)
    return [spec for name, spec in ACTION_TOOLS.items() if name in allowed]


def validate_tool_input(name: str, raw: Any) -> dict[str, Any]:
    """Coerce a tool input into the shape the handlers expect.

    Providers with ``strict`` tool use already guarantee the shape; this
    is the client-side check the eager-streaming contract requires (the
    SDK's tolerant parser can return a truncated object instead of
    raising). Missing scalars are filled with ``""``/``[]`` so a slightly
    short payload still dispatches; a wrong type or bad enum raises
    ``ValueError`` and is handled like any other parse failure.
    """
    spec = ACTION_TOOLS.get(name)
    if spec is None:
        raise ValueError(f"unknown action tool: {name!r}")
    if not isinstance(raw, dict):
        raise ValueError(f"tool input for {name!r} is not an object")
    props: dict[str, Any] = spec.input_schema["properties"]
    out: dict[str, Any] = {}
    for key, schema in props.items():
        value = raw.get(key)
        expected = schema.get("type")
        if value is None:
            value = [] if expected == "array" else ""
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"{name}.{key} must be a string")
            enum = schema.get("enum")
            if enum and value and value not in enum:
                raise ValueError(f"{name}.{key} must be one of {enum}")
        elif expected == "array":
            if not isinstance(value, list):
                raise ValueError(f"{name}.{key} must be an array")
            value = [str(v) for v in value]
        out[key] = value
    return out


def decision_from_tool_call(call: ToolCall) -> dict[str, Any]:
    """Turn a tool call into the legacy ``decision`` dict."""
    payload = validate_tool_input(call.name, call.input)
    return {"action": call.name, **payload}


def decision_tools_prompt(tool_names: Iterable[str]) -> str:
    """System-prompt addendum for the tool-use protocol."""
    names = ", ".join(tool_names)
    return f"""
Decide your SINGLE next action by calling exactly one tool: {names}.
Always call a tool — never answer in plain text. Fill in every field; use an
empty string for fields that do not apply.

Rules:
- Pick up unassigned tasks that match your skills
- If you have an assigned task, work on it
- Create files when your task requires code/content
- Ask for help if stuck
- Be proactive and creative
- Keep speech SHORT and in-character (1 sentence max)"""
