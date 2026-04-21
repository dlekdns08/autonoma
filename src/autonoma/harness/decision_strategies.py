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
    - ``abort``: re-raise the parse error. Strict mode for development
      and CI, where a broken LLM output should surface loudly instead of
      being papered over.

Strategy shape: ``(exc, agent_name) -> dict[str, Any]``. ``abort`` raises
instead of returning. Caller is expected to use the returned dict
directly as the turn's decision.
"""

from __future__ import annotations

from typing import Any

from autonoma.harness.strategies import register


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
    raise exc
