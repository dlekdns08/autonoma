"""Harness engineering package.

Holds the ``HarnessPolicy`` Pydantic model, its defaults, and (in later
phases) the strategy registry that backs every enum-valued knob.
"""

from autonoma.harness.policy import (
    ActionPolicy,
    BudgetPolicy,
    CachePolicy,
    CheckpointPolicy,
    DecisionPolicy,
    HarnessPolicy,
    HarnessPolicyContent,
    LoopPolicy,
    MemoryPolicy,
    MoodPolicy,
    RoutingPolicy,
    SafetyPolicy,
    SocialPolicy,
    SpawnPolicy,
    SystemPolicy,
    default_policy_content,
)

# Importing the strategy impl modules triggers ``@register`` decorators
# so the registry is populated whenever the package is loaded.
from autonoma.harness import routing_strategies as _routing_strategies  # noqa: F401
from autonoma.harness import loop_strategies as _loop_strategies  # noqa: F401
from autonoma.harness import safety_strategies as _safety_strategies  # noqa: F401
from autonoma.harness import stall_strategies as _stall_strategies  # noqa: F401
from autonoma.harness import spawn_strategies as _spawn_strategies  # noqa: F401
from autonoma.harness import action_strategies as _action_strategies  # noqa: F401
from autonoma.harness import decision_strategies as _decision_strategies  # noqa: F401
from autonoma.harness import message_strategies as _message_strategies  # noqa: F401
from autonoma.harness import llm_error_strategies as _llm_error_strategies  # noqa: F401
from autonoma.harness import enforcement_strategies as _enforcement_strategies  # noqa: F401
from autonoma.harness import memory_strategies as _memory_strategies  # noqa: F401
from autonoma.harness import (  # noqa: F401
    safety_enforcement_strategies as _safety_enforcement_strategies,
)
from autonoma.harness import mood_strategies as _mood_strategies  # noqa: F401
from autonoma.harness import system_strategies as _system_strategies  # noqa: F401
from autonoma.harness import cache_strategies as _cache_strategies  # noqa: F401
from autonoma.harness import budget_strategies as _budget_strategies  # noqa: F401
from autonoma.harness import (  # noqa: F401
    checkpoint_strategies as _checkpoint_strategies,
)

__all__ = [
    "ActionPolicy",
    "BudgetPolicy",
    "CachePolicy",
    "CheckpointPolicy",
    "DecisionPolicy",
    "HarnessPolicy",
    "HarnessPolicyContent",
    "LoopPolicy",
    "MemoryPolicy",
    "MoodPolicy",
    "RoutingPolicy",
    "SafetyPolicy",
    "SocialPolicy",
    "SpawnPolicy",
    "SystemPolicy",
    "default_policy_content",
]
