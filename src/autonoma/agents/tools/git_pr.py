"""GitHub PR tool — feature #9.

Allows a coder agent to commit its working changes and open a pull
request as itself. Kept as a thin wrapper around ``gh``-cli so we
don't own a GitHub OAuth app in this repo:

  * Operator installs ``gh`` in the container.
  * Each agent may have a dedicated bot account; its ``GH_TOKEN`` is
    read from ``AUTONOMA_AGENT_GH_TOKEN_{AGENT_UPPER}`` env var.
    Falling back to ``GH_TOKEN`` shared across the swarm.
  * The commit trailer includes ``Co-Authored-By: {agent_name} <bot@autonoma>``
    so GitHub's contributor graph visibly shows which agent authored
    what.

Security: refuses to run outside the configured ``settings.output_dir``
subtree, so an agent can't push user home directory files.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from autonoma.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GitPRResult:
    ok: bool
    url: str = ""
    reason: str = ""


def _gh_token_for(agent_name: str) -> str | None:
    env_key = f"AUTONOMA_AGENT_GH_TOKEN_{agent_name.upper().replace(' ', '_')}"
    return os.environ.get(env_key) or os.environ.get("GH_TOKEN")


def _is_inside_workspace(path: Path) -> bool:
    """Reject paths outside the configured output dir."""
    try:
        path.resolve().relative_to(Path(settings.output_dir).resolve())
        return True
    except ValueError:
        return False


def open_pull_request(
    *,
    agent_name: str,
    repo_path: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> GitPRResult:
    """Commit any uncommitted changes in ``repo_path`` as ``agent_name``,
    push the branch, and open a PR. Returns the PR URL or a reason.
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        return GitPRResult(ok=False, reason=f"repo_path not a directory: {repo}")
    if not _is_inside_workspace(repo):
        return GitPRResult(ok=False, reason=f"repo_path escapes workspace: {repo}")
    if shutil.which("gh") is None:
        return GitPRResult(ok=False, reason="gh CLI not installed in this container")

    token = _gh_token_for(agent_name)
    if not token:
        return GitPRResult(ok=False, reason="no GH_TOKEN available for this agent")

    env = {**os.environ, "GH_TOKEN": token}
    try:
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-b", branch],
            check=True, capture_output=True, text=True, env=env,
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"],
            check=True, capture_output=True, text=True, env=env,
        )
        commit_msg = (
            f"{title}\n\nCo-Authored-By: {agent_name} "
            f"<{agent_name.lower().replace(' ', '-')}@autonoma-bot>\n"
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", commit_msg, "--allow-empty"],
            check=True, capture_output=True, text=True, env=env,
        )
        subprocess.run(
            ["git", "-C", str(repo), "push", "-u", "origin", branch],
            check=True, capture_output=True, text=True, env=env,
        )
        pr = subprocess.run(
            ["gh", "pr", "create",
             "--title", title,
             "--body", body,
             "--base", base,
             "--head", branch],
            check=True, capture_output=True, text=True,
            cwd=str(repo), env=env,
        )
        url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""
        return GitPRResult(ok=True, url=url)
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or exc.stdout or str(exc)).strip()[:500]
        logger.warning("[git_pr] failed for %s: %s", agent_name, reason)
        return GitPRResult(ok=False, reason=reason)


__all__ = ["GitPRResult", "open_pull_request"]
