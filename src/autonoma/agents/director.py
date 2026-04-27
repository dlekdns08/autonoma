"""Director Agent - the brain that decomposes tasks and orchestrates the swarm.

Uses the DIRECTOR_HARNESS for constraint enforcement and failure mode inoculation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from autonoma.agents.base import (
    AutonomousAgent,
    _atomic_claim_task,
    _extract_json,
    get_tasks_lock,
)
from autonoma.agents.harness import DIRECTOR_HARNESS, get_harness
from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.harness import stall_strategies as _stall_strategies  # noqa: F401 — triggers @register
from autonoma.harness.policy import HarnessPolicyContent
from autonoma.harness.strategies import lookup as _strategy_lookup
from autonoma.llm import LLMConfig
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
    compute_critical_path,
    overdue_tasks,
)

logger = logging.getLogger(__name__)

DIRECTOR_PERSONA = AgentPersona(
    name="Director",
    emoji="👑",
    role="Project Director - decomposes goals into tasks, spawns agents, ensures completion",
    skills=["planning", "task decomposition", "team management", "architecture"],
    color="yellow",
)


# ── Feature 2: Skill-Based Task Matching helpers ───────────────────────────

def skill_similarity(task_title: str, task_description: str, agent_skills: list[str]) -> float:
    """Compute a simple skill-match score for a task against an agent's skills.

    Tokenizes the task title + description and counts how many agent skills
    appear as substrings. Returns count / max(1, len(agent_skills)), capped at 1.0.
    """
    if not agent_skills:
        return 0.0
    combined = (task_title + " " + task_description).lower()
    matches = sum(1 for skill in agent_skills if skill.lower() in combined)
    return min(1.0, matches / len(agent_skills))


def find_best_agent_for_task(task: Task, agents: list) -> str | None:
    """Return the agent name with the highest skill_similarity score, or None.

    ``agents`` should be a list of AgentPersona objects.
    Used as a hint comment in the Director's situation string; actual
    assignment is still made by the LLM.
    """
    best_name: str | None = None
    best_score: float = -1.0
    for agent in agents:
        if agent.name == "Director":
            continue
        score = skill_similarity(task.title, task.description, agent.skills)
        if score > best_score:
            best_score = score
            best_name = agent.name
    return best_name if best_score > 0 else None


class DirectorAgent(AutonomousAgent):
    """The Director observes the project state and autonomously:
    1. Decomposes the user's goal into tasks
    2. Decides what agents are needed (with harness-aware role matching)
    3. Assigns work and monitors progress
    4. Spawns new agents when needed
    5. Declares the project complete
    """

    def __init__(
        self,
        llm_config: LLMConfig | None = None,
        policy: HarnessPolicyContent | None = None,
    ) -> None:
        super().__init__(
            DIRECTOR_PERSONA,
            harness=DIRECTOR_HARNESS,
            llm_config=llm_config,
            policy=policy,
        )
        self._stall_counter = 0

    def _build_situation(self, project: "ProjectState") -> str:
        """Director-enhanced situation report with critical path, skill matching, and overdue tasks."""
        from autonoma.models import TaskStatus as _TS

        # Build base situation from parent
        base = super()._build_situation(project)

        # ── Feature 1: Critical path ──
        critical_path = compute_critical_path(project.tasks)
        task_map = {t.id: t for t in project.tasks}
        cp_titles: list[str] = []
        for cp_id in critical_path[:3]:
            t = task_map.get(cp_id)
            if t:
                cp_titles.append(t.title)
        cp_line = (
            f"Critical path tasks (prioritize these): {', '.join(cp_titles)}"
            if cp_titles else "No dependency chain detected."
        )

        # ── Feature 2: Available agents with skill match scores ──
        open_tasks = [t for t in project.tasks if t.status in (_TS.OPEN, _TS.ASSIGNED)]
        worker_personas = [a for a in project.agents if a.name != "Director"]
        agent_lines: list[str] = []
        for agent in worker_personas:
            # Compute average match across all open tasks
            if open_tasks:
                avg_score = sum(
                    skill_similarity(t.title, t.description, agent.skills)
                    for t in open_tasks
                ) / len(open_tasks)
            else:
                avg_score = 0.0
            agent_lines.append(
                f"  - {agent.name} ({agent.role}): skills={agent.skills}, match={avg_score:.0%}"
            )
        agents_section = "\n".join(agent_lines) if agent_lines else "  None"

        # ── Feature 5: Overdue tasks ──
        current_overdue = overdue_tasks(project.tasks, self._round_number)
        overdue_section = ""
        if current_overdue:
            overdue_titles = ", ".join(t.title for t in current_overdue)
            overdue_section = (
                f"\n⚠️ OVERDUE TASKS (escalate immediately): {overdue_titles}\n"
            )

        director_addendum = f"""
