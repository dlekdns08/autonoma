"""Spawn approval strategies — gate ``agent.spawn_requested`` events
before a new agent is instantiated.

The swarm currently auto-approves every spawn request; this module lets
presets make that stricter (``director_only``) or demand a plurality of
existing peers before accepting (``peer_vote``).

Strategy shape::

    (requester, existing_agent_names) -> (approved: bool, reason: str)

``reason`` is filled only on denial and is surfaced in the
``agent.spawn_failed`` bus event for operator visibility.
"""

from __future__ import annotations

from autonoma.harness.strategies import register


@register("spawn.approval_mode", "director_only")
def _director_only(
    requester: str,
    existing_agent_names: list[str],
) -> tuple[bool, str]:
    """Only the Director may spawn. The requester string travels in
    the bus event payload, so an empty value (legacy callers) is
    treated as an outside request and denied."""
    if requester == "Director":
        return True, ""
    return False, f"spawn denied: policy=director_only, requester='{requester or 'unknown'}'"


@register("spawn.approval_mode", "peer_vote")
def _peer_vote(
    requester: str,
    existing_agent_names: list[str],
) -> tuple[bool, str]:
    """Approve if a meaningful peer group exists to "vouch" for the
    spawn. The swarm has no real vote-collection primitive, so this
    proxies the intent: a peer quorum (at least two non-Director
    agents already in the swarm) stands in for an actual vote. The
    Director can still self-spawn regardless — it's the one role that
    doesn't need peer approval."""
    if requester == "Director":
        return True, ""
    peers = [n for n in existing_agent_names if n != "Director"]
    if len(peers) >= 2:
        return True, ""
    return (
        False,
        f"spawn denied: policy=peer_vote, peer_count={len(peers)} < 2",
    )


@register("spawn.approval_mode", "automatic")
def _automatic(
    requester: str,
    existing_agent_names: list[str],
) -> tuple[bool, str]:
    """Legacy behavior — accept every request. Matches the pre-harness
    codebase, which had no approval step at all."""
    return True, ""
