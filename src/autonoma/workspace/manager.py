"""Workspace manager: writes generated artifacts to disk."""

from __future__ import annotations

import logging
from pathlib import Path

import aiofiles

from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.models import FileArtifact, ProjectState

logger = logging.getLogger(__name__)


class WorkspaceManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or settings.output_dir

    async def write_all(self, project: ProjectState) -> dict[str, list[str]]:
        """Write all project artifacts to disk."""
        project_dir = self.base_dir / project.name
        project_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        skipped: list[str] = []
        for artifact in project.files:
            # Defense-in-depth: validate path stays within project dir
            sanitized = artifact.path.lstrip("/").replace("..", "")
            resolved = (project_dir / sanitized).resolve()
            if not str(resolved).startswith(str(project_dir.resolve())):
                logger.warning(f"[Workspace] Skipping path traversal attempt: {artifact.path}")
                skipped.append(artifact.path)
                continue

            resolved.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(resolved, "w") as f:
                await f.write(artifact.content)
            written.append(str(resolved))

        await bus.emit(
            "workspace.complete",
            total_files=len(written),
            project_dir=str(project_dir),
        )
        if skipped:
            logger.warning(f"[Workspace] Skipped {len(skipped)} unsafe paths")
        return {"files": written, "skipped": skipped}

    def get_tree(self, project: ProjectState) -> str:
        project_dir = self.base_dir / project.name
        if not project_dir.exists():
            return "(not yet written to disk)"
        return _tree(project_dir)


def _tree(path: Path, prefix: str = "", is_last: bool = True) -> str:
    lines: list[str] = []
    connector = "└── " if is_last else "├── "
    lines.append(f"{prefix}{connector}{path.name}")
    if path.is_dir():
        children = sorted(
            [c for c in path.iterdir() if not c.name.startswith(".")],
            key=lambda p: (p.is_file(), p.name),
        )
        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(children):
            lines.append(_tree(child, new_prefix, i == len(children) - 1))
    return "\n".join(lines)
