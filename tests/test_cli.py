"""Smoke tests for the ``autonoma`` CLI entry points.

The CLI is implemented with ``click`` so we drive it through Click's
``CliRunner``. Heavy dependencies (the engine, the LLM) are stubbed via
monkeypatch so the tests don't actually start a swarm.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from autonoma.cli import _derive_name, cli


def test_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    # Each documented subcommand should appear in the help output.
    assert "build" in result.output
    assert "interactive" in result.output
    assert "demo" in result.output


def test_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_build_aborts_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ANTHROPIC_API_KEY the CLI should bail out with exit code 1."""
    from autonoma.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")

    runner = CliRunner()
    result = runner.invoke(cli, ["build", "make a thing"])
    assert result.exit_code == 1
    assert "No Anthropic API key" in result.output


def test_build_invokes_engine_with_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``autonoma build`` should construct the engine and run it once."""
    from autonoma.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-fake")

    captured: dict[str, object] = {}

    class FakeEngine:
        def __init__(self, console, output_dir=None) -> None:
            captured["constructed"] = True
            captured["output_dir"] = output_dir

        async def run(self, **kwargs) -> None:
            captured["run_kwargs"] = kwargs

    monkeypatch.setattr("autonoma.engine.AutonomaEngine", FakeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["build", "build a url shortener", "--name", "shorty", "--rounds", "3"],
    )
    assert result.exit_code == 0, result.output
    assert captured["constructed"] is True
    run_kwargs = captured["run_kwargs"]
    assert run_kwargs["name"] == "shorty"
    assert run_kwargs["description"] == "build a url shortener"
    assert run_kwargs["max_rounds"] == 3


@pytest.mark.parametrize(
    "description,expected",
    [
        ("Build a tiny website", "build-a-tiny"),
        # Pure-digit words are filtered (no isalpha) but alpha words remain.
        ("123 numeric only", "numeric-only"),
        # Wholly digit / empty input falls back to ``project``.
        ("", "project"),
        ("999 888 777", "project"),
    ],
)
def test_derive_name(description: str, expected: str) -> None:
    assert _derive_name(description) == expected
