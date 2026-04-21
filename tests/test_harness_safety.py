"""Tests for ``safety.code_execution`` strategy.

The strategy itself is a pure predicate; the integration test confirms
that ``_action_run_code`` actually honors the gate before touching the
sandbox.
"""

from __future__ import annotations

import pytest

from autonoma.harness import safety_strategies  # noqa: F401 — ensures registration
from autonoma.harness.policy import HarnessPolicyContent, SafetyPolicy
from autonoma.harness.strategies import is_stub, lookup


def test_strategies_are_registered() -> None:
    assert is_stub("safety.code_execution", "sandbox") is False
    assert is_stub("safety.code_execution", "disabled") is False


def test_sandbox_predicate_allows() -> None:
    assert lookup("safety.code_execution", "sandbox")() is True


def test_disabled_predicate_denies() -> None:
    assert lookup("safety.code_execution", "disabled")() is False


@pytest.mark.asyncio
async def test_action_run_code_short_circuits_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration: with code_execution=disabled the sandbox must not
    be touched. We assert by patching ``CodeSandbox`` to explode if
    anyone constructs it — the strategy gate runs first, so no
    construction should happen."""
    from autonoma.agents.base import AutonomousAgent
    from autonoma.agents import base as base_mod
    from autonoma.models import AgentPersona, ProjectState

    def _boom(*a: object, **kw: object) -> object:
        raise AssertionError("CodeSandbox should not be instantiated when disabled")

    monkeypatch.setattr(base_mod, "CodeSandbox", _boom)

    policy = HarnessPolicyContent(safety=SafetyPolicy(code_execution="disabled"))
    agent = AutonomousAgent(
        persona=AgentPersona(name="Tester", role="coder", emoji="🧪", color="cyan"),
        policy=policy,
    )

    result = await agent._action_run_code(
        {"code_body": "print('hi')", "code_language": "python"},
        ProjectState(name="t", description="t"),
    )
    assert result == {
        "agent": "Tester",
        "action": "run_code",
        "error": "code_execution_disabled",
    }


@pytest.mark.asyncio
async def test_action_run_code_proceeds_to_sandbox_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the strategy is ``sandbox`` (default), the gate must pass
    through. We stub the sandbox to capture that it was reached without
    actually shelling out."""
    from autonoma.agents.base import AutonomousAgent
    from autonoma.agents import base as base_mod
    from autonoma.models import AgentPersona, ProjectState

    called: dict[str, bool] = {"ran": False}

    class _FakeSandbox:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def run(self, code: str, language: object) -> object:
            called["ran"] = True

            class _Res:
                ok = True
                backend = "fake"
                exit_code = 0
                duration_sec = 0.01
                timed_out = False
                truncated = False
                stdout = "hi"
                stderr = ""

                def summarize(self, max_chars: int = 400) -> str:
                    return "ok"

            return _Res()

    monkeypatch.setattr(base_mod, "CodeSandbox", _FakeSandbox)

    policy = HarnessPolicyContent()  # defaults → safety.code_execution="sandbox"
    agent = AutonomousAgent(
        persona=AgentPersona(name="Tester", role="coder", emoji="🧪", color="cyan"),
        policy=policy,
    )
    result = await agent._action_run_code(
        {"code_body": "print('hi')", "code_language": "python"},
        ProjectState(name="t", description="t"),
    )
    assert called["ran"] is True
    assert result["action"] == "run_code"
    assert result.get("error") is None
