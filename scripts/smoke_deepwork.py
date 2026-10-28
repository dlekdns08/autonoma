"""Live smoke test for deep work (spends real money: one short Agent SDK session).

    uv sync --extra agent-sdk
    export ANTHROPIC_API_KEY=...
    uv run python scripts/smoke_deepwork.py

Runs a tiny task in a scratch directory under ``output/_smoke`` with a
$0.25 budget cap and prints the streamed text, tool calls, and result.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from autonoma.agents import deepwork


async def main() -> int:
    if not deepwork.is_available():
        print("claude-agent-sdk not installed: uv sync --extra agent-sdk")
        return 2
    root = Path(tempfile.mkdtemp(prefix="autonoma-smoke-", dir=Path("output").resolve()))

    async def on_text(t: str) -> None:
        print(f"[text] {t.strip()[:200]}")

    async def on_tool(name: str, inp: dict) -> None:
        print(f"[tool] {name} {str(inp)[:120]}")

    session = deepwork.DeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="smoke",
        task_title="FizzBuzz CLI",
        task_description="Create fizzbuzz.py printing 1..15 with the FizzBuzz rules and run it.",
        project_description="smoke test",
        project_dir=root,
        max_turns=12,
        max_budget_usd=0.25,
        timeout_s=300,
        on_text=on_text,
        on_tool=on_tool,
    )
    result = await session.run()
    print(
        f"ok={result.ok} files={result.files} turns={result.turns} "
        f"cost=${result.cost_usd:.3f} duration={result.duration_s}s error={result.error!r}"
    )
    print(f"summary:\n{result.summary}")
    print(f"workspace: {root}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
