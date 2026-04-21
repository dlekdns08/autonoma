"""Inbox-trimming strategies for ``decision.message_priority``.

When an agent's inbox overflows its cap, we have to drop something. The
three strategies are opinions on what a "fair" drop looks like:

- ``urgency_ordered`` (default, pre-harness behavior): rank by a
  per-message-type priority table, keep the highest-urgency, and within
  equal-urgency prefer the most recent. Critical coordination messages
  (task assignments, help requests) never get crowded out by chat.
- ``fifo``: plain ring buffer — drop oldest, keep newest N. No urgency
  awareness; useful when every message type is equally important.
- ``round_robin``: keep roughly equal counts per sender so one chatty
  agent can't monopolize anyone's inbox. Preserves cross-agent signal
  at the cost of occasionally evicting high-urgency messages.

Strategy shape: ``(messages, max_size, priority_fn) -> trimmed_list``.
The priority_fn is passed in from the call site (rather than imported
here) so this module stays free of ``AgentMessage`` coupling — tests can
use ``SimpleNamespace`` stand-ins. All strategies return messages in
arrival (timestamp) order so downstream display code doesn't have to
re-sort.
"""

from __future__ import annotations

from typing import Any, Callable

from autonoma.harness.strategies import register


@register("decision.message_priority", "urgency_ordered")
def _urgency_ordered(
    messages: list[Any],
    max_size: int,
    priority_fn: Callable[[Any], int],
) -> list[Any]:
    if len(messages) <= max_size:
        return list(messages)
    # Two-key sort — priority ASC (lower number = more urgent), insertion
    # index DESC (prefer recent among ties). Then restore arrival order.
    pairs = list(enumerate(messages))
    pairs.sort(key=lambda p: (priority_fn(p[1]), -p[0]))
    kept = [m for _, m in pairs[:max_size]]
    return sorted(kept, key=lambda m: m.timestamp)


@register("decision.message_priority", "fifo")
def _fifo(
    messages: list[Any],
    max_size: int,
    priority_fn: Callable[[Any], int],
) -> list[Any]:
    if len(messages) <= max_size:
        return list(messages)
    return list(messages[-max_size:])


@register("decision.message_priority", "round_robin")
def _round_robin(
    messages: list[Any],
    max_size: int,
    priority_fn: Callable[[Any], int],
) -> list[Any]:
    if len(messages) <= max_size:
        return list(messages)
    # Bucket by sender preserving arrival order, then rotate-pop the
    # newest (tail) of each bucket until we've kept ``max_size``. A
    # sender with N messages gets roughly ceil(max_size / num_senders)
    # slots before others start getting seconds.
    buckets: dict[str, list[Any]] = {}
    for m in messages:
        buckets.setdefault(m.sender, []).append(m)
    kept: list[Any] = []
    active = list(buckets.values())
    while active and len(kept) < max_size:
        next_round: list[list[Any]] = []
        for bucket in active:
            if len(kept) >= max_size:
                break
            if bucket:
                kept.append(bucket.pop())
                if bucket:
                    next_round.append(bucket)
        active = next_round
    return sorted(kept, key=lambda m: m.timestamp)
