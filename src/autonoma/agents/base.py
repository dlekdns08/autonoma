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

import anthropic

from autonoma.agents.harness import AgentHarness, CODER_HARNESS, get_harness
from autonoma.config import settings
from autonoma.event_bus import bus
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


def _extract_json(text: str) -> dict[str, Any]:
    """Robustly extract JSON from LLM response, handling markdown fences."""
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try finding first { ... } block
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


class AutonomousAgent:
    """A fully autonomous agent with harness-aware think->act loop.

    The harness controls:
    - What actions the agent can perform (capability filtering)
    - How the agent thinks (system prompt with failure mode inoculation)
    - What reminders are injected each turn (dead-man's switch)
    - How results are structured (output format requirements)
    """

    def __init__(self, persona: AgentPersona, harness: AgentHarness | None = None) -> None:
        self.persona = persona
        self.harness = harness or get_harness(persona.role)
        self.state = AgentState.IDLE
        self.position = Position(x=0, y=0)
        self.target_position: Position | None = None
        self.speech: SpeechBubble | None = None
        self.current_task: Task | None = None
        self.inbox: list[AgentMessage] = []
        self._client: anthropic.AsyncAnthropic | None = None
        self._history: list[dict[str, str]] = []
        self._total_tokens = 0
        self._consecutive_errors = 0

        # ── World System ──
        self.bones = AgentBones.from_role(persona.role, persona.name)
        self.mood = Mood.CURIOUS
        self.memory = AgentMemory()
        self.stats = AgentStats()

    @property
    def name(self) -> str:
        return self.persona.name

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key or None
            )
        return self._client

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
        if not self.harness.can_perform(action_type):
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
            self.inbox = self.inbox[-MAX_INBOX_SIZE:]

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
{self.memory.get_summary()}

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

    async def _decide(self, situation: str) -> dict[str, Any]:
        """Ask LLM to decide the next action, using harness-aware system prompt."""

        # Build system prompt from harness (includes failure mode inoculation)
        system = self.harness.build_system_prompt(self.persona.name, self.persona.skills)

        # Add JSON action format
        system += """
Based on the situation, decide your SINGLE next action. Respond with JSON:
{
  "thinking": "Brief internal thought about what to do next",
  "action": "one of: work_on_task, create_file, send_message, request_help, review_work, spawn_agent, complete_task, celebrate, idle",
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
  "verdict": "PASS|FAIL|PARTIAL if reviewing (required for reviewers/testers)"
}

Rules:
- Pick up unassigned tasks that match your skills
- If you have an assigned task, work on it
- Create files when your task requires code/content
- Ask for help if stuck
- Be proactive and creative
- Keep speech SHORT and in-character (1 sentence max)"""

        try:
            response = await self.client.messages.create(
                model=settings.model,
                max_tokens=4096,
                temperature=settings.temperature,
                system=system,
                messages=[{"role": "user", "content": situation}],
            )
            self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
            return _extract_json(response.content[0].text)

        except anthropic.APIConnectionError as e:
            logger.warning(f"[{self.name}] API connection error: {e}")
            return {"action": "idle", "speech": "Can't reach the API...", "thinking": "connection_error"}
        except anthropic.RateLimitError:
            logger.warning(f"[{self.name}] Rate limited, backing off")
            await asyncio.sleep(2)
            return {"action": "idle", "speech": "Rate limited, waiting...", "thinking": "rate_limited"}
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[{self.name}] Failed to parse LLM response: {e}")
            return {"action": "idle", "speech": "Couldn't parse my thoughts...", "thinking": "parse_error"}
        except Exception as e:
            logger.error(f"[{self.name}] Unexpected error in decide: {e}")
            return {"action": "idle", "speech": f"Error: {str(e)[:30]}", "thinking": "error"}

    # ── Actions ────────────────────────────────────────────────────────

    async def _action_work(self, decision: dict, project: ProjectState) -> dict[str, Any]:
        """Pick up or continue working on a task."""
        await self._set_state(AgentState.WORKING)

        task_id = decision.get("target_task_id")
        if task_id:
            task = next((t for t in project.tasks if t.id == task_id), None)
            if task:
                if task.status == TaskStatus.OPEN:
                    task.assigned_to = self.name
                    task.status = TaskStatus.IN_PROGRESS
                    self.current_task = task
                    await bus.emit("task.assigned", agent=self.name, task_id=task.id, title=task.title)
                elif task.assigned_to == self.name and task.status == TaskStatus.ASSIGNED:
                    task.status = TaskStatus.IN_PROGRESS
                    self.current_task = task
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
        self.speech = SpeechBubble(text=text[:60], style=style)
        await bus.emit("agent.speech", agent=self.name, text=text, style=style)

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
