"""Director Agent - the brain that decomposes tasks and orchestrates the swarm.

Uses the DIRECTOR_HARNESS for constraint enforcement and failure mode inoculation.
"""

from __future__ import annotations

import logging
from typing import Any

from autonoma.agents.base import AutonomousAgent, _extract_json
from autonoma.agents.harness import DIRECTOR_HARNESS, get_harness
from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.tracing import traced_messages_create
from autonoma.models import (
    AgentMessage,
    AgentPersona,
    AgentState,
    MessageType,
    ProjectState,
    Task,
    TaskPriority,
    TaskStatus,
)

logger = logging.getLogger(__name__)

DIRECTOR_PERSONA = AgentPersona(
    name="Director",
    emoji="👑",
    role="Project Director - decomposes goals into tasks, spawns agents, ensures completion",
    skills=["planning", "task decomposition", "team management", "architecture"],
    color="yellow",
)


class DirectorAgent(AutonomousAgent):
    """The Director observes the project state and autonomously:
    1. Decomposes the user's goal into tasks
    2. Decides what agents are needed (with harness-aware role matching)
    3. Assigns work and monitors progress
    4. Spawns new agents when needed
    5. Declares the project complete
    """

    def __init__(self) -> None:
        super().__init__(DIRECTOR_PERSONA, harness=DIRECTOR_HARNESS)
        self._stall_counter = 0

    async def decompose_goal(self, project: ProjectState) -> list[Task]:
        """Break down the project description into actionable tasks."""
        await self._set_state(AgentState.THINKING)
        await self._say("Let me break this down...", style="bold yellow")

        system = """You are a project director. Given a project description, decompose it into
concrete, actionable tasks. Each task should be achievable by a single agent.

Available agent roles (each has a specialized harness):
- Coder: Writes code, creates implementation files
- Designer: Plans architecture, creates design docs
- Tester: Verifies implementations, writes tests, finds edge cases (adversarial)
- Reviewer: Reviews code quality (read-only, cannot create files)
- Writer: Creates documentation, README, API docs

Respond with JSON:
{
  "analysis": "Brief analysis of what needs to be built",
  "agents_needed": [
    {"name": "AgentName", "emoji": "emoji", "role": "one of: coder/designer/tester/reviewer/writer", "skills": ["skill1"], "color": "color"}
  ],
  "tasks": [
    {
      "title": "Short task title",
      "description": "Detailed description of what to do",
      "priority": "low|medium|high|critical",
      "suggested_agent": "AgentName or null",
      "depends_on_titles": ["titles of tasks this depends on"]
    }
  ]
}

Rules:
- Decide team size based on the project's actual complexity — a trivial
  one-file script needs 1-2 agents, a multi-subsystem build may need 5+.
- Create 4-12 specific, actionable tasks
- Suggest 2-5 specialized agents with creative names and emojis
- ALWAYS include at least one Tester agent for verification
- Include design, implementation, testing, and documentation tasks
- Set dependencies correctly (implementation depends on design, etc.)
- Tasks should produce concrete file artifacts
- Match agent roles to the available harness types above"""

        prompt = (
            f"Project: {project.name}\n"
            f"Description: {project.description}\n\n"
            "Decompose this into tasks and decide what agents we need."
        )

        try:
            response = await traced_messages_create(
                self.client,
                agent="Director",
                phase="decompose_goal",
                model=settings.model,
                max_tokens=settings.max_tokens,
                temperature=0.2,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
            data = _extract_json(response.content[0].text)
        except Exception as e:
            logger.error(f"[Director] Goal decomposition failed: {e}")
            await self._say(f"Planning error: {e}", style="bold red")
            await bus.emit("director.plan_failed", error=str(e))
            fallback = Task(
                title="Implement project",
                description=project.description,
                priority=TaskPriority.HIGH,
                created_by="Director",
            )
            project.tasks = [fallback]
            await bus.emit(
                "agent.spawn_requested",
                requester="Director",
                name="Builder",
                role="coder",
                skills=["coding", "testing"],
                emoji="🔨",
                color="cyan",
            )
            return [fallback]

        # Create tasks
        tasks: list[Task] = []
        title_to_id: dict[str, str] = {}

        for t_data in data.get("tasks", []):
            try:
                priority = TaskPriority(t_data.get("priority", "medium"))
            except ValueError:
                priority = TaskPriority.MEDIUM

            task = Task(
                title=t_data.get("title", "Unnamed task"),
                description=t_data.get("description", ""),
                priority=priority,
                created_by="Director",
            )
            tasks.append(task)
            title_to_id[task.title] = task.id

        # Resolve dependencies by title with validation
        unresolved_deps: list[str] = []
        for task, t_data in zip(tasks, data.get("tasks", [])):
            for dep_title in t_data.get("depends_on_titles", []):
                dep_id = title_to_id.get(dep_title)
                if dep_id:
                    task.depends_on.append(dep_id)
                else:
                    unresolved_deps.append(f"{task.title} -> {dep_title}")

        if unresolved_deps:
            logger.warning(f"[Director] Unresolved dependencies: {unresolved_deps}")

        if not tasks:
            logger.warning("[Director] LLM returned empty task list, creating fallback")
            tasks = [Task(
                title="Implement project",
                description=project.description,
                priority=TaskPriority.HIGH,
                created_by="Director",
            )]

        project.tasks = tasks

        # Emit spawn requests for needed agents (with harness-aware role matching)
        agents_spawned = 0
        for agent_data in data.get("agents_needed", []):
            role = agent_data.get("role", "coder")
            # Match to harness to get proper capabilities
            harness = get_harness(role)

            await bus.emit(
                "agent.spawn_requested",
                requester="Director",
                name=agent_data.get("name", "Worker"),
                role=role,
                skills=agent_data.get("skills", harness.default_skills),
                emoji=agent_data.get("emoji", harness.emoji),
                color=agent_data.get("color", harness.color),
            )
            agents_spawned += 1

        # Ensure at least one worker agent exists
        if agents_spawned == 0:
            await bus.emit(
                "agent.spawn_requested",
                requester="Director",
                name="Builder",
                role="coder",
                skills=["coding", "testing", "documentation"],
                emoji="🔨",
                color="cyan",
            )

        await self._say(
            f"Plan ready! {len(tasks)} tasks, {agents_spawned} agents",
            style="bold green",
        )
        await bus.emit(
            "director.plan_ready",
            task_count=len(tasks),
            agent_count=agents_spawned,
            analysis=data.get("analysis", ""),
        )

        return tasks

    async def think_and_act(self, project: ProjectState) -> dict[str, Any]:
        """Director's special loop: monitor, assign, and manage."""
        await self._set_state(AgentState.THINKING)

        # Check if all tasks are done
        if project.tasks and all(t.status == TaskStatus.DONE for t in project.tasks):
            await self._set_state(AgentState.CELEBRATING)
            await self._say("Project complete! Amazing teamwork!", style="bold green")
            project.completed = True
            await bus.emit("project.completed", agent="Director")
            return {"agent": "Director", "action": "project_complete"}

        # Assign unassigned tasks to available agents (respecting dependencies)
        open_tasks = [
            t for t in project.tasks
            if t.status == TaskStatus.OPEN
            and all(
                any(t2.id == dep and t2.status == TaskStatus.DONE for t2 in project.tasks)
                for dep in t.depends_on
            )
        ]

        available_agents = [
            a.name for a in project.agents
            if a.name != "Director"
            and not any(
                t.assigned_to == a.name and t.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)
                for t in project.tasks
            )
        ]

        assigned_count = 0
        for task, agent_name in zip(open_tasks, available_agents):
            task.assigned_to = agent_name
            task.status = TaskStatus.ASSIGNED
            await self._say(f"{agent_name}, handle '{task.title}'!", style="bold")

            msg = AgentMessage(
                sender="Director",
                recipient=agent_name,
                msg_type=MessageType.TASK_ASSIGN,
                content=f"Task: {task.title}\n{task.description}\nPriority: {task.priority.value}",
                data={"task_id": task.id},
            )
            project.messages.append(msg)

            await bus.emit(
                "task.assigned",
                agent="Director",
                assigned_to=agent_name,
                task_id=task.id,
                title=task.title,
            )
            assigned_count += 1

        # Detect stalls
        done = sum(1 for t in project.tasks if t.status == TaskStatus.DONE)
        total = len(project.tasks)
        in_progress = sum(1 for t in project.tasks if t.status == TaskStatus.IN_PROGRESS)
        blocked = sum(1 for t in project.tasks if t.status == TaskStatus.BLOCKED)

        if assigned_count == 0 and in_progress == 0 and done < total:
            self._stall_counter += 1
            if self._stall_counter >= 3:
                stuck_tasks = [t for t in project.tasks if t.status == TaskStatus.OPEN]
                if stuck_tasks and available_agents:
                    stuck_tasks[0].depends_on.clear()
                    await self._say("Unblocking stuck task!", style="bold red")
                    self._stall_counter = 0
        else:
            self._stall_counter = 0

        if total > 0:
            await self._say(
                f"Progress: {done}/{total} done, {in_progress} active",
                style="bold yellow",
            )

        return {
            "agent": "Director",
            "action": "manage",
            "done": done,
            "total": total,
            "assigned": assigned_count,
        }
