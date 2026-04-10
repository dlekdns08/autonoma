"""Progress tracking for cross-session continuity.

Ported from the Initializer/Coding Agent pattern described in Anthropic's
"Effective Harnesses for Long-Running Agents" blog post.

Key pattern: git history + a progress file let agents reconstruct project state
when starting a new context window — mirroring how real engineers work.

This module provides:
1. A progress file (JSON) tracking feature status across sessions
2. Session logging with timestamps for audit trail
3. State reconstruction from progress file for new sessions
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from autonoma.models import ProjectState, TaskStatus

logger = logging.getLogger(__name__)

PROGRESS_FILENAME = "autonoma-progress.json"


class ProgressTracker:
    """Tracks project progress across sessions via a JSON file.

    The progress file serves as the agent's "memory" between sessions,
    similar to how claude-progress.txt works in the blog post pattern.
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.progress_file = project_dir / PROGRESS_FILENAME
        self._data: dict[str, Any] = {}

    def save(self, project: ProjectState) -> None:
        """Save current project state to progress file."""
        self.project_dir.mkdir(parents=True, exist_ok=True)

        tasks_data = []
        for task in project.tasks:
            tasks_data.append({
                "id": task.id,
                "title": task.title,
                "description": task.description[:200],
                "status": task.status.value,
                "assigned_to": task.assigned_to,
                "priority": task.priority.value,
                "artifacts": task.artifacts,
                "output": task.output[:200] if task.output else "",
            })

        files_data = [
            {"path": f.path, "created_by": f.created_by, "description": f.description}
            for f in project.files
        ]

        agents_data = [
            {"name": a.name, "emoji": a.emoji, "role": a.role, "skills": a.skills}
            for a in project.agents
        ]

        done = sum(1 for t in project.tasks if t.status == TaskStatus.DONE)
        total = len(project.tasks)

        self._data = {
            "project_name": project.name,
            "description": project.description,
            "progress": {
                "done": done,
                "total": total,
                "percentage": round(done / total * 100, 1) if total else 0,
                "completed": project.completed,
            },
            "tasks": tasks_data,
            "files": files_data,
            "agents": agents_data,
            "sessions": self._data.get("sessions", []) + [
                {
                    "timestamp": datetime.now().isoformat(),
                    "tasks_completed": done,
                    "tasks_total": total,
                    "files_created": len(project.files),
                    "agents_used": len(project.agents),
                }
            ],
            "last_updated": datetime.now().isoformat(),
        }

        self.progress_file.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        logger.info(f"[Progress] Saved to {self.progress_file} ({done}/{total} done)")

    def load(self) -> dict[str, Any] | None:
        """Load progress from a previous session."""
        if not self.progress_file.exists():
            return None

        try:
            self._data = json.loads(self.progress_file.read_text())
            logger.info(
                f"[Progress] Loaded from {self.progress_file}: "
                f"{self._data.get('progress', {}).get('done', 0)}/"
                f"{self._data.get('progress', {}).get('total', 0)} done"
            )
            return self._data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Progress] Failed to load progress file: {e}")
            return None

    def get_summary(self) -> str:
        """Generate a human-readable progress summary for context reconstruction.

        This is injected into the agent's situation report when resuming,
        so it can pick up where it left off without re-reading everything.
        """
        if not self._data:
            return "No previous session data."

        progress = self._data.get("progress", {})
        tasks = self._data.get("tasks", [])
        sessions = self._data.get("sessions", [])

        lines = [
            f"== PREVIOUS SESSION SUMMARY ==",
            f"Project: {self._data.get('project_name', 'unknown')}",
            f"Progress: {progress.get('done', 0)}/{progress.get('total', 0)} tasks done ({progress.get('percentage', 0)}%)",
            f"Sessions so far: {len(sessions)}",
            "",
        ]

        # What's done
        done_tasks = [t for t in tasks if t.get("status") == "done"]
        if done_tasks:
            lines.append("COMPLETED:")
            for t in done_tasks[:10]:
                lines.append(f"  [DONE] {t['title']}")

        # What's pending
        pending_tasks = [t for t in tasks if t.get("status") not in ("done",)]
        if pending_tasks:
            lines.append("\nREMAINING:")
            for t in pending_tasks[:10]:
                lines.append(f"  [{t.get('status', '?').upper()}] {t['title']} (assigned: {t.get('assigned_to', '-')})")

        # Files created
        files = self._data.get("files", [])
        if files:
            lines.append(f"\nFILES CREATED ({len(files)}):")
            for f in files[:15]:
                lines.append(f"  {f['path']} (by {f.get('created_by', '?')})")

        return "\n".join(lines)
