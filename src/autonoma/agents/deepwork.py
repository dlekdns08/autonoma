"""Deep work — delegate a task's real building to the Claude Agent SDK.

The swarm's per-round loop is deliberately shallow: one action per
agent per round, a file at a time. That keeps the stage lively but the
output thin. With ``AUTONOMA_WORKER_BACKEND=agent_sdk`` a worker that
picks up a task hands the *building* to a Claude Code session running
in the project's workspace directory (``output/<project>/``): it reads
what's there, writes and edits files, runs commands, and returns a
summary. Autonoma stays the Director, the characters, and the stage;
the session's text and tool calls are streamed back as speech and
``agent.tool_use`` events so viewers see the work happen.

The SDK (``claude-agent-sdk`` extra) bundles the Claude Code binary and
reads ``ANTHROPIC_API_KEY`` from the environment. It is imported lazily
so the base install never needs it; ``is_available()`` gates the path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from autonoma.config import settings

logger = logging.getLogger(__name__)

TextCallback = Callable[[str], Awaitable[None]]
ToolCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

# Files the session may legitimately create that are noise for the stage.
_IGNORED_PARTS = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}
_MAX_CAPTURE_BYTES = 200_000


@dataclass
class DeepWorkResult:
    ok: bool
    summary: str
    files: list[str] = field(default_factory=list)
    file_contents: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0
    turns: int = 0
    duration_s: float = 0.0
    error: str = ""


# ── Containment ──────────────────────────────────────────────────────
#
# The Agent SDK merges the *whole* server environment into the CLI
# subprocess (``ClaudeAgentOptions.env`` can only override, never
# remove), and file tools pre-approved via ``allowed_tools`` accept any
# absolute path. Without the guards below a session could read the
# repo's ``.env``, the SQLite DB under ``data_dir``, or every API token
# in the environment.

# Credentials the Claude Code CLI itself needs to reach the API.
_ENV_KEEP = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
_SECRET_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "COOKIE",
    "SESSION",
    "DATABASE_URL",
    "DSN",
)
_SECRET_PREFIXES = (
    "AUTONOMA_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCP_",
    "GH_",
    "GITHUB_",
    "HF_",
    "OPENAI",
)
_FILE_TOOLS = "Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep"
_PATH_KEYS = ("file_path", "path", "notebook_path")


def scrubbed_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Overrides that blank every secret-looking inherited variable.

    Returned as ``ClaudeAgentOptions.env``; the SDK applies it on top of
    the inherited environment, so an empty string is how a variable is
    removed. The Anthropic credential the CLI runs on is kept.
    """
    env = os.environ if environ is None else environ
    out: dict[str, str] = {}
    for key in env:
        if key in _ENV_KEEP:
            continue
        upper = key.upper()
        if upper.startswith(_SECRET_PREFIXES) or any(m in upper for m in _SECRET_MARKERS):
            out[key] = ""
    return out


def _app_root() -> Path:
    # src/autonoma/agents/deepwork.py → repo root
    return Path(__file__).resolve().parents[3]


def sensitive_read_denies() -> list[str]:
    """Permission deny rules (``//`` = absolute path). Claude Code applies
    Read deny rules to its bash sandbox too, so ``cat ../../.env`` is
    blocked as well as the Read tool."""
    paths: set[str] = set()
    for root in {_app_root(), Path.cwd().resolve()}:
        paths.add(str(root / ".env"))
        paths.add(str(root / ".env.local"))
        paths.add(str(root / ".secrets") + "/**")
    paths.add(str(Path(settings.data_dir).resolve()) + "/**")
    home = Path.home().resolve()
    for d in (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".claude"):
        paths.add(str(home / d) + "/**")
    for f in (".netrc", ".npmrc", ".pypirc", ".zshrc", ".bashrc", ".zsh_history", ".bash_history"):
        paths.add(str(home / f))
    return sorted(f"Read(/{p})" for p in paths)


def make_path_guard(project_dir: Path) -> Any:
    """PreToolUse hook: file tools may only touch ``project_dir``."""
    root = project_dir.resolve()

    async def guard(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        tool_input = (input_data or {}).get("tool_input") or {}
        for key in _PATH_KEYS:
            raw = tool_input.get(key)
            if not raw:
                continue
            candidate = Path(str(raw)).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve()
            if resolved != root and not resolved.is_relative_to(root):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"{raw} is outside the project workspace; "
                            "only files under the current directory are available."
                        ),
                    }
                }
        return {}

    return guard


