"""HarnessPolicy — the typed shape of every runtime behavior knob.

Structure:

- ``HarnessPolicyContent`` is the serializable body (what gets stored as
  JSON on disk). Nine sub-policies group knobs by concern: loop, action,
  decision, memory, spawn, routing, safety, mood, social.
- ``HarnessPolicy`` wraps the content with persistence metadata: id,
  owner user id, display name, default flag, timestamps.

Every algorithmic branch is a ``Literal[...]`` enum — values map to
callables in ``autonoma.harness.strategies`` (populated in Phase 2). Every
numeric knob has ``ge`` / ``le`` bounds so wildly-broken presets are
rejected at validation time rather than at run time.

Defaults match the current hardcoded behavior exactly; the ``default``
preset seeded at startup is therefore behavior-equivalent to the
pre-harness codebase, which keeps Phase 3 a mechanical refactor.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── sub-policies ──────────────────────────────────────────────────────


class LoopPolicy(BaseModel):
    """Governs the outer round-robin loop and per-agent step timing."""

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=40, ge=10, le=500)
    agent_timeout_s: float = Field(default=90.0, ge=30.0, le=600.0)
    llm_timeout_s: float = Field(default=60.0, ge=15.0, le=300.0)
    exit_condition: Literal[
        "all_tasks_done", "director_decides", "max_rounds_only"
    ] = "all_tasks_done"
    stall_policy: Literal[
        "auto_unblock", "wait", "escalate_to_director"
    ] = "auto_unblock"


class ActionPolicy(BaseModel):
    """Controls the action-dispatch surface: what agents may do, and how
    rough edges (invalid JSON, sandbox failures) are handled."""

    model_config = ConfigDict(extra="forbid")

    inbox_size: int = Field(default=50, ge=10, le=500)
    sandbox_wall_time_s: int = Field(default=8, ge=1, le=60)
    sandbox_memory_mb: int = Field(default=256, ge=64, le=2048)
    json_extraction: Literal[
        "direct", "fenced_first", "fallback_chain"
    ] = "fallback_chain"
    llm_error_handling: Literal[
        "backoff", "rate_limit_sleep", "abort"
    ] = "backoff"
    harness_enforcement: Literal["strict", "permissive", "off"] = "strict"


class DecisionPolicy(BaseModel):
    """LLM decision-layer behavior (parsing, retries, message priority)."""

    model_config = ConfigDict(extra="forbid")

    max_parse_retries: int = Field(default=3, ge=0, le=10)
    on_parse_failure: Literal[
        "skip_turn", "force_idle", "abort"
    ] = "skip_turn"
    message_priority: Literal[
        "urgency_ordered", "fifo", "round_robin"
    ] = "urgency_ordered"


class MemoryPolicy(BaseModel):
    """Per-agent memory footprint and TTS budgets."""

    model_config = ConfigDict(extra="forbid")

    max_private_memories: int = Field(default=20, ge=5, le=200)
    max_hindsight_memories: int = Field(default=15, ge=0, le=100)
    tts_chars_per_round: int = Field(default=800, ge=0, le=4000)
    tts_chars_per_session: int = Field(default=20_000, ge=0, le=200_000)
    summarization: Literal[
        "none", "tail_window", "rolling_summary"
    ] = "none"


class SpawnPolicy(BaseModel):
    """Rules around spawning new agents."""

    model_config = ConfigDict(extra="forbid")

    max_agents: int = Field(default=8, ge=1, le=32)
    cooldown_rounds: int = Field(default=2, ge=0, le=20)
    approval_mode: Literal[
        "director_only", "peer_vote", "automatic"
    ] = "director_only"


class RoutingPolicy(BaseModel):
    """How the swarm dispatches tasks and messages among agents."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal[
        "priority", "round_robin", "broadcast"
    ] = "priority"


class SafetyPolicy(BaseModel):
    """Circuit breakers and code-execution policy."""

    model_config = ConfigDict(extra="forbid")

    kill_on_repeat_failure: int = Field(default=5, ge=1, le=20)
    code_execution: Literal["sandbox", "disabled"] = "sandbox"
    enforcement_level: Literal["strict", "permissive", "off"] = "strict"


