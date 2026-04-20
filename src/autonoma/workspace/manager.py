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
        project_root = project_dir.resolve()

        # Dedup by path: if two agents produced the same path this round, the
        # LAST artifact is the most recent write and should win. We do this on
        # an in-memory copy so we don't mutate project.files (owned by the
        # model layer).
        dedup: dict[str, FileArtifact] = {}
        for artifact in project.files:
            dedup[artifact.path] = artifact
        collapsed = len(project.files) - len(dedup)
        if collapsed > 0:
            logger.info(
                f"[Workspace] Collapsed {collapsed} duplicate file artifact(s) "
                f"(kept most recent per path)"
            )
        artifacts = list(dedup.values())

        written: list[str] = []
        skipped: list[str] = []
        for artifact in artifacts:
            raw_path = artifact.path
            # Robust path traversal defense:
            # 1) no backslashes or null bytes
            # 2) no absolute paths (POSIX leading slash or Windows drive)
            # 3) resolved path must stay inside project_dir
            if "\x00" in raw_path or "\\" in raw_path:
                logger.warning(
                    f"[Workspace] Skipping unsafe path (null byte or backslash): {raw_path!r}"
                )
                skipped.append(raw_path)
                continue

            safe_path = raw_path
            # Reject absolute POSIX paths
            if safe_path.startswith("/"):
                logger.warning(
                    f"[Workspace] Skipping absolute path: {raw_path!r}"
                )
                skipped.append(raw_path)
                continue
            # Reject Windows-style absolute paths (e.g. "C:/foo")
            if len(safe_path) >= 2 and safe_path[1] == ":" and safe_path[0].isalpha():
                logger.warning(
                    f"[Workspace] Skipping absolute (drive-letter) path: {raw_path!r}"
                )
                skipped.append(raw_path)
                continue

            try:
                resolved = (project_dir / safe_path).resolve()
            except (OSError, RuntimeError) as e:
                logger.warning(
                    f"[Workspace] Skipping path that failed to resolve ({raw_path!r}): {e}"
                )
                skipped.append(raw_path)
                continue

            if not resolved.is_relative_to(project_root):
                logger.warning(
                    f"[Workspace] Skipping path traversal attempt: {raw_path!r}"
                )
                skipped.append(raw_path)
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
