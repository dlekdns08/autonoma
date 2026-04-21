"""Budget-enforcement strategies.

Each ``budget.enforcement`` option maps to a decision function:

    (tokens_used: int, cap: int) -> BudgetVerdict

``BudgetVerdict`` is one of ``"ok"``, ``"warn"``, ``"stop"``. The swarm
loop checks this before issuing the next LLM call; ``"warn"`` is logged
and emitted as an event, ``"stop"`` short-circuits new turns.
"""

from __future__ import annotations

from typing import Literal

from autonoma.harness.strategies import register

BudgetVerdict = Literal["ok", "warn", "stop"]


@register("budget.enforcement", "soft_warn")
def _soft_warn(tokens_used: int, cap: int) -> BudgetVerdict:
    if cap <= 0:
        return "ok"
    return "warn" if tokens_used >= cap else "ok"


@register("budget.enforcement", "hard_stop")
def _hard_stop(tokens_used: int, cap: int) -> BudgetVerdict:
    if cap <= 0:
        return "ok"
    return "stop" if tokens_used >= cap else "ok"


@register("budget.enforcement", "off")
def _off(tokens_used: int, cap: int) -> BudgetVerdict:
    return "ok"
