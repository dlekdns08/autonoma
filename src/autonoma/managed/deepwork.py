"""Outcome-driven deep work on Managed Agents.

Same contract as :class:`autonoma.agents.deepwork.DeepWorkSession` so the
agent loop can swap backends: streams text and tool calls through the
callbacks, returns a :class:`DeepWorkResult` with the files written to
``/mnt/session/outputs/`` copied into the project workspace.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from autonoma.agents.deepwork import (
    DeepWorkResult,
    TextCallback,
    ToolCallback,
    _changed_files,
    _snapshot,
)
from autonoma.config import settings
from autonoma.managed import registry
from autonoma.managed.client import MANAGED_BETA, get_client

logger = logging.getLogger(__name__)


def draft_rubric(task_title: str, task_description: str) -> str:
    """A starter rubric for the outcome grader, drafted from the task."""
    return (
        f"# Rubric — {task_title}\n"
        f"- The deliverable does what the task asks: {task_description[:400]}\n"
        "- Every deliverable file is written under /mnt/session/outputs/ with its "
        "project-relative path\n"
        "- Code runs (or tests pass) — the session shows the command and its output\n"
        "- No placeholder text, TODOs, or empty sections remain\n"
        "- A 3-6 line summary of what was built and what is left open ends the work\n"
    )


class ManagedDeepWorkSession:
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
        character_uuid: str = "",
        max_budget_usd: float | None = None,
        timeout_s: float | None = None,
        on_text: TextCallback | None = None,
        on_tool: ToolCallback | None = None,
        on_subagent: Any = None,
        client: Any = None,
    ) -> None:
        self.agent_name = agent_name
        self.role = role
        self.task_id = task_id
        self.task_title = task_title
        self.task_description = task_description
        self.project_description = project_description
        self.project_dir = project_dir
        self.character_uuid = character_uuid
        self.max_budget_usd = (
            settings.deep_work_max_budget_usd if max_budget_usd is None else max_budget_usd
        )
        self.timeout_s = timeout_s or settings.deep_work_timeout_s
        self.on_text = on_text
        self.on_tool = on_tool
        # ``await on_subagent(phase, thread_id, agent_name)`` with phase
        # "started" | "finished" for multiagent sub-threads.
        self.on_subagent = on_subagent
        self._client = client
        self.subagents: dict[str, str] = {}
        self.session_id: str = ""

    def _prompt(self, existing: list[str]) -> str:
        files = "\n".join(f"  - {f}" for f in existing[:40]) or "  (empty)"
        return (
            f"You are {self.agent_name}, the team's {self.role}.\n"
            f"Project goal: {self.project_description}\n\n"
            f"Task: {self.task_title}\n{self.task_description}\n\n"
            f"Files the team already produced (for context):\n{files}\n"
        )

    async def run(self) -> DeepWorkResult:
        started = time.perf_counter()
        client = self._client or get_client()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot(self.project_dir)

        texts: list[str] = []
        error = ""
        try:
            env_id = await registry.ensure_environment(client)
            agent_id, version = await registry.ensure_agent(client)
            resources: list[dict[str, Any]] = []
            if self.character_uuid and settings.managed_memory_enabled:
                store_id = await registry.ensure_memory_store(
                    client, self.character_uuid, self.agent_name
                )
                resources.append(
                    {
                        "type": "memory_store",
                        "memory_store_id": store_id,
                        "access": "read_write",
                        "instructions": (
                            f"{self.agent_name}'s long-term memory. Read it before starting; "
                            "append what you learned when done."
                        ),
                    }
                )
            create_kwargs: dict[str, Any] = dict(
                agent=(
                    {"type": "agent", "id": agent_id, "version": version} if version else agent_id
                ),
                environment_id=env_id,
                title=f"{self.agent_name}: {self.task_title}"[:120],
                initial_events=[
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": self._prompt(sorted(before))}],
                    },
                    {
                        "type": "user.define_outcome",
                        "description": f"Deliver: {self.task_title}",
                        "rubric": {
                            "type": "text",
                            "content": draft_rubric(self.task_title, self.task_description),
                        },
                        "max_iterations": settings.managed_outcome_max_iterations,
                    },
                ],
                metadata={"autonoma_agent": self.agent_name, "task_id": self.task_id},
            )
            if resources:
                create_kwargs["resources"] = resources
            if self.max_budget_usd:
                cents = max(1, int(round(self.max_budget_usd * 100)))
                create_kwargs["budget"] = {
                    "type": "limit",
                    "max_list_cost": {"amount": str(cents), "currency": "USD"},
                }
            session = await client.beta.sessions.create(**create_kwargs)
            self.session_id = session.id

            async def _consume() -> None:
                async with client.beta.sessions.events.stream(session_id=session.id) as stream:
                    async for event in stream:
                        etype = getattr(event, "type", "")
                        if etype == "agent.message":
                            for block in getattr(event, "content", []) or []:
                                if getattr(block, "type", "") == "text" and block.text.strip():
                                    texts.append(block.text)
                                    if self.on_text:
                                        await self.on_text(block.text)
                        elif etype in ("agent.tool_use", "agent.mcp_tool_use"):
                            if self.on_tool:
                                await self.on_tool(
                                    str(getattr(event, "name", "tool")),
                                    dict(getattr(event, "input", {}) or {}),
                                )
                            if getattr(event, "evaluated_permission", "") == "ask":
                                # Unattended run: never approve a paused call.
                                await client.beta.sessions.events.send(
                                    session_id=session.id,
                                    events=[
                                        {
                                            "type": "user.tool_confirmation",
                                            "tool_use_id": event.id,
                                            "result": "deny",
                                        }
                                    ],
                                )
                        elif etype == "session.thread_created":
                            tid = str(getattr(event, "session_thread_id", ""))
                            name = str(getattr(event, "agent_name", "") or "helper")
                            self.subagents[tid] = name
                            if self.on_subagent:
                                await self.on_subagent("started", tid, name)
                        elif etype in (
                            "session.thread_status_terminated",
                            "session.thread_status_idle",
                        ):
                            tid = str(getattr(event, "session_thread_id", ""))
                            if tid in self.subagents and self.on_subagent:
                                await self.on_subagent("finished", tid, self.subagents.pop(tid))
                        elif etype == "session.status_idle":
                            stop = getattr(getattr(event, "stop_reason", None), "type", "")
                            if stop != "requires_action":
                                return
                        elif etype == "session.status_terminated":
                            return

            await asyncio.wait_for(_consume(), timeout=self.timeout_s)
            await self._download_outputs(client, session.id)
        except asyncio.TimeoutError:
            error = f"timed out after {self.timeout_s:.0f}s"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("[managed:%s] session failed: %s", self.agent_name, error)

        files, contents = _changed_files(self.project_dir, before)
        cost = 0.0
        if self.session_id:
            try:
                s = await client.beta.sessions.retrieve(session_id=self.session_id)
                usage = getattr(s, "usage", None)
                lc = getattr(usage, "list_cost", None)
                amount = getattr(lc, "amount", None) if lc is not None else None
                if amount is not None:
                    cost = float(amount) / 100.0
            except Exception:
                pass
        summary = (texts[-1].strip() if texts else "") or (error and f"Failed: {error}") or ""
        return DeepWorkResult(
            ok=not error,
            summary=summary[:2000],
            files=files,
            file_contents=contents,
            cost_usd=cost,
            turns=0,
            duration_s=round(time.perf_counter() - started, 1),
            error=error,
        )

    async def _download_outputs(self, client: Any, session_id: str) -> None:
        """Copy ``/mnt/session/outputs/*`` into the project workspace."""
        for attempt in range(3):
            page = await client.beta.files.list(scope_id=session_id, betas=[MANAGED_BETA])
            items = list(getattr(page, "data", []) or [])
            if items or attempt == 2:
                break
            await asyncio.sleep(1.5)  # indexing lag after status_idle
        for f in items:
            rel = str(getattr(f, "filename", "") or "").lstrip("/")
            if not rel or ".." in rel.split("/"):
                continue
            target = (self.project_dir / rel).resolve()
            if not target.is_relative_to(self.project_dir.resolve()):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = await client.beta.files.download(f.id)
            await content.write_to_file(target)