class MoodPolicy(BaseModel):
    """Emotional-state transition rules and environmental sentiment."""

    model_config = ConfigDict(extra="forbid")

    weather_affect_probability: float = Field(default=0.3, ge=0.0, le=1.0)
    sentiment_positive_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    sentiment_negative_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    transition_strategy: Literal[
        "sticky", "reactive", "random_walk"
    ] = "sticky"

    @model_validator(mode="after")
    def _positive_above_negative(self) -> "MoodPolicy":
        if self.sentiment_positive_threshold <= self.sentiment_negative_threshold:
            raise ValueError(
                "sentiment_positive_threshold must exceed sentiment_negative_threshold"
            )
        return self


class SystemPolicy(BaseModel):
    """Shapes the persona system prompt rendered for each agent.

    ``prompt_variant`` picks the overall tone/length; the full prompt is
    still generated from the persona + world state, this just scales how
    much flavor text gets appended before the task list.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_variant: Literal["balanced", "concise", "elaborate"] = "balanced"


class CachePolicy(BaseModel):
    """Provider-side prompt-cache behavior.

    Anthropic supports explicit ``cache_control`` blocks; other providers
    no-op on this flag. Keeping it enabled reduces cost and latency on
    repeated system-prompt blocks, which dominate token usage in long
    swarm runs."""

    model_config = ConfigDict(extra="forbid")

    provider_cache: Literal["enabled", "disabled"] = "enabled"


class BudgetPolicy(BaseModel):
    """Soft / hard cap on total tokens a single run is allowed to spend.

    ``enforcement=soft_warn`` emits a warning event when the cap is
    crossed; ``hard_stop`` halts new turns; ``off`` disables the check
    entirely (still tracks usage for observability).
    """

    model_config = ConfigDict(extra="forbid")

    tokens_per_run: int = Field(default=500_000, ge=10_000, le=10_000_000)
    enforcement: Literal["soft_warn", "hard_stop", "off"] = "soft_warn"


class CheckpointPolicy(BaseModel):
    """Periodic ``session.checkpoint`` event cadence.

    Zero disables the timer; non-zero emits a snapshot event every N
    seconds so an external durable store (or the UI) can persist a
    restore point."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(default=60, ge=0, le=600)
    include_full_state: Literal["on", "off"] = "off"


class SocialPolicy(BaseModel):
    """Relationship thresholds and periodic social-event cadences."""

    model_config = ConfigDict(extra="forbid")

    friend_trust_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    rival_trust_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    guild_trust_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    trading_post_interval: int = Field(default=4, ge=0, le=20)
    guild_formation_interval: int = Field(default=5, ge=0, le=20)
    campfire_interval: int = Field(default=7, ge=0, le=20)
    quest_interval: int = Field(default=3, ge=0, le=20)

    @model_validator(mode="after")
    def _friend_above_rival(self) -> "SocialPolicy":
        if self.friend_trust_threshold <= self.rival_trust_threshold:
            raise ValueError(
                "friend_trust_threshold must exceed rival_trust_threshold"
            )
        return self


# ── top-level content & record ────────────────────────────────────────


class HarnessPolicyContent(BaseModel):
    """The serializable body of a preset — the shape stored as JSON."""

    model_config = ConfigDict(extra="forbid")

    loop: LoopPolicy = Field(default_factory=LoopPolicy)
    action: ActionPolicy = Field(default_factory=ActionPolicy)
    decision: DecisionPolicy = Field(default_factory=DecisionPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    spawn: SpawnPolicy = Field(default_factory=SpawnPolicy)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    mood: MoodPolicy = Field(default_factory=MoodPolicy)
    social: SocialPolicy = Field(default_factory=SocialPolicy)
    system: SystemPolicy = Field(default_factory=SystemPolicy)
    cache: CachePolicy = Field(default_factory=CachePolicy)
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)
    checkpoint: CheckpointPolicy = Field(default_factory=CheckpointPolicy)


class HarnessPolicy(BaseModel):
    """A persisted preset: metadata + content.

    ``owner_user_id`` is nullable so the system-wide ``default`` preset
    (``is_default=True``) isn't tied to any specific account.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    owner_user_id: str | None
    name: str = Field(min_length=1, max_length=64)
    is_default: bool = False
    content: HarnessPolicyContent = Field(default_factory=HarnessPolicyContent)
    created_at: datetime
    updated_at: datetime


def default_policy_content() -> HarnessPolicyContent:
    """Return a fresh content object matching the current codebase defaults.

    The ``default`` preset stored in the DB is built from this and must be
    byte-equivalent to the pre-harness hardcoded behavior."""
    return HarnessPolicyContent()
