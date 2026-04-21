"""Safety strategies — gate dangerous actions before they run.

``safety.code_execution``
    Allows or blocks LLM-authored code from reaching the sandbox. The
    ``disabled`` variant short-circuits at the dispatch site so the
    sandbox never receives the payload — useful for hosted demos where
    shelling out to a user-written program is unacceptable regardless
    of how strong the sandbox is.

Strategy shape: a zero-arg predicate returning ``True`` if the action
should proceed. Keeping the return type boolean (rather than a rich
decision object) matches the call sites — they just want a yes/no
gate.
"""

from __future__ import annotations

from autonoma.harness.strategies import register


@register("safety.code_execution", "sandbox")
def _allow_sandbox() -> bool:
    return True


@register("safety.code_execution", "disabled")
def _deny_code_execution() -> bool:
    return False
