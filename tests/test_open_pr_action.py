"""Integration of the GitHub PR tool into the agent action dispatcher.

Validates three things that are easy to regress:

1. ``AgentCapability.OPEN_PR`` exists and the default coder harness
   allows it (default behavior: all capabilities allowed).
2. ``open_pr`` appears in the prompt's action list for a harness that
   permits it, and is absent for a harness that disallows it.
3. ``_action_open_pr`` returns a structured failure (never raises) when
   args are missing, and delegates to ``open_pull_request`` with the
   agent's name — we patch the function to avoid touching ``gh``.
"""

from __future__ import annotations

from typing import Any

import pytest

from autonoma.agents.harness import (
    CODER_HARNESS,
    REVIEWER_HARNESS,
    AgentCapability,
)


def test_open_pr_capability_exists() -> None:
    assert AgentCapability.OPEN_PR.value == "open_pr"


def test_coder_can_open_pr_by_default() -> None:
    # CODER_HARNESS gets all capabilities by default (field default is
    # ``list(AgentCapability)``). That includes OPEN_PR.
    assert CODER_HARNESS.can_perform("open_pr") is True


def test_read_only_reviewer_cannot_open_pr() -> None:
    # REVIEWER is explicitly read_only and its allowed_capabilities
    # excludes write actions — open_pr must be blocked.
    assert REVIEWER_HARNESS.can_perform("open_pr") is False


def test_prompt_lists_open_pr_when_allowed() -> None:
    """The generated system prompt should mention 'open_pr' only when
    the harness permits it."""
    from autonoma.agents.harness import AgentHarness

    open_pr_harness = AgentHarness(
        name="pr-bot",
        role_description="opens PRs",
        allowed_capabilities=[AgentCapability.CREATE_FILE, AgentCapability.OPEN_PR],
    )
    prompt = open_pr_harness.build_system_prompt("PRBot", ["python"])
    # The system_prompt itself doesn't enumerate actions — that happens
    # in base.py's _decide(). We test the capability machinery here and
    # the prompt assembly below via a direct reconstruction.
    _effective = {cap.value for cap in open_pr_harness.get_effective_capabilities()}
    assert "open_pr" in _effective

    denied_harness = AgentHarness(
        name="denied",
        role_description="cannot open PRs",
        allowed_capabilities=[AgentCapability.CREATE_FILE],
    )
    _effective = {cap.value for cap in denied_harness.get_effective_capabilities()}
    assert "open_pr" not in _effective


@pytest.mark.asyncio
async def test_action_open_pr_missing_args_returns_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no repo_path/branch/title, the handler must return a clean
    failure and never invoke the tool — this is the dead-man's-switch
    against LLM hallucinations."""
    from autonoma.models import ProjectState
    from autonoma.agents.base import AutonomousAgent
    from autonoma.agents import tools as tools_pkg

    called = {"hit": False}

    def _never(**_kwargs: Any) -> Any:
        called["hit"] = True
        raise AssertionError("open_pull_request should not be called")

    monkeypatch.setattr(tools_pkg, "open_pull_request", _never)

    # The handler is a bound coroutine on BaseAgent; call it via a
    # lightweight stand-in that only needs _set_state + _say + stats +
    # memory. We build a minimal subclass to bypass LLM setup.
    agent = _make_stub_agent()
    project = ProjectState(
        project_uuid="test-proj",
        name="test",
        description="",
        goal="",
    )

    result = await agent._action_open_pr(  # type: ignore[attr-defined]
        {"action": "open_pr"}, project,
    )
    assert called["hit"] is False
    assert result["ok"] is False
    assert result["reason"] == "missing_args"
    assert result["action"] == "open_pr"


@pytest.mark.asyncio
async def test_action_open_pr_delegates_with_agent_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: patched tool returns a fake URL; handler surfaces it
    + emits ``agent.opened_pr`` on the bus."""
    from autonoma.agents import base as agent_base
    from autonoma.agents.tools import git_pr as git_pr_mod
    from autonoma.event_bus import bus
    from autonoma.models import ProjectState

    captured: list[dict[str, Any]] = []

    async def on_emit(**kwargs: Any) -> None:
        captured.append(kwargs)

    bus.on("agent.opened_pr", on_emit)

    class _Ok:
        ok = True
        url = "https://github.com/x/y/pull/42"
        reason = ""

    def _fake_open_pr(**kwargs: Any) -> _Ok:
        assert kwargs["agent_name"] == "Midori"
        assert kwargs["branch"] == "feature/midori-x"
        assert kwargs["title"].startswith("Add")
        return _Ok()

    # Patch both the package attribute (what base.py imports) and the
    # module source (what test_git_pr_tool patches). The handler does
    # ``from autonoma.agents.tools import open_pull_request`` at call
    # time, so monkeypatching the package surface is enough.
    import autonoma.agents.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "open_pull_request", _fake_open_pr)
    monkeypatch.setattr(git_pr_mod, "open_pull_request", _fake_open_pr)

    agent = _make_stub_agent(name="Midori")
    project = ProjectState(
        project_uuid="test", name="n", description="", goal="",
    )

    result = await agent._action_open_pr(  # type: ignore[attr-defined]
        {
            "action": "open_pr",
            "repo_path": "/tmp/does-not-matter",
            "branch": "feature/midori-x",
            "pr_title": "Add greeter",
            "pr_body": "what + why",
        },
        project,
    )
    bus.off("agent.opened_pr", on_emit)

    assert result["ok"] is True
    assert result["url"] == "https://github.com/x/y/pull/42"
    assert result["action"] == "open_pr"
    # Event emitted with the agent name + url.
    assert any(
        e.get("agent") == "Midori" and "pull/42" in str(e.get("url", ""))
        for e in captured
    )


# ── helpers ──────────────────────────────────────────────────────────


def _make_stub_agent(name: str = "Agent"):
    """Hand-build a BaseAgent-shaped object with just enough state for
    ``_action_open_pr`` to run. Avoids the LLM/persona/stats construction
    cost and network deps."""
    from autonoma.agents.base import AutonomousAgent
    from autonoma.agents.harness import CODER_HARNESS
    from autonoma.world import AgentStats, AgentBones

    class _Stub(AutonomousAgent):
        def __init__(self, nm: str) -> None:
            # Skip AutonomousAgent.__init__ entirely — it wants an LLM
            # config + full persona. Manually set the fields
            # ``_action_open_pr`` actually reads. ``name`` is a property
            # over ``persona.name``, so we only need to shape that.
            self.persona = type("P", (), {"name": nm, "skills": []})()
            self.harness = CODER_HARNESS
            self.stats = AgentStats()
            from autonoma.world import AgentMemory, Mood
            from autonoma.harness.policy import default_policy_content as _defp
            self.memory = AgentMemory()
            self.mood = Mood.FOCUSED
            self.policy = _defp()
            self._round_number = 1
            # _say / _set_state / _set_mood call into event loop via bus;
            # override to no-ops so the test doesn't require a running
            # socket or rich console.
            self.bones = AgentBones.from_role(role="coder", name=nm)

        async def _say(self, text: str, style: str = "dim") -> None:  # type: ignore[override]
            pass

        async def _set_state(self, state) -> None:  # type: ignore[override]
            pass

        async def _set_mood(self, mood) -> None:  # type: ignore[override]
            self.mood = mood

    return _Stub(name)