== DIRECTOR INTELLIGENCE ==
{cp_line}
{overdue_section}
AVAILABLE AGENTS (with skill match scores for open tasks):
{agents_section}
"""
        return base + director_addendum

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

        logger.info(
            f"[Director] Decomposing goal: '{project.description[:120]}' "
            f"(model={self._model})"
        )
        try:
            response = await traced_messages_create(
                self.client,
                agent="Director",
                phase="decompose_goal",
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0.2,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
            data = _extract_json(
                response.content[0].text,
                strategy=self.policy.action.json_extraction,
            )
            logger.info(
                f"[Director] LLM plan parsed: tasks={len(data.get('tasks', []))}, "
                f"agents_needed={len(data.get('agents_needed', []))}"
            )
        except Exception as e:
            logger.error(
                f"[Director] Goal decomposition failed ({type(e).__name__}: {e}); "
                f"falling back to 1-task/1-agent plan — this is the likely cause "
                f"if the run later ends with 0 completed tasks."
            )
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

        # Create tasks — drop malformed entries (empty title OR empty
        # description) so a partly-garbage LLM plan doesn't spawn tasks
        # no agent can pick up, which causes an instant stall.
        raw_task_entries = data.get("tasks", [])
        kept_entries: list[dict] = []
        tasks: list[Task] = []
        title_to_id: dict[str, str] = {}

        for t_data in raw_task_entries:
            title = str(t_data.get("title", "")).strip()
            description = str(t_data.get("description", "")).strip()
            if not title or not description:
                logger.warning(
                    f"[Director] dropping malformed task from LLM plan "
                    f"(title={title!r}, desc_empty={not description})"
                )
                continue

            try:
                priority = TaskPriority(t_data.get("priority", "medium"))
            except ValueError:
                priority = TaskPriority.MEDIUM

            task = Task(
                title=title,
                description=description,
                priority=priority,
                created_by="Director",
            )
            tasks.append(task)
            kept_entries.append(t_data)
            title_to_id[task.title] = task.id

        # Resolve dependencies by title with validation
        unresolved_deps: list[str] = []
        for task, t_data in zip(tasks, kept_entries):
            for dep_title in t_data.get("depends_on_titles", []):
                dep_id = title_to_id.get(dep_title)
                if dep_id:
                    task.depends_on.append(dep_id)
                else:
                    unresolved_deps.append(f"{task.title} -> {dep_title}")

        if unresolved_deps:
            logger.warning(f"[Director] Unresolved dependencies: {unresolved_deps}")

        if not tasks:
            # Either the LLM gave us no tasks, or every task was
            # malformed and we dropped them all. In both cases agents
            # would be spawned against an empty backlog, triggering the
            # stall detector immediately. Emit a distinct event so
            # telemetry separates "empty plan" from "parse failure".
            logger.warning(
                f"[Director] LLM plan produced 0 usable tasks "
                f"(raw_count={len(raw_task_entries)}); creating fallback "
                f"(single 'Implement project' task, one Builder agent)"
            )
            await bus.emit(
                "director.decompose_empty",
                raw_task_count=len(raw_task_entries),
            )
            tasks = [Task(
                title="Implement project",
                description=project.description,
                priority=TaskPriority.HIGH,
                created_by="Director",
            )]

        project.tasks = tasks

        # Build spawn payloads first (pure data, no I/O), then fire all
        # agent.spawn_requested events concurrently so the DB hydration
        # calls in _on_spawn_request overlap rather than serializing.
        spawn_payloads: list[dict] = []
        for agent_data in data.get("agents_needed", []):
            role = agent_data.get("role", "coder")
            harness = get_harness(role)
            spawn_payloads.append(dict(
                requester="Director",
                name=agent_data.get("name", "Worker"),
                role=role,
                skills=agent_data.get("skills", harness.default_skills),
                emoji=agent_data.get("emoji", harness.emoji),
                color=agent_data.get("color", harness.color),
            ))

        # Ensure at least one worker agent exists
        if not spawn_payloads:
            spawn_payloads.append(dict(
                requester="Director",
                name="Builder",
                role="coder",
                skills=["coding", "testing", "documentation"],
                emoji="🔨",
                color="cyan",
            ))

        agents_spawned = len(spawn_payloads)
        await asyncio.gather(
            *(bus.emit("agent.spawn_requested", **p) for p in spawn_payloads)
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
                model=self._model,
                max_tokens=self._max_tokens,
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

        # Fallback Korean template (used when LLM synthesis fails)
        fallback = (
            f"## 요약\n"
            f"'{project.name}' 프로젝트에서 {len(tasks_done)}개의 태스크를 완료하고 "
            f"{len(files)}개의 파일을 생성했습니다.\n\n"
            f"## 생성된 결과물\n{file_lines}\n"
        )
        if tasks_open:
            fallback += f"\n## 남은 이슈\n미완료 태스크 {len(tasks_open)}개\n"
        return fallback

    async def _check_for_conflicts(self, project: ProjectState) -> None:
        """Detect and resolve conflicts via DebateArena (Feature 20).

        Heuristic: two different agents referenced the same file path in
        messages sent during the same round. When such a conflict is found,
        DebateArena resolves it and the disputed task is assigned to the winner.
        """
        from autonoma.world import DebateArena

        # DebateArena lives on the swarm; pull it from the swarm if we can
        # reach it, otherwise build a local one (unit-test / standalone paths).
        arena: DebateArena | None = getattr(self, "_swarm_debate_arena", None)
        if arena is None:
            arena = DebateArena()

        relationships = getattr(self, "_swarm_relationships", None)

        # Collect file paths mentioned in the most recent messages.
        import re as _re
        path_pattern = _re.compile(r"[\w./\-]+\.[a-zA-Z]{1,6}")

        # Scan wider than 30 messages so conflicts separated by a few rounds
        # don't slip through. Budget grows with team size (agents chatter
        # more in bigger teams) and is capped so DebateArena's scan stays
        # bounded on very long runs.
        worker_count = max(1, sum(1 for a in project.agents if a.name != "Director"))
        window_size = min(200, max(60, worker_count * 20))
        file_to_senders: dict[str, list[str]] = {}
        for msg in project.messages[-window_size:]:
            sender = msg.sender
            if sender == "Director":
                continue
            for match in path_pattern.finditer(msg.content):
                path = match.group()
                file_to_senders.setdefault(path, [])
                if sender not in file_to_senders[path]:
                    file_to_senders[path].append(sender)

        # A conflict exists when 2+ different agents mention the same path.
        for file_path, senders in file_to_senders.items():
            if len(senders) < 2:
                continue

            proposer, opponent = senders[0], senders[1]
            audience = senders[2:]

            logger.info(
                f"[Director] Conflict detected over '{file_path}': "
                f"proposer={proposer}, opponent={opponent}"
            )

            await bus.emit(
                "debate.started",
                participants=[proposer, opponent],
                topic=file_path,
                round=self._round_number,
            )

            debate = arena.start_debate(
                topic=f"Who should implement {file_path}?",
                proposer=proposer,
                opponent=opponent,
                audience=audience,
                round_number=self._round_number,
            )

            # Resolve by trust score from RelationshipGraph if available.
            winner: str | None = None
            if relationships is not None:
                try:
                    p_trust = relationships.get(proposer, "Director").trust
                    o_trust = relationships.get(opponent, "Director").trust
                    if p_trust >= o_trust:
                        debate.votes[proposer] = "proposer"
                        winner = proposer
                    else:
                        debate.votes[opponent] = "opponent"
                        winner = opponent
                except Exception:
                    logger.warning(
                        "[Director] trust-based debate resolution failed; "
                        "falling back to random (proposer=%s opponent=%s)",
                        proposer, opponent, exc_info=True,
                    )

            if winner is None:
                import random as _random
                winner = _random.choice([proposer, opponent])
                debate.votes[winner] = "proposer" if winner == proposer else "opponent"

            outcome = debate.resolve()
            logger.info(
                f"[Director] Debate resolved: winner={winner}, outcome={outcome.value}"
            )

            await bus.emit(
                "debate.resolved",
                participants=[proposer, opponent],
                topic=file_path,
                winner=winner,
                outcome=outcome.value,
                round=self._round_number,
            )

            # Assign the disputed task to the winner if it's still open.
            for task in project.tasks:
                if (
                    file_path in task.description
                    and task.assigned_to in (proposer, opponent)
                    and task.assigned_to != winner
                ):
                    task.assigned_to = winner
                    logger.info(
                        f"[Director] Disputed task '{task.title}' "
                        f"reassigned to winner={winner}"
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

        # ── Feature 20: Conflict detection via DebateArena ──
        await self._check_for_conflicts(project)

        # ── Feature 1: Critical path computation ──
        critical_path = compute_critical_path(project.tasks)

        # ── Feature 5: Overdue task detection ──
        current_overdue = overdue_tasks(project.tasks, self._round_number)
        if current_overdue:
            overdue_titles = ", ".join(t.title for t in current_overdue)
            logger.warning(
                f"[Director] Overdue tasks at round {self._round_number}: {overdue_titles}"
            )

        # Assign unassigned tasks to available agents (respecting dependencies).
        # Hold the tasks lock across the read-select + write so agents running
        # concurrently in _action_work can't race us into the same OPEN task.
        # We capture an immutable snapshot (title/description/priority as
        # plain strings) inside the lock so when we emit events outside, we
        # can't observe the task object mid-mutation.
        pending_assignments: list[dict[str, Any]] = []
        # Stall-detection counters must be captured inside the lock — if
        # we read ``done/in_progress/blocked/in_review`` after releasing,
        # a concurrent ``_action_work`` can flip a task's status between
        # the assignment write and the count read, producing bogus stall
        # triggers or missed stalls.
        stall_snapshot: dict[str, int] = {}
        async with get_tasks_lock():
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

            for task, agent_name in zip(open_tasks, available_agents):
                # Dual-write, inside the lock, so status and assigned_to stay
                # consistent and no other agent can steal this OPEN task.
                task.assigned_to = agent_name
                task.status = TaskStatus.ASSIGNED
                pending_assignments.append({
                    "task_id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "priority": task.priority.value,
                    "agent_name": agent_name,
                })

            # Snapshot the status tallies while still holding the lock so
            # the stall check below operates on a coherent view.
            stall_snapshot["done"] = sum(
                1 for t in project.tasks if t.status == TaskStatus.DONE
            )
            stall_snapshot["total"] = len(project.tasks)
            stall_snapshot["in_progress"] = sum(
                1 for t in project.tasks if t.status == TaskStatus.IN_PROGRESS
            )
            stall_snapshot["blocked"] = sum(
                1 for t in project.tasks if t.status == TaskStatus.BLOCKED
            )
            stall_snapshot["in_review"] = sum(
                1 for t in project.tasks if t.status == TaskStatus.REVIEW
            )

        assigned_count = 0
        for snap in pending_assignments:
            agent_name = snap["agent_name"]
            await self._say(
                f"{agent_name}, handle '{snap['title']}'!", style="bold"
            )

            # Feature 2: compute skill match score for the assignment message
            agent_persona = next((a for a in project.agents if a.name == agent_name), None)
            agent_skills = agent_persona.skills if agent_persona else []
            match_score = skill_similarity(
                snap["title"], snap["description"], agent_skills
            )

            msg = AgentMessage(
                sender="Director",
                recipient=agent_name,
                msg_type=MessageType.TASK_ASSIGN,
                content=(
                    f"Task: {snap['title']}\n{snap['description']}\n"
                    f"Priority: {snap['priority']}\n"
                    f"Skill match: {match_score:.0%}"
                ),
                data={"task_id": snap["task_id"]},
            )
            project.messages.append(msg)

            await bus.emit(
                "task.assigned",
                agent="Director",
                assigned_to=agent_name,
                task_id=snap["task_id"],
                title=snap["title"],
            )
            assigned_count += 1

        # Detect stalls — use the snapshot captured inside the lock above.
        done = stall_snapshot["done"]
        total = stall_snapshot["total"]
        in_progress = stall_snapshot["in_progress"]
        blocked = stall_snapshot["blocked"]
        in_review = stall_snapshot["in_review"]

        # REVIEW→DONE transition: without a dedicated reviewer loop, tasks
        # that reach REVIEW can stick forever. After the stall threshold we
        # auto-approve any REVIEW tasks so the project can finish.
        review_stuck = bool(in_review) and assigned_count == 0 and in_progress == 0

        # Feature 5: overdue tasks trigger escalation the same as stalls
        has_overdue = bool(current_overdue)

        if (
            assigned_count == 0
            and in_progress == 0
            and done < total
        ) or review_stuck or has_overdue:
            self._stall_counter += 1
            overdue_note = (
                f", overdue={len(current_overdue)}" if current_overdue else ""
            )
            logger.warning(
                f"[Director] Stall detected (counter={self._stall_counter}/3): "
                f"done={done}/{total}, in_progress=0, assigned_this_round=0, "
                f"available_agents={len(available_agents)}, "
                f"blocked={blocked}, review={in_review}, "
                f"open_ready={len(open_tasks)}{overdue_note}"
            )
            if self._stall_counter >= 3:
                review_tasks = [t for t in project.tasks if t.status == TaskStatus.REVIEW]
                open_candidates = [t for t in project.tasks if t.status == TaskStatus.OPEN]
                # Pass full status map so stall strategies can respect
                # the critical path (don't clear deps pointing to live
                # upstream work).
                task_status_map = {t.id: t.status for t in project.tasks}
                plan_fn = _strategy_lookup(
                    "loop.stall_policy", self.policy.loop.stall_policy
                )
                plan = plan_fn(
                    review_tasks,
                    open_candidates,
                    len(available_agents),
                    task_status_map=task_status_map,
                )
                action = plan.get("action", "none")

                if action == "approve_reviews":
                    targets = plan.get("tasks", [])
                    did_reset = False
                    async with get_tasks_lock():
                        for rt in targets:
                            logger.warning(
                                f"[Director] Auto-approving stalled REVIEW task "
                                f"'{rt.title}' (assigned_to={rt.assigned_to or 'none'}) "
                                f"-> DONE (no reviewer agent is consuming REVIEW queue)"
                            )
                            rt.status = TaskStatus.DONE
                        # Reset inside the lock so partial failures leave the
                        # counter intact for re-evaluation next round.
                        did_reset = True
                    await self._say("Auto-approving stuck reviews!", style="bold red")
                    await bus.emit("director.review_auto_approved", count=len(targets))
                    if did_reset:
                        self._stall_counter = 0

                elif action == "clear_deps":
                    target = plan["task"]
                    cleared = plan.get("cleared", [])
                    did_reset = False
                    async with get_tasks_lock():
                        # Only drop the explicit subset the strategy
                        # flagged as safe — live critical-path deps
                        # (IN_PROGRESS/ASSIGNED upstream) must stay.
                        if cleared:
                            to_remove = set(cleared)
                            target.depends_on = [
                                d for d in target.depends_on if d not in to_remove
                            ]
                        did_reset = True
                    logger.warning(
                        f"[Director] Unblocking '{target.title}': "
                        f"cleared stale deps={cleared}, "
                        f"remaining live deps={target.depends_on}"
                    )
                    await self._say("Unblocking stuck task!", style="bold red")
                    if did_reset:
                        self._stall_counter = 0

                elif action == "escalate":
                    logger.warning(
                        f"[Director] stall escalation: {plan.get('message', '')}"
                    )
                    await self._say("Stall detected — awaiting intervention.", style="bold red")
                    await bus.emit(
                        "director.stall_escalated",
                        message=plan.get("message", ""),
                    )
                    # Counter intentionally NOT reset: the next round will
                    # re-evaluate and may escalate again.

                elif action == "none":
                    # "wait" strategy: note it and keep spinning.
                    logger.info(
                        f"[Director] stall noted (policy=wait); counter "
                        f"stays at {self._stall_counter}"
                    )
        else:
            self._stall_counter = 0

        if total > 0:
            overdue_suffix = f", {len(current_overdue)} overdue" if current_overdue else ""
            await self._say(
                f"Progress: {done}/{total} done, {in_progress} active{overdue_suffix}",
                style="bold yellow",
            )

        # Build enriched situation notes for the LLM (Features 1, 2, 5)
        situation_notes: list[str] = []

        # Feature 1: critical path hint
        if critical_path:
            cp_titles = []
            task_map = {t.id: t for t in project.tasks}
            for cp_id in critical_path[:3]:
                t = task_map.get(cp_id)
                if t:
                    cp_titles.append(t.title)
            if cp_titles:
                situation_notes.append(
                    f"Critical path tasks (prioritize these): {', '.join(cp_titles)}"
                )

        # Feature 5: overdue task warning
        if current_overdue:
            overdue_titles = ", ".join(t.title for t in current_overdue)
            situation_notes.append(
                f"⚠️ OVERDUE TASKS (escalate immediately): {overdue_titles}"
            )

        # Feature 2: best agent hints for open tasks
        worker_personas = [a for a in project.agents if a.name != "Director"]
        for t in open_tasks[:3]:
            best = find_best_agent_for_task(t, worker_personas)
            if best:
                situation_notes.append(
                    f"Best agent for '{t.title}': {best} (skill match)"
                )

        if situation_notes:
            notes_text = "\n".join(f"  {note}" for note in situation_notes)
            logger.debug(f"[Director] Situation notes:\n{notes_text}")

        return {
            "agent": "Director",
            "action": "manage",
            "done": done,
            "total": total,
            "assigned": assigned_count,
            "critical_path": critical_path[:3],
            "overdue_count": len(current_overdue),
            "situation_notes": situation_notes,
        }
