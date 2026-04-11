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

    async def synthesize_final_answer(self, project: ProjectState) -> str:
        """Produce the project's final answer in Korean.

        Called once at the end of a swarm run. Summarizes what the swarm
        built and answers the original goal directly. Best-effort: on any
        failure, returns a simple templated Korean summary so the UI never
        shows an empty final-answer panel.
        """
        tasks_done = [t for t in project.tasks if t.status == TaskStatus.DONE]
        tasks_open = [t for t in project.tasks if t.status != TaskStatus.DONE]
        files = project.files

        task_lines = "\n".join(
            f"- [{t.status.value}] {t.title}" for t in project.tasks
        ) or "(없음)"
        file_lines = "\n".join(
            f"- {f.path} ({len(f.content)} bytes) — {f.description or ''}".rstrip(" —")
            for f in files[:30]
        ) or "(없음)"

        system = (
            "당신은 프로젝트 디렉터입니다. 방금 끝난 에이전트 스웜 프로젝트에 대한 "
            "최종 답변을 **반드시 한국어로** 작성하세요. 사용자가 원래 제시한 목표에 "
            "직접 답변하고, 무엇을 만들었는지, 핵심 결과물이 무엇인지, 남아있는 이슈가 "
            "있다면 무엇인지 간결하게 설명하세요. 마크다운을 사용해도 좋습니다. "
            "영어로 답변하지 마세요."
        )
        prompt = (
            f"# 프로젝트 목표\n{project.description}\n\n"
            f"# 태스크 현황 ({len(tasks_done)}/{len(project.tasks)} 완료)\n{task_lines}\n\n"
            f"# 생성된 파일 ({len(files)}개)\n{file_lines}\n\n"
            f"# 미완료 태스크: {len(tasks_open)}개\n\n"
            "위 정보를 바탕으로 사용자에게 전달할 최종 답변을 한국어로 작성하세요. "
            "다음 섹션을 포함하세요:\n"
            "1. **요약** — 한두 문장으로 무엇을 했는지\n"
            "2. **최종 답변** — 원래 목표에 대한 직접적인 답변 또는 결과물 설명\n"
            "3. **생성된 결과물** — 주요 파일 목록과 각 파일의 역할\n"
            "4. **남은 이슈** — 미완료 태스크나 주의사항이 있다면 (없으면 생략)\n"
        )

        try:
            response = await traced_messages_create(
                self.client,
                agent="Director",
                phase="final_answer",
                model=settings.model,
                max_tokens=settings.max_tokens,
                temperature=0.3,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
            text = response.content[0].text.strip() if response.content else ""
            if text:
                return text
        except Exception as e:
            logger.error(f"[Director] Final answer synthesis failed: {e}")

        # Fallback Korean template
        return (
            f"## 요약\n"
            f"'{project.name}' 프로젝트에서 {len(tasks_done)}개의 태스크를 완료하고 "
            f"{len(files)}개의 파일을 생성했습니다.\n\n"
            f"## 생성된 결과물\n{file_lines}\n\n"
            f"## 남은 이슈\n미완료 태스크 {len(tasks_open)}개"
            if tasks_open else ""
        )

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
