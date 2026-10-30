"""Eval harness: settings patching, budget sharing/stop, report, dry run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autonoma.config import settings
from autonoma.eval import runner
from autonoma.eval.runner import RunResult, patched_settings, render_report, summarize
from autonoma.eval.suite import SUITES, VARIANTS, Goal, Variant


def _res(variant: str, done: int, total: int, cost: float, ok: bool = True) -> RunResult:
    return RunResult(
        variant=variant,
        goal="g",
        repeat=0,
        ok=ok,
        exit_reason="project_complete",
        rounds=5,
        tasks_total=total,
        tasks_done=done,
        files=2,
        files_ci_ok=1,
        cost_usd=cost,
        cache_hit_ratio=0.5,
        duration_s=1.0,
    )


def test_patched_settings_restores() -> None:
    before = settings.worker_effort
    with patched_settings({"worker_effort": "max"}):
        assert settings.worker_effort == "max"
    assert settings.worker_effort == before


def test_variants_only_override_real_settings() -> None:
    for v in VARIANTS.values():
        for key in v.overrides:
            assert hasattr(settings, key), (v.name, key)
    assert {"smoke", "core"} <= set(SUITES)


def test_summary_and_report() -> None:
    rs = [_res("a", 2, 4, 0.10), _res("a", 4, 4, 0.30), _res("b", 1, 4, 0.05, ok=False)]
    s = summarize(rs)
    assert s["a"]["completion_mean"] == pytest.approx(0.75)
    assert s["a"]["cost_per_task_done"] == pytest.approx(0.40 / 6)
    assert s["b"]["crashes"] == 1
    md = render_report(rs, budget_usd=1.0, spent=0.45)
    assert "| a | 2 | 75%" in md and "fewer than 5 times" in md


@pytest.mark.asyncio
async def test_budget_is_shared_and_stops(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shares: list[float] = []

    async def fake_run_one(
        goal: Goal, variant: Variant, rep: int, *, budget_usd: float, **kw: Any
    ) -> RunResult:
        shares.append(budget_usd)
        return _res(variant.name, 1, 2, cost=0.6)

    monkeypatch.setattr(runner, "run_one", fake_run_one)
    goals = [Goal("g1", "g1", "x"), Goal("g2", "g2", "y")]
    variants = [VARIANTS["baseline"], VARIANTS["advisor"]]
    results = await runner.run_eval(goals, variants, repeats=1, budget_usd=1.0, out_dir=tmp_path)
    # 4 scheduled; first gets 1/4 of the budget, second the rest split 3 ways,
    # then the $1 is gone (0.6 + 0.6) and the eval stops.
    assert len(results) == 2
    assert shares[0] == pytest.approx(0.25)
    assert shares[1] == pytest.approx(0.4 / 3)
    # Interleaved: the two runs that happened are one per variant.
    assert [r.variant for r in results] == ["baseline", "advisor"]
    assert (tmp_path / "runs.jsonl").read_text().count("\n") == 2
    assert (tmp_path / "report.md").exists()


def test_live_run_requires_budget(capsys: pytest.CaptureFixture[str]) -> None:
    from autonoma.eval.__main__ import main

    with pytest.raises(SystemExit):
        main(["--variants", "baseline"])
    assert "--budget-usd is required" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_dry_run_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma.eval.stub import stub_factory
    from autonoma.llm import LLMConfig

    cfg = LLMConfig(
        provider="anthropic",
        api_key="dry",
        model="claude-sonnet-5-5",
        worker_model="claude-haiku-5-5",
    )
    results = await runner.run_eval(
        [Goal("fizz", "fizz", "fizzbuzz", max_rounds=6)],
        [VARIANTS["baseline"]],
        repeats=1,
        budget_usd=1.0,
        out_dir=tmp_path,
        client_factory=stub_factory,
        llm_config=cfg,
    )
    assert len(results) == 1
    r = results[0]
    assert r.ok, r.error
    assert r.tasks_total == 2 and r.tasks_done >= 1
    assert r.files >= 1 and r.files_ci_ok == r.files
    assert r.cost_usd > 0 and 0 < r.cache_hit_ratio < 1
