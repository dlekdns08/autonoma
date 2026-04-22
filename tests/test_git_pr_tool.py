"""Unit tests for the git_pr tool.

We don't exercise the real ``gh`` CLI (the test container has no
network + no bot token). Instead we verify the preconditions + token
resolution, which is where real bugs have historically been:

* refuses to run outside ``settings.output_dir``
* picks the per-agent token over the shared ``GH_TOKEN``
* returns a structured ``reason`` instead of raising when ``gh`` is
  missing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autonoma.agents.tools.git_pr import (
    _gh_token_for,
    _is_inside_workspace,
    open_pull_request,
)


def test_token_file_fallback_for_github_app_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Option C (GitHub App sidecar): no env GH_TOKEN, read from file."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("AUTONOMA_AGENT_GH_TOKEN_ALICE", raising=False)
    env_file = tmp_path / "gh.env"
    env_file.write_text(
        "# auto-generated\n"
        "# expires_at=2026-04-22T10:00:00Z\n"
        "GH_TOKEN=ghs_installationtoken\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_TOKEN_FILE", str(env_file))
    assert _gh_token_for("Alice") == "ghs_installationtoken"


def test_per_agent_git_identity_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Option A: per-agent GIT_AUTHOR_* env overlay."""
    from autonoma.agents.tools.git_pr import _git_author_env_for

    monkeypatch.setenv("AUTONOMA_AGENT_GH_NAME_MIDORI", "midori-bot")
    monkeypatch.setenv("AUTONOMA_AGENT_GH_EMAIL_MIDORI", "1+midori@users.noreply.github.com")
    overlay = _git_author_env_for("Midori")
    assert overlay["GIT_AUTHOR_NAME"] == "midori-bot"
    assert overlay["GIT_COMMITTER_NAME"] == "midori-bot"
    assert overlay["GIT_AUTHOR_EMAIL"].endswith("@users.noreply.github.com")
    assert overlay["GIT_COMMITTER_EMAIL"] == overlay["GIT_AUTHOR_EMAIL"]


def test_git_identity_overlay_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Options B/C/D: no per-agent vars → empty overlay (defers to
    container's global git config)."""
    from autonoma.agents.tools.git_pr import _git_author_env_for

    monkeypatch.delenv("AUTONOMA_AGENT_GH_NAME_BEAR", raising=False)
    monkeypatch.delenv("AUTONOMA_AGENT_GH_EMAIL_BEAR", raising=False)
    assert _git_author_env_for("Bear") == {}


def test_per_agent_token_beats_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "shared-fallback")
    monkeypatch.setenv("AUTONOMA_AGENT_GH_TOKEN_MIDORI", "midori-specific")
    assert _gh_token_for("Midori") == "midori-specific"


def test_shared_token_used_when_no_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTONOMA_AGENT_GH_TOKEN_BEAR", raising=False)
    monkeypatch.setenv("GH_TOKEN", "shared-fallback")
    assert _gh_token_for("Bear") == "shared-fallback"


def test_no_token_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTONOMA_AGENT_GH_TOKEN_ALICE", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert _gh_token_for("Alice") is None


def test_workspace_guard_rejects_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "output_dir", tmp_path / "out")
    (tmp_path / "out").mkdir()
    (tmp_path / "outside").mkdir()
    assert _is_inside_workspace(tmp_path / "out" / "repo") is True
    assert _is_inside_workspace(tmp_path / "outside" / "repo") is False


def test_missing_repo_returns_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.config import settings
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    monkeypatch.setenv("GH_TOKEN", "x")
    res = open_pull_request(
        agent_name="Alice",
        repo_path=str(tmp_path / "does-not-exist"),
        branch="feature/x",
        title="test",
        body="b",
    )
    assert res.ok is False
    assert "not a directory" in res.reason


def test_missing_token_returns_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.config import settings
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("AUTONOMA_AGENT_GH_TOKEN_ALICE", raising=False)
    res = open_pull_request(
        agent_name="Alice",
        repo_path=str(repo),
        branch="feature/x",
        title="test",
        body="b",
    )
    assert res.ok is False
    # Either "no GH_TOKEN" or "gh CLI not installed" — both are acceptable
    # pre-flight failures. The test env usually lacks both.
    assert res.reason
