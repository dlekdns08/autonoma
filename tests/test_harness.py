"""Tests for the agent harness system."""

import pytest

from autonoma.agents.harness import (
    CODER_HARNESS,
    DIRECTOR_HARNESS,
    HARNESS_REGISTRY,
    REVIEWER_HARNESS,
    TESTER_HARNESS,
    AgentCapability,
    AgentHarness,
    get_harness,
)


class TestAgentHarness:
    def test_director_cannot_create_files(self):
        assert not DIRECTOR_HARNESS.can_perform("create_file")

    def test_director_can_spawn(self):
        assert DIRECTOR_HARNESS.can_spawn is True
        assert DIRECTOR_HARNESS.can_perform("spawn_agent")

    def test_coder_cannot_spawn(self):
        assert not CODER_HARNESS.can_perform("spawn_agent")

    def test_coder_can_create_files(self):
        assert CODER_HARNESS.can_perform("create_file")

    def test_reviewer_is_read_only(self):
        assert REVIEWER_HARNESS.read_only is True
        assert not REVIEWER_HARNESS.can_perform("create_file")
        assert not REVIEWER_HARNESS.can_perform("complete_task")
        assert not REVIEWER_HARNESS.can_perform("spawn_agent")

    def test_reviewer_can_review_and_message(self):
        assert REVIEWER_HARNESS.can_perform("review_work")
        assert REVIEWER_HARNESS.can_perform("send_message")

    def test_tester_has_failure_modes(self):
        assert len(TESTER_HARNESS.failure_modes) >= 2
        # Should include verification avoidance
        assert any("avoidance" in fm.lower() for fm in TESTER_HARNESS.failure_modes)

    def test_tester_has_verdict_format(self):
        assert "VERDICT" in TESTER_HARNESS.output_format

    def test_unknown_action_allowed_by_default(self):
        assert CODER_HARNESS.can_perform("some_unknown_action")


class TestHarnessPrompt:
    def test_system_prompt_includes_role(self):
        prompt = CODER_HARNESS.build_system_prompt("TestCoder", ["python", "testing"])
        assert "TestCoder" in prompt
        assert "python" in prompt

    def test_system_prompt_includes_failure_modes(self):
        prompt = TESTER_HARNESS.build_system_prompt("TestTester", ["testing"])
        assert "RECOGNIZE YOUR OWN FAILURE MODES" in prompt
        assert "verification avoidance" in prompt.lower()

    def test_read_only_prompt_includes_restriction(self):
        prompt = REVIEWER_HARNESS.build_system_prompt("TestReviewer", ["review"])
        assert "READ-ONLY" in prompt
        assert "CANNOT" in prompt

    def test_director_prompt_includes_no_file_restriction(self):
        prompt = DIRECTOR_HARNESS.build_system_prompt("Director", ["planning"])
        assert "RESTRICTED ACTIONS" in prompt

    def test_critical_reminder(self):
        reminder = DIRECTOR_HARNESS.get_critical_reminder()
        assert "Director" in reminder
        assert "REMINDER" in reminder

    def test_no_reminder_for_harness_without_one(self):
        harness = AgentHarness(name="Test", role_description="test")
        assert harness.get_critical_reminder() == ""


class TestGetHarness:
    def test_direct_match(self):
        assert get_harness("coder") is CODER_HARNESS
        assert get_harness("director") is DIRECTOR_HARNESS

    def test_keyword_match(self):
        assert get_harness("software engineer") is CODER_HARNESS
        assert get_harness("code reviewer") is REVIEWER_HARNESS
        assert get_harness("test engineer") is TESTER_HARNESS
        assert get_harness("technical writer") is HARNESS_REGISTRY["writer"]

    def test_default_fallback(self):
        assert get_harness("unknown role xyz") is CODER_HARNESS

    def test_all_registry_entries_exist(self):
        assert len(HARNESS_REGISTRY) >= 6
        for name, harness in HARNESS_REGISTRY.items():
            assert harness.name
            assert harness.role_description