def _load_sdk() -> Any:
    """Import the Agent SDK on demand (tests monkeypatch this)."""
    import claude_agent_sdk  # noqa: PLC0415 — optional dependency

    return claude_agent_sdk


def is_available() -> bool:
    try:
        _load_sdk()
    except Exception:  # ImportError, or a broken optional install
        return False
    return True


def _snapshot(root: Path) -> dict[str, float]:
    """``relative path → mtime`` for every regular file under ``root``."""
    out: dict[str, float] = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _IGNORED_PARTS for part in rel.parts):
            continue
        try:
            out[rel.as_posix()] = p.stat().st_mtime
        except OSError:
            continue
    return out


def _changed_files(root: Path, before: dict[str, float]) -> tuple[list[str], dict[str, str]]:
    after = _snapshot(root)
    changed = sorted(p for p, m in after.items() if p not in before or m > before[p])
    contents: dict[str, str] = {}
    for rel in changed:
        path = root / rel
        try:
            if path.stat().st_size > _MAX_CAPTURE_BYTES:
                continue
            contents[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: listed, not captured
    return changed, contents


def build_prompt(
    *,
    agent_name: str,
    role: str,
    task_title: str,
    task_description: str,
    project_description: str,
    existing_files: list[str],
) -> str:
    files = "\n".join(f"  - {f}" for f in existing_files[:40]) or "  (empty)"
    return (
        f"You are {agent_name}, the team's {role}, working inside the project workspace.\n"
        f"Project goal: {project_description}\n\n"
        f"Your task: {task_title}\n{task_description}\n\n"
        f"Files already in the workspace:\n{files}\n\n"
        "Build it for real in this directory: create or edit the files the task "
        "needs, and when it is code, run it or its tests to check your work. "
        "Stay within the task; do not refactor unrelated files. Finish with a "
        "short summary (3-6 lines) of what you built and anything left open."
    )


class DeepWorkSession:
    """One Agent SDK run for one task, streaming progress through callbacks."""

    def __init__(
        self,
        *,
        agent_name: str,
        role: str,
        task_id: str,
        task_title: str,
        task_description: str,
        project_description: str,
        project_dir: Path,
        model: str | None = None,
        effort: str | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
        timeout_s: float | None = None,
        on_text: TextCallback | None = None,
        on_tool: ToolCallback | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.role = role
        self.task_id = task_id
        self.task_title = task_title
        self.task_description = task_description
        self.project_description = project_description
        self.project_dir = project_dir
        self.model = model or settings.deep_work_model or settings.model
        self.effort = effort or settings.deep_work_effort or None
        self.max_turns = max_turns or settings.deep_work_max_turns
        self.max_budget_usd = (
            settings.deep_work_max_budget_usd if max_budget_usd is None else max_budget_usd
        )
        self.timeout_s = timeout_s or settings.deep_work_timeout_s
        self.on_text = on_text
        self.on_tool = on_tool

    def _options(self, sdk: Any) -> Any:
        system_prompt = (
            f"You are {self.agent_name}, a {self.role} on an autonomous software team. "
            "You work only inside the current directory. Be concrete and ship working files."
        )
        kwargs: dict[str, Any] = dict(
            cwd=str(self.project_dir),
            allowed_tools=list(settings.deep_work_allowed_tools),
            disallowed_tools=[*settings.deep_work_disallowed_tools, *sensitive_read_denies()],
            # Without the OS sandbox the workspace directory is the only
            # boundary, so every edit/command is pre-approved; with it on
            # (below) edits are auto-accepted and bash is auto-approved
            # only when it actually runs sandboxed.
            permission_mode="acceptEdits" if settings.deep_work_sandbox else "bypassPermissions",
            # Don't pick up the operator's own ~/.claude or repo settings.
            setting_sources=[],
            max_turns=self.max_turns,
            model=self.model,
            system_prompt=system_prompt,
            env={**scrubbed_env(), "CLAUDE_AGENT_SDK_CLIENT_APP": "autonoma"},
        )
        if self.max_budget_usd:
            kwargs["max_budget_usd"] = self.max_budget_usd
        if self.effort:
            kwargs["effort"] = self.effort
        if settings.deep_work_sandbox:
            kwargs["sandbox"] = {
                "enabled": True,
                "autoAllowBashIfSandboxed": True,
                # A command that cannot be sandboxed is denied, never
                # escalated — this is an unattended run.
                "allowUnsandboxedCommands": False,
                "excludedCommands": [],
                "network": {
                    "allowedDomains": list(settings.deep_work_sandbox_allowed_domains),
                    "allowLocalBinding": True,
                },
            }
        if settings.deep_work_checkpointing:
            # Snapshots file edits so a failed session can be rewound.
            kwargs["enable_file_checkpointing"] = True
        if settings.deep_work_task_budget_tokens:
            # Advisory token budget the model paces itself against
            # (distinct from the hard dollar cap above). API minimum 20k.
            kwargs["task_budget"] = {
                "total": max(20_000, int(settings.deep_work_task_budget_tokens))
            }
        if settings.deep_work_subagents and hasattr(sdk, "AgentDefinition"):
            kwargs["agents"] = {
                "tester": sdk.AgentDefinition(
                    description=(
                        "Runs the project's tests or the program itself and reports "
                        "failures with reproduction steps. Delegate verification here."
                    ),
                    prompt=(
                        "You verify work inside the current directory: run tests or the "
                        "program, read failures, and report precisely what broke and why. "
                        "Do not edit source files."
                    ),
                    tools=["Read", "Bash", "Glob", "Grep"],
                    model="haiku",
                ),
            }
        if hasattr(sdk, "HookMatcher"):
            kwargs["hooks"] = {
                "PreToolUse": [
                    sdk.HookMatcher(matcher=_FILE_TOOLS, hooks=[make_path_guard(self.project_dir)])
                ]
            }
        return sdk.ClaudeAgentOptions(**kwargs)

    async def run(self) -> DeepWorkResult:
        started = time.perf_counter()
        try:
            sdk = _load_sdk()
        except Exception as exc:
            return DeepWorkResult(ok=False, summary="", error=f"agent sdk unavailable: {exc}")

        self.project_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot(self.project_dir)
        prompt = build_prompt(
            agent_name=self.agent_name,
            role=self.role,
            task_title=self.task_title,
            task_description=self.task_description,
            project_description=self.project_description,
            existing_files=sorted(before),
        )

        texts: list[str] = []
        result_msg: Any = None

        async def _consume() -> None:
            nonlocal result_msg
            async for message in sdk.query(prompt=prompt, options=self._options(sdk)):
                if isinstance(message, sdk.AssistantMessage):
                    for block in message.content:
                        if isinstance(block, sdk.TextBlock) and block.text.strip():
                            texts.append(block.text)
                            if self.on_text:
                                await self.on_text(block.text)
                        elif isinstance(block, sdk.ToolUseBlock):
                            if self.on_tool:
                                await self.on_tool(block.name, dict(block.input or {}))
                elif isinstance(message, sdk.ResultMessage):
                    result_msg = message

        error = ""
        try:
            await asyncio.wait_for(_consume(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            error = f"timed out after {self.timeout_s:.0f}s"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # CLI/SDK failures are reported, never raised
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("[deepwork:%s] session failed: %s", self.agent_name, error)

        files, contents = _changed_files(self.project_dir, before)
        duration = round(time.perf_counter() - started, 1)

        summary = ""
        cost = 0.0
        turns = 0
        ok = not error
        if result_msg is not None:
            summary = (getattr(result_msg, "result", None) or "").strip()
            cost = float(getattr(result_msg, "total_cost_usd", 0.0) or 0.0)
            turns = int(getattr(result_msg, "num_turns", 0) or 0)
            if getattr(result_msg, "is_error", False):
                ok = False
                error = error or f"session ended with {getattr(result_msg, 'subtype', 'error')}"
        if not summary:
            summary = (texts[-1].strip() if texts else "") or (error and f"Failed: {error}") or ""
        # A session that produced files but hit the turn/budget cap still
        # delivered work; let the caller decide, but flag it.
        return DeepWorkResult(
            ok=ok,
            summary=summary[:2000],
            files=files,
            file_contents=contents,
            cost_usd=cost,
            turns=turns,
            duration_s=duration,
            error=error,
        )
