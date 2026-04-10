"""Tests for core models."""

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
    TaskPriority,
    TaskStatus,
)


def test_position_move_toward():
    p = Position(x=0, y=0)
    target = Position(x=10, y=10)
    moved = p.move_toward(target, speed=2)
    assert moved.x == 2
    assert moved.y == 2


def test_position_distance():
    a = Position(x=0, y=0)
    b = Position(x=3, y=4)
    assert a.distance_to(b) == 5.0


def test_speech_bubble():
    s = SpeechBubble(text="Hello!", ttl=10)
    assert s.ttl == 10
    assert s.text == "Hello!"


def test_task_defaults():
    t = Task(title="Test", description="Do stuff")
    assert t.status == TaskStatus.OPEN
    assert t.priority == TaskPriority.MEDIUM
    assert t.id


def test_agent_persona():
    p = AgentPersona(name="Coder", emoji="⚡", role="writes code", skills=["python"])
    assert p.name == "Coder"
    assert "python" in p.skills


def test_file_artifact():
    f = FileArtifact(path="main.py", content="print('hi')", created_by="Coder")
    assert f.path == "main.py"


def test_project_state():
    ps = ProjectState(name="test", description="A test project")
    assert ps.completed is False
    assert ps.tasks == []
    assert ps.files == []


def test_agent_message():
    msg = AgentMessage(
        sender="A", recipient="B", msg_type=MessageType.CHAT, content="Hey"
    )
    assert msg.sender == "A"
    assert msg.msg_type == MessageType.CHAT
