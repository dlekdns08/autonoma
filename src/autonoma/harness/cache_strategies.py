"""Provider-cache strategies.

Resolves ``cache.provider_cache`` into a simple boolean consumed by
``autonoma.llm``. Kept as a strategy (not a raw flag) so future providers
with richer cache controls — TTL, scope tiers — can slot in without
changing call sites.
"""

from __future__ import annotations

from autonoma.harness.strategies import register


@register("cache.provider_cache", "enabled")
def _enabled() -> bool:
    return True


@register("cache.provider_cache", "disabled")
def _disabled() -> bool:
    return False
