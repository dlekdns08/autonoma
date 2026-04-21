"""Pipeline view of the harness — the "16 nodes arranged in 3 groups" map.

Front-end renders these as an SVG flow diagram so users can see the
harness as a pipeline of stages rather than a form. Each node points at
one ``HarnessPolicyContent`` field; clicking a node opens the existing
per-field editor used by ``HarnessPanel``.

Two pieces of information live here and nowhere else:

- **Layout** — which field belongs to which group, and the intra-group
  ordering. That's a product/UX decision, not something derivable from
  the Pydantic model.
- **Admin-sensitivity hint** — a boolean per node saying "at least one
  value choice on this field is admin-only." The authoritative rule set
  still lives in ``autonoma.harness.validation.ADMIN_ONLY_RULES``; this
  is just a UI hint so the pipeline can paint a lock badge even before
  the user picks a value.

Everything else (enum options, numeric bounds, current values, defaults)
is pulled via ``/api/harness/schema`` and the policy payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PipelineGroup:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class PipelineNode:
    id: str
    label: str
    group: str
    field_path: str
    admin_sensitive: bool = False


GROUPS: list[PipelineGroup] = [
    PipelineGroup(
        id="A",
        label="Planning & Routing",
        description="How the swarm decomposes goals and dispatches work.",
    ),
    PipelineGroup(
        id="B",
        label="Safety & Action",
        description="Guard rails around code execution and error recovery.",
    ),
    PipelineGroup(
        id="C",
        label="System & Budget",
        description="Prompt style, provider cache, budget cap, and checkpointing.",
    ),
]


# Order within each list drives the render order left-to-right.
NODES: list[PipelineNode] = [
    # ── Group A — Planning & Routing (6) ──
    PipelineNode("loop.exit_condition", "Exit condition", "A", "loop.exit_condition"),
    PipelineNode("loop.stall_policy", "Stall policy", "A", "loop.stall_policy"),
    PipelineNode("routing.strategy", "Routing strategy", "A", "routing.strategy"),
    PipelineNode(
        "decision.message_priority",
        "Message priority",
        "A",
        "decision.message_priority",
    ),
    PipelineNode("spawn.approval_mode", "Approval mode", "A", "spawn.approval_mode"),
    PipelineNode(
        "mood.transition_strategy",
        "Mood transition",
        "A",
        "mood.transition_strategy",
    ),
    # ── Group B — Safety & Action (6) ──
    PipelineNode(
        "safety.code_execution",
        "Code execution",
        "B",
        "safety.code_execution",
        admin_sensitive=True,
    ),
    PipelineNode(
        "safety.enforcement_level",
        "Safety enforcement",
        "B",
        "safety.enforcement_level",
        admin_sensitive=True,
    ),
    PipelineNode(
        "action.harness_enforcement",
        "Action enforcement",
        "B",
        "action.harness_enforcement",
        admin_sensitive=True,
    ),
    PipelineNode(
        "action.json_extraction",
        "JSON extraction",
        "B",
        "action.json_extraction",
    ),
    PipelineNode(
        "action.llm_error_handling",
        "LLM error handling",
        "B",
        "action.llm_error_handling",
    ),
    PipelineNode("memory.summarization", "Memory summarization", "B", "memory.summarization"),
    # ── Group C — System & Budget (4) ──
    PipelineNode(
        "system.prompt_variant",
        "Prompt variant",
        "C",
        "system.prompt_variant",
    ),
    PipelineNode(
        "cache.provider_cache",
        "Provider cache",
        "C",
        "cache.provider_cache",
    ),
    PipelineNode(
        "budget.enforcement",
        "Budget enforcement",
        "C",
        "budget.enforcement",
    ),
    PipelineNode(
        "checkpoint.include_full_state",
        "Checkpoint shape",
        "C",
        "checkpoint.include_full_state",
    ),
]


def _edges_within_group(group_id: str) -> list[dict[str, str]]:
    """Chain nodes within a group head-to-tail so the UI can render a
    single-line flow per group."""
    group_nodes = [n for n in NODES if n.group == group_id]
    return [
        {"from": a.id, "to": b.id}
        for a, b in zip(group_nodes, group_nodes[1:], strict=False)
    ]


def pipeline_payload() -> dict[str, Any]:
    """JSON shape consumed by ``GET /api/harness/pipeline``.

    Flat list of nodes + groups + edges. The UI can do its own layout
    (positions aren't encoded here) but gets a stable ordering + grouping
    from the ``group`` field + the order in ``NODES``.
    """
    edges: list[dict[str, str]] = []
    for group in GROUPS:
        edges.extend(_edges_within_group(group.id))
    return {
        "groups": [
            {"id": g.id, "label": g.label, "description": g.description}
            for g in GROUPS
        ],
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "group": n.group,
                "field_path": n.field_path,
                "admin_sensitive": n.admin_sensitive,
            }
            for n in NODES
        ],
        "edges": edges,
    }
