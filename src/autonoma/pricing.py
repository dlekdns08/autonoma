"""List-price cost accounting for LLM calls.

Prices are USD per million tokens at Anthropic's public list rates
(cached 2026-10). ``input_tokens`` from the API excludes cache reads, so a
call costs ``input × in + cache_read × in × 0.1 + output × out``. Cache
*writes* (1.25× input) are not reported separately by our client and are
billed here as ordinary input — a slight under-estimate on the first
call of a session. Unknown models (OpenAI, vLLM, renamed ids) return
``None`` so callers can say "cost unknown" instead of pretending it's 0.
"""

from __future__ import annotations

# (input $/MTok, output $/MTok). Longest prefix wins.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_READ_MULTIPLIER = 0.1


def price_for(model: str) -> tuple[float, float] | None:
    best: tuple[int, tuple[float, float]] | None = None
    for prefix, price in _PRICES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float | None:
    price = price_for(model)
    if price is None:
        return None
    pin, pout = price
    return (
        input_tokens * pin + cache_read_tokens * pin * CACHE_READ_MULTIPLIER + output_tokens * pout
    ) / 1_000_000
