"""Strategies for ``memory.summarization``.

Decides how the private-memory layer is rendered into the situation
report each turn. The knob controls prompt budget vs. long-horizon
recall — summarizing older entries keeps older context visible without
paying per-token for the raw text.

- ``none`` (default, pre-harness): emit the last N entries verbatim —
  no additional processing. Matches the current ``private[-6:]`` slice.
- ``tail_window``: same shape as ``none`` at default window size. The
  distinct name reserves a slot for future window tuning (e.g., larger
  on short-context runs, smaller on token-constrained hosted models).
- ``rolling_summary``: prepend a one-line roll-up of older entries
  ("Prior N: X observations, Y failures, Z lessons") before the tail.
  Useful for long runs where the first dozen rounds would otherwise
  drop out of the prompt entirely.

Strategy shape: ``(entries, limit) -> list[str]``. Each returned string
is a pre-formatted line ready to paste under "Private Memories".
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from autonoma.harness.strategies import register


@register("memory.summarization", "none")
def _none(entries: list[Any], limit: int) -> list[str]:
    return [f"    {e}" for e in entries[-limit:]]


@register("memory.summarization", "tail_window")
def _tail_window(entries: list[Any], limit: int) -> list[str]:
    return [f"    {e}" for e in entries[-limit:]]


@register("memory.summarization", "rolling_summary")
def _rolling_summary(entries: list[Any], limit: int) -> list[str]:
    if len(entries) <= limit:
        return [f"    {e}" for e in entries]
    older = entries[:-limit]
    counts = Counter(getattr(e, "memory_type", "observation") for e in older)
    parts = [f"{v} {k}" for k, v in counts.most_common()]
    summary = f"    [Prior {len(older)}: {', '.join(parts)}]"
    tail = [f"    {e}" for e in entries[-limit:]]
    return [summary, *tail]
