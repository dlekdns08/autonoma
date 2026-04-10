"""Tests for the progress tracking system."""

import json

import pytest

from autonoma.models import (
    FileArtifact,
    ProjectState,
    Task,
    TaskPriority,
    TaskStatus,
    AgentPersona,
)
from autonoma.progress import ProgressTracker


def test_save_and_load(tmp_path):
    project = ProjectState(name="test-project", description="A test")
    project.tasks = [
        Task(title="Task 1", description="Do thing 1", priority=TaskPriority.HIGH, status=TaskStatus.DONE),
        Task(title="Task 2", description="Do thing 2", priority=TaskPriority.MEDIUM, status=TaskStatus.IN_PROGRESS),
    ]
    project.files = [FileArtifact(path="main.py", content="print('hi')", created_by="Coder")]
    project.agents = [AgentPersona(name="Director", emoji="👑", role="director", skills=["planning"])]

    tracker = ProgressTracker(tmp_path)
    tracker.save(project)

    # Verify file exists
    progress_file = tmp_path / "autonoma-progress.json"
    assert progress_file.exists()

    # Load and verify
    data = tracker.load()
    assert data is not None
    assert data["project_name"] == "test-project"
    assert data["progress"]["done"] == 1
    assert data["progress"]["total"] == 2
    assert data["progress"]["percentage"] == 50.0


def test_load_nonexistent(tmp_path):
    tracker = ProgressTracker(tmp_path)
    assert tracker.load() is None


def test_summary(tmp_path):
    project = ProjectState(name="test", description="test")
    project.tasks = [
        Task(title="Done task", description="done", status=TaskStatus.DONE),
        Task(title="Open task", description="open", status=TaskStatus.OPEN),
    ]

    tracker = ProgressTracker(tmp_path)
    tracker.save(project)
    tracker.load()

    summary = tracker.get_summary()
    assert "Done task" in summary
    assert "Open task" in summary
    assert "COMPLETED" in summary
    assert "REMAINING" in summary


def test_session_accumulation(tmp_path):
    project = ProjectState(name="test", description="test")
    project.tasks = [Task(title="T1", description="d1")]

    tracker = ProgressTracker(tmp_path)
    tracker.save(project)
    tracker.save(project)  # Second session

    data = tracker.load()
    assert len(data["sessions"]) == 2
