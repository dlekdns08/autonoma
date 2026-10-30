"""Run goals × variants × repeats and collect metrics."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from autonoma.config import settings
from autonoma.eval.suite import Goal, Variant

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    variant: str
    goal: str
    repeat: int
    ok: bool
    exit_reason: str
    rounds: int
    tasks_total: int
    tasks_done: int
    files: int
    files_ci_ok: int
    cost_usd: float
    cache_hit_ratio: float
    duration_s: float
    error: str = ""
    unpriced_calls: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def completion(self) -> float:
        return self.tasks_done / self.tasks_total if self.tasks_total else 0.0

    @property
    def ci_pass_rate(self) -> float:
        return self.files_ci_ok / self.files if self.files else 0.0


@contextlib.contextmanager
def patched_settings(overrides: dict[str, Any]) -> Iterator[None]:
    saved = {k: getattr(settings, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(settings, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


async def _ci_pass(project: Any) -> tuple[int, int]:
    """Objective check on the artifacts: does each file parse/lint?"""
    from autonoma.ci_loop import run_ci_check

    latest: dict[str, str] = {}
    for f in project.files:
        latest[f.path] = f.content
    ok = 0
    for path, content in latest.items():
        res = await run_ci_check(path, content)
        ok += int(res.ok)
    return len(latest), ok


async def run_one(
    goal: Goal,
    variant: Variant,
    repeat: int,
    *,
    budget_usd: float,
    llm_config: Any = None,
    client_factory: Any = None,
) -> RunResult:
    """One headless swarm run in an isolated data/output directory."""
    from autonoma.agents.swarm import AgentSwarm
    from autonoma.llm import llm_config_from_settings
    from autonoma.models import ProjectState

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"autonoma-eval-{goal.id}-") as tmp:
        overrides = {
            **variant.overrides,
            "data_dir": Path(tmp) / "data",
            "output_dir": Path(tmp) / "output",
            "trace_dir": Path(tmp) / "traces",
            "swarm_budget_usd": max(0.01, budget_usd),
            "gesture_gen_enabled": False,
            "managed_dreams_enabled": False,
        }
        with patched_settings(overrides):
            # The DB engine is a process singleton bound to the data_dir it
            # first saw; point it at this run's scratch DB and migrate it.
            from autonoma.db.engine import dispose_engine, init_db

            await dispose_engine()
            await init_db()
            cfg = llm_config or llm_config_from_settings()
            swarm = AgentSwarm(llm_config=cfg)
            if client_factory is not None:
                # Dry runs: every agent (incl. ones spawned later) gets the stub.
                swarm.director._client = client_factory("Director")
                original_spawn = swarm.spawn_agent

                def spawn_with_stub(*a: Any, **kw: Any) -> Any:
                    agent = original_spawn(*a, **kw)
                    if agent is not None:
                        agent._client = client_factory(agent.name)
                    return agent

                swarm.spawn_agent = spawn_with_stub  # type: ignore[method-assign]
            project = ProjectState(name=goal.name, description=goal.description)
            error = ""
            try:
                await swarm.initialize(project)
                await swarm.run(project, max_rounds=goal.max_rounds)
            except Exception as exc:  # a crashed run is a data point, not a crash
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                logger.exception("[eval] %s/%s#%d crashed", variant.name, goal.id, repeat)
            files, ci_ok = await _ci_pass(project)
            cost = swarm.cost_summary()
            await dispose_engine()
            done = sum(
                1 for t in project.tasks if str(getattr(t.status, "value", t.status)) == "done"
            )
            return RunResult(
                variant=variant.name,
                goal=goal.id,
                repeat=repeat,
                ok=not error,
                exit_reason=getattr(swarm, "_last_exit_reason", "") or ("error" if error else ""),
                rounds=getattr(swarm, "_round", 0),
                tasks_total=len(project.tasks),
                tasks_done=done,
                files=files,
                files_ci_ok=ci_ok,
                cost_usd=float(cost["total_usd"]),
                cache_hit_ratio=float(cost["cache_hit_ratio"]),
                duration_s=round(time.perf_counter() - started, 1),
                error=error,
                unpriced_calls=int(cost["unpriced_calls"]),
            )


async def run_eval(
    goals: list[Goal],
    variants: list[Variant],
    *,
    repeats: int,
    budget_usd: float,
    out_dir: Path,
    client_factory: Any = None,
    llm_config: Any = None,
) -> list[RunResult]:
    """Interleave variants per goal/repeat so a budget stop leaves a fair
    (balanced) comparison rather than one variant fully run and the next
    not started. Each run gets an equal share of what is left."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "runs.jsonl"
    schedule = [(g, v, r) for r in range(repeats) for g in goals for v in variants]
    results: list[RunResult] = []
    spent = 0.0
    for i, (goal, variant, rep) in enumerate(schedule):
        remaining = budget_usd - spent
        if remaining <= 0:
            logger.warning("[eval] budget exhausted after %d/%d runs", i, len(schedule))
            break
        share = remaining / (len(schedule) - i)
        res = await run_one(
            goal,
            variant,
            rep,
            budget_usd=share,
            client_factory=client_factory,
            llm_config=llm_config,
        )
        spent += res.cost_usd
        results.append(res)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**asdict(res), "completion": res.completion}) + "\n")
        logger.info(
            "[eval] %s/%s#%d: %d/%d tasks, %d/%d files ok, $%.4f (total $%.4f)",
            variant.name,
            goal.id,
            rep,
            res.tasks_done,
            res.tasks_total,
            res.files_ci_ok,
            res.files,
            res.cost_usd,
            spent,
        )
    (out_dir / "report.md").write_text(render_report(results, budget_usd, spent), encoding="utf-8")
    return results


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def _sd(xs: list[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def summarize(results: list[RunResult]) -> dict[str, dict[str, float]]:
    by: dict[str, list[RunResult]] = {}
    for r in results:
        by.setdefault(r.variant, []).append(r)
    out: dict[str, dict[str, float]] = {}
    for name, rs in by.items():
        completion = [r.completion for r in rs]
        cost = [r.cost_usd for r in rs]
        out[name] = {
            "runs": len(rs),
            "completion_mean": _mean(completion),
            "completion_sd": _sd(completion),
            "ci_pass_mean": _mean([r.ci_pass_rate for r in rs]),
            "cost_mean": _mean(cost),
            "cost_per_task_done": (sum(cost) / max(1, sum(r.tasks_done for r in rs))),
            "rounds_mean": _mean([float(r.rounds) for r in rs]),
            "cache_hit_mean": _mean([r.cache_hit_ratio for r in rs]),
            "crashes": float(sum(1 for r in rs if not r.ok)),
        }
    return out


def render_report(results: list[RunResult], budget_usd: float, spent: float) -> str:
    s = summarize(results)
    lines = [
        "# Autonoma eval report",
        "",
        f"Runs: {len(results)} · spent ${spent:.4f} of ${budget_usd:.2f}",
        "",
        "Completion = tasks the swarm marked done / tasks planned (self-reported).",
        "CI pass = produced files that parse/lint (objective).",
        "",
        "| variant | runs | completion | CI pass | $/run | $/task done | rounds | cache hit | crashes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, m in s.items():
        lines.append(
            f"| {name} | {int(m['runs'])} | {m['completion_mean']:.0%} ± {m['completion_sd']:.0%} "
            f"| {m['ci_pass_mean']:.0%} | ${m['cost_mean']:.4f} | ${m['cost_per_task_done']:.4f} "
            f"| {m['rounds_mean']:.1f} | {m['cache_hit_mean']:.0%} | {int(m['crashes'])} |"
        )
    unpriced = sum(r.unpriced_calls for r in results)
    if unpriced:
        lines += [
            "",
            f"Note: {unpriced} LLM call(s) had no list price (non-Anthropic model); costs undercount.",
        ]
    small = [n for n, m in s.items() if m["runs"] < 5]
    if small:
        lines += [
            "",
            f"Caution: {', '.join(small)} ran fewer than 5 times; differences under ~15 points "
            "of completion are within run-to-run noise at this sample size.",
        ]
    return "\n".join(lines) + "\n"


def run_sync(**kwargs: Any) -> list[RunResult]:
    return asyncio.run(run_eval(**kwargs))
