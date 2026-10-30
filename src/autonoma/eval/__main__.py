"""CLI: ``python -m autonoma.eval``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from autonoma.eval.runner import run_sync
from autonoma.eval.suite import SUITES, VARIANTS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autonoma.eval", description=__doc__)
    ap.add_argument("--suite", default="smoke", choices=sorted(SUITES))
    ap.add_argument("--variants", default="baseline", help=f"comma list of {', '.join(VARIANTS)}")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="hard cap for the whole eval (required unless --dry-run)",
    )
    ap.add_argument("--dry-run", action="store_true", help="stub LLM, no network, no cost")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    unknown = [v for v in args.variants.split(",") if v not in VARIANTS]
    if unknown:
        ap.error(f"unknown variant(s): {', '.join(unknown)}")
    if not args.dry_run and not args.budget_usd:
        ap.error("--budget-usd is required for live runs (this spends real API money)")

    variants = [VARIANTS[v] for v in args.variants.split(",")]
    goals = SUITES[args.suite]
    out = args.out or Path("output") / "eval" / datetime.now().strftime("%Y%m%d-%H%M%S")
    client_factory = None
    llm_config = None
    if args.dry_run:
        from autonoma.eval.stub import stub_factory
        from autonoma.llm import LLMConfig

        client_factory = stub_factory
        llm_config = LLMConfig(
            provider="anthropic",
            api_key="dry-run",
            model="claude-sonnet-5-5",
            worker_model="claude-haiku-5-5",
        )
    runs = len(goals) * len(variants) * args.repeats
    print(
        f"eval: {runs} run(s) · suite={args.suite} · variants={args.variants} · "
        f"{'DRY RUN' if args.dry_run else f'budget ${args.budget_usd:.2f}'} → {out}"
    )
    run_sync(
        goals=goals,
        variants=variants,
        repeats=args.repeats,
        budget_usd=args.budget_usd or 1.0,
        out_dir=out,
        client_factory=client_factory,
        llm_config=llm_config,
    )
    report = out / "report.md"
    if args.dry_run:
        report.write_text(
            "> **DRY RUN** — stub model, no API calls. Costs and completion are synthetic;\n"
            "> this only proves the harness works.\n\n" + report.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    print(report.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
