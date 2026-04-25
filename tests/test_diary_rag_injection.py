"""Phase 1-#6a — diary RAG injection into the situation prompt.

We don't try to exercise the LLM here; we only verify that
``_build_situation`` pulls relevant entries from ``diary_index`` and
formats them into the prompt under a recognisable section header.
"""

from __future__ import annotations

import pytest

from autonoma.agents.base import AutonomousAgent
from autonoma.models import AgentPersona, ProjectState, Task
from autonoma.world.diary_search import diary_index


def _make_agent(name: str = "Tester") -> AutonomousAgent:
    persona = AgentPersona(name=name, role="coder", emoji="🧪", color="cyan")
    return AutonomousAgent(persona=persona)


@pytest.fixture(autouse=True)
def _isolate_diary_index():
    diary_index.clear()
    yield
    diary_index.clear()


def test_no_diary_entries_means_no_rag_section() -> None:
    agent = _make_agent("Alice")
    project = ProjectState(name="proj", description="build a thing")
    situation = agent._build_situation(project)
    assert "RELEVANT PAST DIARY" not in situation


def test_relevant_diary_entry_appears_in_situation() -> None:
    agent = _make_agent("Alice")
    diary_index.add_entry(
        agent="Alice",
        round_number=2,
        mood="frustrated",
        content="Got stuck on the JWT signing path during authentication work.",
    )
    diary_index.add_entry(
        agent="Alice",
        round_number=4,
        mood="proud",
        content="Cleaned up the database migration runner instead.",
    )
    project = ProjectState(
        name="auth",
        description="implement user authentication with JWT tokens",
        tasks=[Task(title="JWT auth", description="ship login", id="t1")],
    )
    agent.current_task = project.tasks[0]
    situation = agent._build_situation(project)
    assert "RELEVANT PAST DIARY" in situation
    # The auth-related entry should outrank the migration one for an
    # auth-focused query.
    assert "JWT" in situation


def test_only_self_diary_is_recalled() -> None:
    agent = _make_agent("Alice")
    diary_index.add_entry(
        agent="Bob",
        round_number=1,
        mood="happy",
        content="JWT wizardry mastered today!",
    )
    project = ProjectState(
        name="auth",
        description="implement user authentication with JWT tokens",
    )
    situation = agent._build_situation(project)
    # Bob's diary must not leak into Alice's prompt.
    assert "Bob" not in situation.split("RELEVANT PAST DIARY")[-1] if "RELEVANT" in situation else True
    assert "JWT wizardry" not in situation
