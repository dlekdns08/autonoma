"""Core data models for the autonomous agent swarm."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Agent Identity ─────────────────────────────────────────────────────────

class AgentPersona(BaseModel):
    """Dynamic persona - agents define their own role."""
    name: str
    emoji: str = "🤖"
    role: str = ""  # Self-assigned role description
    skills: list[str] = Field(default_factory=list)
    color: str = "cyan"  # Rich color for TUI


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    TALKING = "talking"
    MOVING = "moving"
    CELEBRATING = "celebrating"
    ERROR = "error"
    SPAWNING = "spawning"


# ── Task System ────────────────────────────────────────────────────────────

class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    description: str
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.OPEN
    assigned_to: str | None = None
    created_by: str = "director"
    depends_on: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)  # file paths produced
    output: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    deadline_round: int | None = None  # Feature 5: soft deadline

    def is_overdue(self, current_round: int) -> bool:
        """Return True if deadline_round is set and current_round exceeds it."""
        return self.deadline_round is not None and current_round > self.deadline_round


# ── Task Graph Helpers (Feature 1) ────────────────────────────────────────

def get_dependency_graph(tasks: list[Task]) -> dict[str, list[str]]:
    """Return a reverse-dependency graph: {task_id: [ids of tasks that depend on it]}.

    Useful for understanding which tasks are downstream of a given task.
    """
    graph: dict[str, list[str]] = {t.id: [] for t in tasks}
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id in graph:
                graph[dep_id].append(task.id)
    return graph


def task_depth(task_id: str, tasks: list[Task]) -> int:
    """Return how deep a task is in the dependency tree.

    A task with no dependencies has depth 0. A task whose dependencies
    are all at depth N has depth N+1.
    """
    task_map = {t.id: t for t in tasks}

    def _depth(tid: str, visited: set[str]) -> int:
        if tid in visited:
            return 0  # Cycle guard
        task = task_map.get(tid)
        if task is None or not task.depends_on:
            return 0
        visited.add(tid)
        return 1 + max(_depth(dep, visited) for dep in task.depends_on)

    return _depth(task_id, set())


def compute_critical_path(tasks: list[Task]) -> list[str]:
    """Return the ordered task IDs on the longest dependency chain (critical path).

    Uses topological sort + longest path via dynamic programming.
    Returns IDs ordered from root (no deps) to leaf (deepest dependent).
    """
    if not tasks:
        return []

    task_map = {t.id: t for t in tasks}
    # Build adjacency: id -> list of ids that depend on it (forward edges)
    dependents: dict[str, list[str]] = {t.id: [] for t in tasks}
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}

    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id in dependents:
                dependents[dep_id].append(task.id)
                in_degree[task.id] = in_degree.get(task.id, 0) + 1

    # Kahn's algorithm for topological sort
    from collections import deque
    queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
    topo_order: list[str] = []
    while queue:
        node = queue.popleft()
        topo_order.append(node)
        for neighbor in dependents.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Longest path via DP in topological order
    dist: dict[str, int] = {tid: 0 for tid in task_map}
    parent: dict[str, str | None] = {tid: None for tid in task_map}

    for tid in topo_order:
        for neighbor in dependents.get(tid, []):
            if dist[tid] + 1 > dist[neighbor]:
                dist[neighbor] = dist[tid] + 1
                parent[neighbor] = tid

    # Find the end of the longest path
    if not dist:
        return []
    end_node = max(dist, key=lambda tid: dist[tid])

    # Reconstruct path by walking parent pointers
    path: list[str] = []
    current: str | None = end_node
    while current is not None:
        path.append(current)
        current = parent[current]
    path.reverse()
    return path


# ── Task Deadline Helpers (Feature 5) ─────────────────────────────────────

def overdue_tasks(tasks: list[Task], current_round: int) -> list[Task]:
    """Return IN_PROGRESS or ASSIGNED tasks that are past their deadline."""
    active_statuses = {TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED}
    return [
        t for t in tasks
        if t.status in active_statuses and t.is_overdue(current_round)
    ]


# ── Communication ──────────────────────────────────────────────────────────

class MessageType(str, Enum):
    CHAT = "chat"
    TASK_ASSIGN = "task_assign"
    TASK_COMPLETE = "task_complete"
    TASK_BLOCKED = "task_blocked"
    HELP_REQUEST = "help_request"
    REVIEW_REQUEST = "review_request"
    SPAWN_REQUEST = "spawn_request"
    NEGOTIATE = "negotiate"


class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    sender: str
    recipient: str  # agent name or "all" for broadcast
    msg_type: MessageType
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


# ── TUI Position ───────────────────────────────────────────────────────────

class Position(BaseModel):
    x: int = 0
    y: int = 0

    def move_toward(self, target: Position, speed: int = 1) -> Position:
        dx = max(-speed, min(speed, target.x - self.x))
        dy = max(-speed, min(speed, target.y - self.y))
        return Position(x=self.x + dx, y=self.y + dy)

    def distance_to(self, other: Position) -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


class SpeechBubble(BaseModel):
    text: str
    style: str = "dim"  # Rich style
    ttl: int = 12  # ticks to live


# ── Workspace Artifacts ────────────────────────────────────────────────────

class FileArtifact(BaseModel):
    path: str
    content: str
    created_by: str
    description: str = ""


class ProjectState(BaseModel):
    """The evolving state of the project being built."""
    name: str = ""
    description: str = ""
    tasks: list[Task] = Field(default_factory=list)
    files: list[FileArtifact] = Field(default_factory=list)
    agents: list[AgentPersona] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    completed: bool = False
    final_answer: str = ""

    def to_json(self) -> str:
        """Serialize the project state to a JSON string.

        Uses Pydantic's ``model_dump`` with ``mode="json"`` so all nested
        models (datetime, enum, etc.) are reduced to JSON-safe primitives
        before the ``json.dumps`` call.
        """
        import json as _json
        return _json.dumps(self.model_dump(mode="json"))

    @classmethod
    def from_json(cls, json_str: str) -> "ProjectState":
        """Deserialize a JSON string produced by ``to_json`` back into a
        ``ProjectState`` instance. Raises ``ValueError`` on parse failure."""
        import json as _json
        try:
            data = _json.loads(json_str)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"Invalid ProjectState JSON: {exc}") from exc
        return cls.model_validate(data)
