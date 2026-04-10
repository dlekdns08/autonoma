"""Tests for workspace manager."""

import tempfile
from pathlib import Path

import pytest

from autonoma.models import FileArtifact, ProjectState
from autonoma.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield WorkspaceManager(base_dir=Path(tmpdir))


@pytest.fixture
def project():
    return ProjectState(name="test-project", description="A test")


@pytest.mark.asyncio
async def test_write_all(workspace, project):
    project.files = [
        FileArtifact(path="src/main.py", content="print('hi')", created_by="Coder"),
        FileArtifact(path="README.md", content="# Test", created_by="Writer"),
    ]
    result = await workspace.write_all(project)
    assert len(result["files"]) == 2
    for f in result["files"]:
        assert Path(f).exists()


@pytest.mark.asyncio
async def test_write_nested_dirs(workspace, project):
    project.files = [
        FileArtifact(path="src/deep/nested/file.py", content="# deep", created_by="X"),
    ]
    result = await workspace.write_all(project)
    assert len(result["files"]) == 1
    assert Path(result["files"][0]).exists()
