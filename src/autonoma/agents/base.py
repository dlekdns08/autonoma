"""Base autonomous agent - self-organizing, harness-aware, with think-act loop.

Ported harness patterns from Claude Code:
- Dual enforcement of constraints (harness config + system prompt)
- Failure mode inoculation in every agent's prompt
- Critical reminders injected each turn (dead-man's switch)
- Structured output parsing for machine-readable results
- Capability restriction at action dispatch level
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from autonoma.agents.harness import AgentHarness, CODER_HARNESS, get_harness
from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.harness import (  # noqa: F401 — triggers @register
    action_strategies as _action_strategies,
    enforcement_strategies as _enforcement_strategies,
    llm_error_strategies as _llm_error_strategies,
    memory_strategies as _memory_strategies,
    message_strategies as _message_strategies,
    safety_strategies as _safety_strategies,
)
from autonoma.harness.policy import HarnessPolicyContent
from autonoma.harness.strategies import lookup as _strategy_lookup
from autonoma.llm import (
    BaseLLMClient,
    LLMConfig,
    LLMConnectionError,
    LLMRateLimitError,
    create_llm_client,
    llm_config_from_settings,
)
from autonoma.sandbox import CodeSandbox, Language, SandboxLimits
from autonoma.tracing import traced_messages_create
from autonoma.models import (
    AgentMessage,
    AgentPersona,
    AgentState,
    FileArtifact,
    MessageType,
    Position,
    ProjectState,
    SpeechBubble,
    Task,
    TaskStatus,
)
from autonoma.world import (
    AgentBones,
    AgentMemory,
    AgentStats,
    Mood,
    check_achievements,
)

logger = logging.getLogger(__name__)

MAX_INBOX_SIZE = 50
LLM_TIMEOUT_SECONDS = 60

# Message priority: lower number = higher priority (processed/kept first).
# Critical coordination messages are never crowded out by chat.
_MSG_PRIORITY: dict[str, int] = {
    "task_assignment": 0,
    "help_request":    1,
    "help_response":   2,
    "review_request":  3,
    "review_response": 3,
    "chat":            9,
}

def _msg_priority(msg: "AgentMessage") -> int:
    mt = msg.msg_type
    return _MSG_PRIORITY.get(mt.value if hasattr(mt, "value") else str(mt), 5)

# Mood → reaction-bubble icon. Drives the small EmoteBubble that the
# frontend paints above the sprite when an agent speaks. Unlisted moods
# emit nothing (the bubble simply stays empty); we'd rather miss a beat
# than over-spam icons during a long run.
MOOD_EMOTE: dict[str, str] = {
    "happy": "♪",
    "excited": "✦",
    "frustrated": "💢",
    "worried": "💧",
    "tired": "💤",
    "proud": "★",
    "nostalgic": "✿",
    "inspired": "💡",
    "curious": "?",
    "determined": "‼",
    "relaxed": "～",
    "focused": "•",
    "mischievous": "✧",
}

# Shared async lock protecting the task list of the single running project.
# Both the Director's assignment code and individual agents' self-assign path
# must acquire this lock before mutating Task.status / Task.assigned_to to
# avoid two agents claiming the same OPEN task in the same round.
#
# The swarm only runs one project at a time in-process, so a module-level
# lock is sufficient; if that assumption changes, move this onto ProjectState.
TASKS_LOCK: asyncio.Lock = asyncio.Lock()


def _atomic_claim_task(task: Task, agent_name: str) -> None:
    """Atomically mark a task as IN_PROGRESS and assigned to ``agent_name``.

    Must be called while holding ``TASKS_LOCK``. Keeps ``status`` and
    ``assigned_to`` in sync so callers never see a half-updated task.
    """
    task.assigned_to = agent_name
    task.status = TaskStatus.IN_PROGRESS


def _extract_json(text: str, strategy: str = "fallback_chain") -> dict[str, Any]:
    """Extract JSON from an LLM response.

    Dispatches to the registered ``action.json_extraction`` strategy.
    The default ``fallback_chain`` preserves the pre-harness behavior so
    callers that don't thread a policy through see no change.
    """
    return _strategy_lookup("action.json_extraction", strategy)(text)


class AutonomousAgent:
    """A fully autonomous agent with harness-aware think->act loop.

    The harness controls:
    - What actions the agent can perform (capability filtering)
    - How the agent thinks (system prompt with failure mode inoculation)
    - What reminders are injected each turn (dead-man's switch)
    - How results are structured (output format requirements)
    """

    def __init__(
        self,
        persona: AgentPersona,
        harness: AgentHarness | None = None,
        llm_config: LLMConfig | None = None,
        policy: HarnessPolicyContent | None = None,
    ) -> None:
        self.persona = persona
        self.harness = harness or get_harness(persona.role)
        # Runtime policy: per-session snapshot of every behavior knob.
        # ``HarnessPolicyContent()`` reproduces the pre-harness hardcoded
        # behavior exactly, so callers that don't care can pass ``None``.
        self.policy: HarnessPolicyContent = policy or HarnessPolicyContent()
        self.state = AgentState.IDLE
        self.position = Position(x=0, y=0)
        self.target_position: Position | None = None
        self.speech: SpeechBubble | None = None
        self.current_task: Task | None = None
        self.inbox: list[AgentMessage] = []
        self._llm_config: LLMConfig | None = llm_config
        self._client: BaseLLMClient | None = None
        self._history: list[dict[str, str]] = []
        self._total_tokens = 0
        self._consecutive_errors = 0

        # ── World System ──
        self.bones = AgentBones.from_role(persona.role, persona.name)
        self.mood = Mood.CURIOUS
        self.memory = AgentMemory()
        self.stats = AgentStats()
        # Populated by AgentSwarm._hydrate_agent when the persistent
        # character registry is enabled. Empty string means "no DB row";
        # code that writes to the graveyard / wills tables checks for
        # truthiness before recording.
        self.character_uuid: str = ""
        # Populated lazily by _resolve_voice() on first speech (or
        # eagerly by _hydrate_agent from the DB row).
        self.voice_id: str = ""

    @property
    def name(self) -> str:
        return self.persona.name

    @property
    def client(self) -> BaseLLMClient:
        if self._client is None:
            cfg = self._llm_config or llm_config_from_settings()
            self._client = create_llm_client(cfg)
        return self._client

    @property
    def _model(self) -> str:
        return self._llm_config.model if self._llm_config else settings.model

    @property
    def _temperature(self) -> float:
        return self._llm_config.temperature if self._llm_config else settings.temperature

    @property
    def _max_tokens(self) -> int:
        return self._llm_config.max_tokens if self._llm_config else settings.max_tokens

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    # ── Core Loop ──────────────────────────────────────────────────────

    async def think_and_act(self, project: ProjectState) -> dict[str, Any]:
        """Main autonomous loop: observe state, decide action, execute it."""
        await self._set_state(AgentState.THINKING)
        await self._say("Hmm, let me think...", style="italic dim")

        situation = self._build_situation(project)

        try:
            decision = await asyncio.wait_for(
                self._decide(situation), timeout=LLM_TIMEOUT_SECONDS
            )
            self._consecutive_errors = 0
        except asyncio.TimeoutError:
            self._consecutive_errors += 1
            await self._say("Timed out thinking...", style="bold red")
            return {"agent": self.name, "action": "idle", "error": "timeout"}

        action_type = decision.get("action", "idle")

        # ── Harness enforcement: block disallowed actions (config-level) ──
        enforcer = _strategy_lookup(
            "action.harness_enforcement", self.policy.action.harness_enforcement
        )
        if not enforcer(self.name, action_type, self.harness):
            logger.warning(
                f"[{self.name}] Harness blocked action '{action_type}' "
                f"(not in allowed capabilities for {self.harness.name})"
            )
            await self._say(f"Can't do that - not my role!", style="italic yellow")
            return {"agent": self.name, "action": "blocked", "blocked_action": action_type}

        result: dict[str, Any] = {"agent": self.name, "action": action_type}

        # Show speech from decision
        speech = decision.get("speech", "")
        if speech and action_type not in ("idle",):
            await self._say(speech, style="bold")

        try:
            if action_type == "work_on_task":
                result = await self._action_work(decision, project)
            elif action_type == "create_file":
                result = await self._action_create_file(decision, project)
            elif action_type == "send_message":
                result = await self._action_send_message(decision, project)
            elif action_type == "request_help":
                result = await self._action_request_help(decision, project)
            elif action_type == "review_work":
                result = await self._action_review(decision, project)
            elif action_type == "spawn_agent":
                result = await self._action_spawn(decision, project)
            elif action_type == "complete_task":
                result = await self._action_complete_task(decision, project)
            elif action_type == "run_code":
                result = await self._action_run_code(decision, project)
            elif action_type == "celebrate":
                await self._set_state(AgentState.CELEBRATING)
                await self._say("All done!", style="bold green")
                result = {"agent": self.name, "action": "celebrate"}
            else:
                await self._set_state(AgentState.IDLE)
        except Exception as e:
            logger.error(f"[{self.name}] Action '{action_type}' failed: {e}")
            await self._set_state(AgentState.ERROR)
            await self._say(f"Oops: {str(e)[:40]}", style="bold red")
            self.stats.errors += 1
            self.mood = Mood.FRUSTRATED
            self.memory.remember(f"Error in {action_type}: {str(e)[:60]}", "failure", self._round_number)
            result = {"agent": self.name, "action": "error", "error": str(e)}

        return result

    def receive_message(self, msg: AgentMessage) -> None:
        self.inbox.append(msg)
        if len(self.inbox) > MAX_INBOX_SIZE:
            trimmer = _strategy_lookup(
                "decision.message_priority", self.policy.decision.message_priority
            )
            self.inbox = trimmer(self.inbox, MAX_INBOX_SIZE, _msg_priority)

    # ── Decision Engine (Harness-Aware) ───────────────────────────────

    def _build_situation(self, project: ProjectState) -> str:
        """Build a situation report for the LLM."""
        open_tasks = [t for t in project.tasks if t.status in (TaskStatus.OPEN, TaskStatus.ASSIGNED)]
        my_tasks = [t for t in project.tasks if t.assigned_to == self.name and t.status != TaskStatus.DONE]
        done_tasks = [t for t in project.tasks if t.status == TaskStatus.DONE]
        files = [f.path for f in project.files]
        recent_msgs = [
            f"[{m.sender} -> {m.recipient}] {m.content[:100]}"
            for m in (self.inbox[-5:] if self.inbox else [])
        ]
        other_agents = [
            f"{a.emoji} {a.name} ({a.role})" for a in project.agents if a.name != self.name
        ]

        # World data
        bones = self.bones
        stats = self.stats
        level_bar_filled = int(stats.xp / max(1, stats.xp_to_next_level) * 10)
        level_bar = "★" * level_bar_filled + "☆" * (10 - level_bar_filled)

        situation = f"""== SITUATION REPORT ==
Project: {project.name} - {project.description}

YOUR IDENTITY:
  Name: {self.persona.name}
  Species: {bones.species} {bones.species_emoji}
  Role: {self.persona.role}
  Skills: {', '.join(self.persona.skills)}
  Traits: {', '.join(t.value for t in bones.traits)}
  Catchphrase: "{bones.catchphrase}"
  Mood: {self.mood.value}
  Level: {stats.level} [{level_bar}] ({stats.xp}/{stats.xp_to_next_level} XP)
  Achievements: {', '.join(stats.achievements) or 'None yet'}
  Harness: {self.harness.name} (capabilities: {', '.join(c.value for c in self.harness.get_effective_capabilities())})
  Current task: {self.current_task.title if self.current_task else 'None'}

MEMORIES:
{self.memory.get_summary(private_formatter=_strategy_lookup("memory.summarization", self.policy.memory.summarization))}

TEAM:
  {chr(10).join(other_agents) if other_agents else 'You are alone'}

OPEN TASKS ({len(open_tasks)}):
{chr(10).join(f'  [{t.id}] {t.title} (priority: {t.priority.value}, assigned: {t.assigned_to or "unassigned"})' for t in open_tasks[:10]) or '  None'}

MY ACTIVE TASKS ({len(my_tasks)}):
{chr(10).join(f'  [{t.id}] {t.title} - {t.description[:80]}' for t in my_tasks[:5]) or '  None'}

COMPLETED ({len(done_tasks)}):
{chr(10).join(f'  [{t.id}] {t.title}' for t in done_tasks[:5]) or '  None'}

FILES CREATED ({len(files)}):
{chr(10).join(f'  {f}' for f in files[:15]) or '  None'}

RECENT MESSAGES:
{chr(10).join(f'  {m}' for m in recent_msgs) or '  None'}
"""

        # Inject critical reminder (dead-man's switch from harness)
        reminder = self.harness.get_critical_reminder()
        if reminder:
            situation += f"\n{reminder}"

        return situation

    # Matches the start of the JSON "speech" value so we can begin emitting
    # typing tokens before the full response is assembled.
    _SPEECH_KEY_RE = re.compile(r'"speech"\s*:\s*"')

    async def _stream_decide(self, system: str, situation: str) -> str:
        """Stream the LLM decision, emitting agent.speech_token events as the
        'speech' field value arrives.  Returns the full raw text for JSON parse.

        State machine:
          SCAN  — accumulating buffer, haven't found the "speech": " key yet
          EMIT  — inside the speech value, forwarding chars as tokens
          DONE  — closed speech string, just draining the rest
        """
        chunks: list[str] = []
        buf = ""
        state: str = "SCAN"   # SCAN | EMIT | DONE
        speech_buf: list[str] = []

        async for chunk in self.client.stream(
            model=self._model,
            max_tokens=4096,
            temperature=self._temperature,
            system=system,
            messages=[{"role": "user", "content": situation}],
        ):
            chunks.append(chunk)

            if state == "DONE":
                continue

            # Process each character for speech extraction
            for ch in chunk:
                if state == "SCAN":
                    buf += ch
                    # Keep buffer small: only need the tail where the key could start
                    if len(buf) > 200:
                        buf = buf[-200:]
                    if self._SPEECH_KEY_RE.search(buf):
                        state = "EMIT"
                        buf = ""

                elif state == "EMIT":
                    if ch == '"' and (not speech_buf or speech_buf[-1] != "\\"):
                        # Closing quote — speech value complete
                        state = "DONE"
                        speech_text = "".join(speech_buf)
                        if speech_text:
                            await bus.emit(
                                "agent.speech_token",
                                agent=self.name,
                                text=speech_text,
                                done=True,
                            )
                    else:
                        speech_buf.append(ch)
                        # Emit every ~4 chars to keep WS fan-out costs low
                        if len(speech_buf) % 4 == 0:
                            await bus.emit(
                                "agent.speech_token",
                                agent=self.name,
                                token=ch,
                                partial="".join(speech_buf),
                                done=False,
                            )

        return "".join(chunks)

    async def _decide(self, situation: str) -> dict[str, Any]:
        """Ask LLM to decide the next action, using harness-aware system prompt."""

        # Build system prompt from harness (includes failure mode inoculation)
        system = self.harness.build_system_prompt(self.persona.name, self.persona.skills)

        # Add JSON action format
        system += """
Based on the situation, decide your SINGLE next action. Respond with JSON:
{
  "thinking": "Brief internal thought about what to do next",
  "action": "one of: work_on_task, create_file, send_message, request_help, review_work, spawn_agent, complete_task, run_code, celebrate, idle",
  "speech": "What you say out loud (keep it short, personality-driven)",
  "target_task_id": "task id if working on specific task",
  "file_path": "if creating a file",
  "file_content": "if creating a file",
  "file_description": "if creating a file",
  "message_to": "agent name if sending message",
  "message_content": "if sending message",
  "spawn_name": "name for new agent if spawning",
  "spawn_role": "role for new agent",
  "spawn_skills": ["skills for new agent"],
  "task_output": "result summary if completing a task",
  "verdict": "PASS|FAIL|PARTIAL if reviewing (required for reviewers/testers)",
  "code_language": "python|bash|node (if run_code)",
  "code_body": "the actual program source to execute in the sandbox (if run_code). Stdlib only, no network. Keep it short — a few seconds of CPU max. Use print() to report results."
}

Rules:
- Pick up unassigned tasks that match your skills
- If you have an assigned task, work on it
- Create files when your task requires code/content
- Ask for help if stuck
- Be proactive and creative
- Keep speech SHORT and in-character (1 sentence max)"""

        try:
            full_text = await self._stream_decide(system, situation)
            return _extract_json(full_text, strategy=self.policy.action.json_extraction)

        except (LLMConnectionError, LLMRateLimitError) as e:
            logger.warning(f"[{self.name}] LLM error ({type(e).__name__}): {e}")
            handler = _strategy_lookup(
                "action.llm_error_handling", self.policy.action.llm_error_handling
            )
            return await handler(e, self.name)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[{self.name}] Failed to parse LLM response: {e}")
            handler = _strategy_lookup(
                "decision.on_parse_failure", self.policy.decision.on_parse_failure
            )
            return handler(e, self.name)
        except Exception as e:
            logger.error(f"[{self.name}] Unexpected error in decide: {e}")
            return {"action": "idle", "speech": f"Error: {str(e)[:30]}", "thinking": "error"}

    # ── Actions ────────────────────────────────────────────────────────

    async def _action_work(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        """Pick up or continue working on a task.

        Uses ``TASKS_LOCK`` so the status/assigned_to double-write is atomic
        with respect to the Director's assignment loop — prevents two agents
        claiming the same OPEN task in the same round.
        """
        await self._set_state(AgentState.WORKING)

        task_id = decision.get("target_task_id")
        if task_id:
            async with TASKS_LOCK:
                task = next((t for t in project.tasks if t.id == task_id), None)
                claimed = False
                started = False
                if task:
                    if task.status == TaskStatus.OPEN:
                        _atomic_claim_task(task, self.name)
                        self.current_task = task
                        claimed = True
                    elif task.assigned_to == self.name and task.status == TaskStatus.ASSIGNED:
                        task.status = TaskStatus.IN_PROGRESS
                        self.current_task = task
                        started = True
            # Emit events outside the lock to avoid holding it across awaits.
            if task is not None and claimed:
                await bus.emit(
                    "task.assigned", agent=self.name, task_id=task.id, title=task.title,
                )
            elif task is not None and started:
                await bus.emit("task.started", agent=self.name, task_id=task.id)

        return {"agent": self.name, "action": "work_on_task", "task_id": task_id}

    async def _action_create_file(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        """Create a file artifact."""
        await self._set_state(AgentState.WORKING)
        path = decision.get("file_path", "untitled.py")
        content = decision.get("file_content", "")
        desc = decision.get("file_description", "")

        # Sanitize path - prevent directory traversal
        path = path.lstrip("/").replace("..", "")

        artifact = FileArtifact(
            path=path, content=content, created_by=self.name, description=desc
        )
        project.files.append(artifact)

        if self.current_task:
            self.current_task.artifacts.append(path)

        self.stats.files_created += 1
        self.stats.add_xp(15)
        self.mood = Mood.PROUD
        self.memory.remember(f"Created file: {path}", "success", self._round_number)

        await bus.emit(
            "file.created",
            agent=self.name,
            path=path,
            size=len(content),
            description=desc,
        )
        self._check_and_emit_achievements()
        return {"agent": self.name, "action": "create_file", "path": path}

    async def _action_send_message(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        """Send a message to another agent."""
        await self._set_state(AgentState.TALKING)
        recipient = decision.get("message_to", "all")
        content = decision.get("message_content", "")

        msg = AgentMessage(
            sender=self.name,
            recipient=recipient,
            msg_type=MessageType.CHAT,
            content=content,
        )
        project.messages.append(msg)
        self.stats.messages_sent += 1
        self.stats.add_xp(5)
        self.mood = Mood.FRIENDLY if self.mood != Mood.FRUSTRATED else Mood.DETERMINED
        await bus.emit(
            "message.sent",
            sender=self.name,
            recipient=recipient,
            content=content[:80],
        )
        return {"agent": self.name, "action": "send_message", "to": recipient}

    async def _action_request_help(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        await self._set_state(AgentState.TALKING)

        msg = AgentMessage(
            sender=self.name,
            recipient="all",
            msg_type=MessageType.HELP_REQUEST,
            content=decision.get("message_content", "Need assistance"),
        )
        project.messages.append(msg)
        await bus.emit("help.requested", agent=self.name, content=msg.content)
        return {"agent": self.name, "action": "request_help"}

    async def _action_review(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        await self._set_state(AgentState.THINKING)

        # Extract verdict if present (structured output from harness)
        verdict = decision.get("verdict", "")
        review_content = decision.get("message_content", "")

        if verdict:
            await self._say(f"Review: {verdict}", style="bold magenta")

        await bus.emit("review.started", agent=self.name, verdict=verdict)
        return {"agent": self.name, "action": "review_work", "verdict": verdict}

    async def _action_spawn(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        await self._set_state(AgentState.SPAWNING)
        name = decision.get("spawn_name", "helper")
        role = decision.get("spawn_role", "general assistant")
        skills = decision.get("spawn_skills", ["coding"])

        await bus.emit(
            "agent.spawn_requested",
            requester=self.name,
            name=name,
            role=role,
            skills=skills,
        )
        return {"agent": self.name, "action": "spawn_agent", "spawn_name": name}

    async def _action_complete_task(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        await self._set_state(AgentState.CELEBRATING)

        task_id = decision.get("target_task_id")
        output = decision.get("task_output", "")

        if task_id:
            task = next((t for t in project.tasks if t.id == task_id), None)
            if task and task.assigned_to == self.name:
                task.status = TaskStatus.DONE
                task.output = output
                task.completed_at = datetime.now()

                self.stats.tasks_completed += 1
                leveled_up = self.stats.add_xp(30)
                self.mood = Mood.EXCITED if leveled_up else Mood.PROUD
                self.memory.remember(
                    f"Completed task: {task.title}", "success", self._round_number
                )

                await bus.emit(
                    "task.completed", agent=self.name, task_id=task.id, title=task.title
                )
                if leveled_up:
                    await bus.emit(
                        "agent.level_up",
                        agent=self.name,
                        level=self.stats.level,
                        species=self.bones.species,
                    )

        self.current_task = None
        self._check_and_emit_achievements()
        return {"agent": self.name, "action": "complete_task", "task_id": task_id}

    async def _action_run_code(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        """Execute LLM-authored code in a sandbox and feed the result back to the agent."""
        allow_fn = _strategy_lookup(
            "safety.code_execution", self.policy.safety.code_execution
        )
        if not allow_fn():
            # Gate checked before any sandbox work so ``disabled`` means
            # nothing reaches CodeSandbox at all. The error string is
            # stable so agents / UI can surface a specific hint.
            await self._set_state(AgentState.THINKING)
            return {
                "agent": self.name,
                "action": "run_code",
                "error": "code_execution_disabled",
            }

        code = (decision.get("code_body") or "").strip()
        lang_raw = (decision.get("code_language") or "python").strip().lower()

        if not code:
            await self._set_state(AgentState.THINKING)
            return {"agent": self.name, "action": "run_code", "error": "empty_code"}

        try:
            language = Language(lang_raw)
        except ValueError:
            return {
                "agent": self.name,
                "action": "run_code",
                "error": f"unsupported_language:{lang_raw}",
            }

        limits = SandboxLimits(
            wall_time_sec=settings.sandbox_wall_time_sec,
            cpu_time_sec=settings.sandbox_cpu_time_sec,
            memory_mb=settings.sandbox_memory_mb,
            max_output_bytes=settings.sandbox_max_output_bytes,
        )

        await self._set_state(AgentState.WORKING)
        await bus.emit(
            "sandbox.run_started",
            agent=self.name,
            language=language.value,
            bytes=len(code),
        )

        try:
            result = await CodeSandbox(limits=limits).run(code, language)
        except Exception as exc:
            logger.error(f"[{self.name}] sandbox crashed: {exc}")
            self.memory.remember(
                f"Sandbox crashed: {str(exc)[:60]}", "failure", self._round_number
            )
            return {"agent": self.name, "action": "run_code", "error": str(exc)}

        summary = result.summarize(max_chars=400)
        self.memory.remember(summary, "success" if result.ok else "failure", self._round_number)

        feedback = (
            f"[sandbox run: {language.value} | {result.backend} | "
            f"exit={result.exit_code} | {result.duration_sec}s"
            f"{' | TIMEOUT' if result.timed_out else ''}"
            f"{' | TRUNCATED' if result.truncated else ''}]\n"
            f"--- stdout ---\n{result.stdout or '(empty)'}\n"
            f"--- stderr ---\n{result.stderr or '(empty)'}\n"
        )
        self_msg = AgentMessage(
            sender="sandbox",
            recipient=self.name,
            content=feedback[:4000],
            msg_type=MessageType.CHAT,
        )
        self.receive_message(self_msg)

        self.stats.sandbox_runs = getattr(self.stats, "sandbox_runs", 0) + 1
        if result.ok:
            self.stats.add_xp(5)

        await bus.emit(
            "sandbox.run_finished",
            agent=self.name,
            language=language.value,
            backend=result.backend,
            exit_code=result.exit_code,
            duration=result.duration_sec,
            ok=result.ok,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )

        return {
            "agent": self.name,
            "action": "run_code",
            "backend": result.backend,
            "ok": result.ok,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration": result.duration_sec,
        }

    _round_number: int = 0  # Updated by swarm each round

    def _check_and_emit_achievements(self) -> None:
        """Check for new achievements and emit events."""
        newly_earned = check_achievements(self.stats)
        for ach_id in newly_earned:
            from autonoma.world import ACHIEVEMENTS
            ach = ACHIEVEMENTS[ach_id]
            self.memory.remember(f"Achievement unlocked: {ach['title']}", "success", self._round_number)
            # Event emission is fire-and-forget since we can't await here
            # The swarm loop will pick it up

    # ── TUI Helpers ────────────────────────────────────────────────────

    async def _say(self, text: str, style: str = "dim") -> None:
        # Apply per-character speech styling so every utterance picks up
        # the agent's sonic personality (rarity + traits + mood). The
        # transform is deterministic and never paraphrases — call sites
        # don't need to know it ran.
        from autonoma.dialogue_style import style_speech
        text = style_speech(
            name=self.name,
            text=text,
            bones=getattr(self, "bones", None),
            mood=self.mood.value if self.mood else "",
        )
        self.speech = SpeechBubble(text=text[:60], style=style)
        await bus.emit("agent.speech", agent=self.name, text=text, style=style)
        # Fire TTS when enabled. Every agent gets a deterministic voice
        # from its bones seed the first time it speaks (or via the
        # persistent registry). We enqueue and return immediately — the
        # worker handles budget, rate-limiting, and audio fan-out.
        if settings.tts_enabled:
            voice = self._resolve_voice()
            from autonoma.tts_worker import get_default_worker
            get_default_worker().enqueue(
                agent=self.name,
                text=text,
                voice=voice,
                mood=self.mood.value if self.mood else "",
                language=settings.tts_default_language,
            )
        # Reaction icon — derived from mood. Stays cheap (one event per
        # speech) and lets the frontend paint a small EmoteBubble above
        # the sprite without needing per-mood event plumbing.
        icon = MOOD_EMOTE.get(self.mood.value if self.mood else "", "")
        if icon:
            await self._emote(icon)

    async def _emote(self, icon: str, ttl_ms: int = 2000) -> None:
        """Show a short reaction icon above the agent. Cheap, fire-and-forget.

        ``ttl_ms`` is advisory — the frontend uses it to time the fade-out.
        We don't track the bubble server-side; nothing here pages on emote
        state, so persistence would just be ceremony.
        """
        await bus.emit("agent.emote", agent=self.name, icon=icon, ttl_ms=ttl_ms)

    def _resolve_voice(self) -> str:
        """Return the voice id for this agent. Memoized on ``voice_id``
        so the same character always sounds the same across a run."""
        if getattr(self, "voice_id", ""):
            return self.voice_id
        import hashlib
        from autonoma.tts import pick_voice_for
        seed = hashlib.md5(
            f"{self.persona.role}:{self.persona.name}:autonoma-world-v1".encode()
        ).hexdigest()
        self.voice_id = pick_voice_for(
            seed_hash=seed,
            provider=settings.tts_provider,
            language=settings.tts_default_language,
        )
        return self.voice_id

    async def _set_state(self, state: AgentState) -> None:
        self.state = state
        await bus.emit("agent.state", agent=self.name, state=state.value)

    def tick_speech(self) -> None:
        if self.speech:
            self.speech.ttl -= 1
            if self.speech.ttl <= 0:
                self.speech = None

    def tick_movement(self) -> None:
        if self.target_position and self.position.distance_to(self.target_position) > 1:
            self.position = self.position.move_toward(self.target_position, speed=2)
        else:
            self.target_position = None
