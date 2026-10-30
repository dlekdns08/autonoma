"""Goal suites and configuration variants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Goal:
    id: str
    name: str
    description: str
    max_rounds: int = 12


@dataclass(frozen=True)
class Variant:
    """Settings overrides applied for the duration of a run.

    Keys are ``autonoma.config.Settings`` attribute names; the runner
    patches them on the live settings object and restores them after.
    """

    name: str
    description: str
    overrides: dict[str, Any] = field(default_factory=dict)


# Small, self-contained builds whose output can be checked mechanically
# (files exist, parse, run). Kept cheap: a handful of rounds each.
SUITES: dict[str, list[Goal]] = {
    "smoke": [
        Goal(
            "fizzbuzz",
            "fizzbuzz-cli",
            "A Python CLI that prints FizzBuzz for 1..N (N from argv) with a unit test.",
        ),
        Goal(
            "wordcount",
            "word-count",
            "A Python module that counts word frequencies in a text file and prints the top 10, plus tests.",
        ),
    ],
    "core": [
        Goal(
            "fizzbuzz",
            "fizzbuzz-cli",
            "A Python CLI that prints FizzBuzz for 1..N (N from argv) with a unit test.",
        ),
        Goal(
            "wordcount",
            "word-count",
            "A Python module that counts word frequencies in a text file and prints the top 10, plus tests.",
        ),
        Goal(
            "todo-api",
            "todo-api",
            "A minimal FastAPI todo service (create, list, complete) with an in-memory store and tests.",
            16,
        ),
        Goal(
            "csv-stats",
            "csv-stats",
            "A Python script that reads a CSV and prints mean/median/min/max per numeric column, with tests.",
            14,
        ),
        Goal(
            "markdown-toc",
            "md-toc",
            "A Python tool that inserts a table of contents into a Markdown file based on its headings, with tests.",
            14,
        ),
        Goal(
            "rate-limiter",
            "rate-limiter",
            "A token-bucket rate limiter class in Python with thread safety and tests.",
            14,
        ),
    ],
}

VARIANTS: dict[str, Variant] = {
    "baseline": Variant("baseline", "Sonnet 5.5 Director + Haiku 5.5 workers, tool-use protocol"),
    "sonnet-workers": Variant(
        "sonnet-workers", "Workers on Sonnet 5.5 too", {"worker_model": "claude-sonnet-5-5"}
    ),
    "advisor": Variant(
        "advisor",
        "Haiku workers consult Opus 5.5 via the advisor tool",
        {"advisor_model": "claude-opus-5-5"},
    ),
    "json-protocol": Variant(
        "json-protocol", "Legacy JSON-in-text decisions", {"decision_protocol": "json"}
    ),
    "no-interventions": Variant(
        "no-interventions", "Rule-based Director only", {"director_llm_interventions": False}
    ),
    "worker-effort-medium": Variant(
        "worker-effort-medium", "Worker effort low → medium", {"worker_effort": "medium"}
    ),
    "deep-work": Variant(
        "deep-work", "Tasks delegated to Claude Agent SDK sessions", {"worker_backend": "agent_sdk"}
    ),
}
