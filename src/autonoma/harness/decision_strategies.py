"""Decision-layer strategies.

``decision.on_parse_failure``
    Fires when the LLM response can't be decoded into an action dict —
    bad JSON, wrong shape, empty body. The three variants trade chatty
    continuity for debugging strictness:

    - ``skip_turn``: the pre-harness default. Return an idle action with
      a visible "couldn't parse" speech bubble so the UI shows the hiccup
      without stalling the run.
    - ``force_idle``: same structural fallback but silent — no speech —
      for demos where we'd rather swallow the miss than narrate it.
    - ``abort``: signal the caller to stop the round immediately. Strict
      mode for development and CI, where a broken LLM output should
      surface loudly instead of being papered over.  The caller detects
      the ``parse_failure_abort`` thinking tag and propagates the error.

Strategy shape: ``(exc, agent_name) -> dict[str, Any]``. ``abort``
returns a sentinel dict; the caller (``AutonomousAgent.decide``) is
responsible for detecting it and re-raising.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register


class ParseFailureAbort(RuntimeError):
    """Raised when the ``abort`` on_parse_failure policy is active.

    Distinct from generic RuntimeError so the decide() caller can
    re-raise it without accidentally swallowing unrelated errors.
    """


@register("decision.on_parse_failure", "skip_turn")
def _skip_turn(exc: Exception, agent_name: str) -> dict[str, Any]:
    return {
        "action": "idle",
        "speech": "Couldn't parse my thoughts...",
        "thinking": "parse_error",
    }


@register("decision.on_parse_failure", "force_idle")
def _force_idle(exc: Exception, agent_name: str) -> dict[str, Any]:
    return {
        "action": "idle",
        "speech": "",
        "thinking": "force_idle",
    }


@register("decision.on_parse_failure", "abort")
def _abort(exc: Exception, agent_name: str) -> dict[str, Any]:
    """Re-raise *exc* immediately so strict-mode callers surface the error."""
    raise exc
