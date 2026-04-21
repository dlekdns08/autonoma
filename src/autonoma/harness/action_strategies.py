"""Action-layer strategies.

``action.json_extraction``
    Pulls a JSON object out of an LLM response. LLMs don't reliably
    emit clean JSON: they wrap it in ``` fences, add chatty prose, or
    sandwich it between explanations. The three strategies differ in
    how aggressively they try to recover.

``action.llm_error_handling`` (TODO phase 3)
``action.harness_enforcement`` (TODO phase 3)

Each strategy takes ``(text,)`` and returns the parsed ``dict`` or
raises ``ValueError`` with a short diagnostic. Keeping the signature
stable lets the caller pick a strategy per request without caring
which one is active.
"""

from __future__ import annotations

import json
import re
from typing import Any

from autonoma.harness.strategies import register


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _parse_direct(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _parse_fence(text: str) -> dict[str, Any] | None:
    match = _FENCE_RE.search(text)
    if not match:
        return None
    try:
        value = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _parse_brace_span(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


@register("action.json_extraction", "direct")
def _direct(text: str) -> dict[str, Any]:
    """Strict — only accept valid JSON at the top level. Pairs well
    with LLM setups that enforce JSON mode server-side."""
    text = text.strip()
    parsed = _parse_direct(text)
    if parsed is None:
        raise ValueError(
            f"Could not extract JSON via direct (first 200 chars): {text[:200]}"
        )
    return parsed


@register("action.json_extraction", "fenced_first")
def _fenced_first(text: str) -> dict[str, Any]:
    """Prefer a ```json fenced block when present; fall back to direct
    parse. Skips the loose brace-span recovery, which can silently
    misparse prose."""
    text = text.strip()
    for parser in (_parse_fence, _parse_direct):
        parsed = parser(text)
        if parsed is not None:
            return parsed
    raise ValueError(
        f"Could not extract JSON via fenced_first (first 200 chars): {text[:200]}"
    )


@register("action.json_extraction", "fallback_chain")
def _fallback_chain(text: str) -> dict[str, Any]:
    """Default. Tries direct → fenced → brace-span. Matches the
    pre-harness ``_extract_json`` behavior exactly — the brace-span
    recovery rescues common cases like 'Sure! Here is your JSON: {...}
    hope that helps' that smaller models produce."""
    text = text.strip()
    for parser in (_parse_direct, _parse_fence, _parse_brace_span):
        parsed = parser(text)
        if parsed is not None:
            return parsed
    raise ValueError(
        f"Could not extract JSON via fallback_chain (first 200 chars): {text[:200]}"
    )
