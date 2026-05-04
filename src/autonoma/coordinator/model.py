"""Pydantic models for the swarm-vs-swarm coordinator."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class InstanceRecord(BaseModel):
    """A registered Autonoma instance available for matchmaking."""

    instance_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    endpoint: str = Field(..., min_length=1, max_length=500)
    registered_at: str = Field(default_factory=_now_iso)


class MatchInvite(BaseModel):
    """A pending challenge waiting to be paired against another invite."""

    id: str = Field(default_factory=_new_id)
    instance_id: str = Field(..., min_length=1, max_length=64)
    goal: str = Field(..., min_length=1, max_length=2000)
    max_rounds: int = Field(default=20, ge=1, le=500)
    harness_preset_id: Optional[str] = Field(default=None, max_length=120)
    created_at: str = Field(default_factory=_now_iso)
    # Set when ``pair_pending_matches`` matches this invite to another;
    # both sides share the same ``match_id`` once paired.
    match_id: Optional[str] = None
    # Opposite invite once paired.
    opponent_invite_id: Optional[str] = None
    opponent_instance_id: Optional[str] = None


class MatchSubmission(BaseModel):
    """One side's KPI report for a paired match."""

    match_id: str
    instance_id: str
    kpi: dict[str, Any] = Field(default_factory=dict)
    submitted_at: str = Field(default_factory=_now_iso)


class MatchResult(BaseModel):
    """Resolved match outcome with ELO delta."""

    match_id: str
    goal: str
    instance_a: str
    instance_b: str
    kpi_a: dict[str, Any]
    kpi_b: dict[str, Any]
    winner: str  # one of: instance_id, or "draw"
    elo_before_a: float
    elo_before_b: float
    elo_after_a: float
    elo_after_b: float
    elo_delta_a: float
    elo_delta_b: float
    resolved_at: str = Field(default_factory=_now_iso)


class LeaderboardEntry(BaseModel):
    """One row of the public leaderboard."""

    instance_id: str
    name: str = ""
    rating: float = 1200.0
    matches: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
