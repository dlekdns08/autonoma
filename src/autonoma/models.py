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
