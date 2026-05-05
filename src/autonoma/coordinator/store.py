"""Filesystem-backed coordinator store. Pattern after ``scheduler.store``.

State lives under ``{settings.data_dir}/coordinator/``::

    instances.json   — registered instance records (id → InstanceRecord)
    invites.json     — every invite ever enqueued (id → MatchInvite)
    submissions.json — score submissions keyed by ``f"{match_id}:{instance_id}"``
    results.json     — resolved match results (match_id → MatchResult)
    ratings.json     — instance_id → {rating, matches, wins, losses, draws}

Concurrency is guarded by a per-file ``threading.Lock`` plus an
``asyncio.Lock`` to keep the async API correct under cooperative
multitasking. Persistence is best-effort write-replace; no fsync.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from autonoma.config import settings
from autonoma.coordinator.model import (
    InstanceRecord,
    LeaderboardEntry,
    MatchInvite,
    MatchResult,
    MatchSubmission,
)
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ── ELO ─────────────────────────────────────────────────────────────

ELO_K_FACTOR = 32.0
ELO_DEFAULT = 1200.0


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _elo_update(rating_a: float, rating_b: float, score_a: float) -> tuple[float, float]:
    """Return new ratings given ``score_a`` in {1.0, 0.5, 0.0}."""
    expected_a = _expected(rating_a, rating_b)
    expected_b = 1.0 - expected_a
    score_b = 1.0 - score_a
    new_a = rating_a + ELO_K_FACTOR * (score_a - expected_a)
    new_b = rating_b + ELO_K_FACTOR * (score_b - expected_b)
    return new_a, new_b


# ── KPI scoring ─────────────────────────────────────────────────────


def _kpi_key(kpi: dict[str, Any]) -> tuple[int, int, int, int]:
    """Sort key — higher tuple wins.

    Order of precedence:
      1. ``task_completed`` (bool→int)        — finishing trumps everything
      2. ``tasks_done`` (int)                 — more sub-tasks done is better
      3. negative ``rounds_used``             — fewer rounds wins
      4. ``files_created`` (int)              — more concrete output wins
    """
    return (
        1 if kpi.get("task_completed") else 0,
        int(kpi.get("tasks_done") or 0),
        -int(kpi.get("rounds_used") or 0),
        int(kpi.get("files_created") or 0),
    )


# ── Store ───────────────────────────────────────────────────────────


class CoordinatorStore:
    """In-memory + JSON-backed store. Singleton via ``coordinator_store``."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or (settings.data_dir / "coordinator")).resolve()
        self._instances: dict[str, InstanceRecord] = {}
        self._invites: dict[str, MatchInvite] = {}
        self._submissions: dict[str, MatchSubmission] = {}
        self._results: dict[str, MatchResult] = {}
        self._ratings: dict[str, dict[str, float]] = {}
        # Async lock is lazy-created so it binds to the *current* event
        # loop on first access. Constructing it at import time would
        # latch it to whatever loop happened to be active during module
        # import — problematic in tests where pytest-asyncio rebuilds
        # the loop per function and the singleton survives across tests.
        self._async_lock_ref: asyncio.Lock | None = None
        self._fs_lock = threading.Lock()
        self._loaded = False

    @property
    def _async_lock(self) -> asyncio.Lock:
        # Re-bind the lock if it's missing OR was attached to a stale
        # loop. ``asyncio.get_running_loop()`` raises if we're not in a
        # loop, so we only re-check when there is one.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        existing = self._async_lock_ref
        if existing is None:
            self._async_lock_ref = asyncio.Lock()
            return self._async_lock_ref
        if running is not None:
            bound = getattr(existing, "_loop", None)
            if bound is not None and bound is not running:
                self._async_lock_ref = asyncio.Lock()
        return self._async_lock_ref

    # ── filesystem helpers ────────────────────────────────────────

    def _path(self, name: str) -> Path:
        return self._root / name

    def _write_json(self, name: str, payload: Any) -> None:
        with self._fs_lock:
            self._root.mkdir(parents=True, exist_ok=True)
            path = self._path(name)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            os.replace(tmp, path)

    def _read_json(self, name: str) -> Any:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[coordinator] failed to load %s: %s", name, exc)
            return None

    def _load(self) -> None:
        if self._loaded:
            return
        raw = self._read_json("instances.json") or {}
        for inst_id, data in raw.items():
            try:
                self._instances[inst_id] = InstanceRecord.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[coordinator] skipping corrupt instance %s: %s", inst_id, exc)
        raw = self._read_json("invites.json") or {}
        for inv_id, data in raw.items():
            try:
                self._invites[inv_id] = MatchInvite.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[coordinator] skipping corrupt invite %s: %s", inv_id, exc)
        raw = self._read_json("submissions.json") or {}
        for key, data in raw.items():
            try:
                self._submissions[key] = MatchSubmission.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[coordinator] skipping corrupt submission %s: %s", key, exc)
        raw = self._read_json("results.json") or {}
        for match_id, data in raw.items():
            try:
                self._results[match_id] = MatchResult.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[coordinator] skipping corrupt result %s: %s", match_id, exc)
        raw = self._read_json("ratings.json") or {}
        for inst_id, data in raw.items():
            if isinstance(data, dict):
                self._ratings[inst_id] = {
                    "rating": float(data.get("rating", ELO_DEFAULT)),
                    "matches": float(data.get("matches", 0)),
                    "wins": float(data.get("wins", 0)),
                    "losses": float(data.get("losses", 0)),
                    "draws": float(data.get("draws", 0)),
                }
        self._loaded = True

    def _persist_instances(self) -> None:
        self._write_json(
            "instances.json",
            {k: v.model_dump(mode="json") for k, v in self._instances.items()},
        )

    def _persist_invites(self) -> None:
        self._write_json(
            "invites.json",
            {k: v.model_dump(mode="json") for k, v in self._invites.items()},
        )

    def _persist_submissions(self) -> None:
        self._write_json(
            "submissions.json",
            {k: v.model_dump(mode="json") for k, v in self._submissions.items()},
        )

    def _persist_results(self) -> None:
        self._write_json(
            "results.json",
            {k: v.model_dump(mode="json") for k, v in self._results.items()},
        )

    def _persist_ratings(self) -> None:
        self._write_json("ratings.json", self._ratings)

    # ── ratings helpers ───────────────────────────────────────────

    def _rating_row(self, instance_id: str) -> dict[str, float]:
        row = self._ratings.get(instance_id)
        if row is None:
            row = {
                "rating": ELO_DEFAULT,
                "matches": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
            }
            self._ratings[instance_id] = row
        return row

    # ── public API ────────────────────────────────────────────────

    async def register_instance(self, instance_id: str, name: str, endpoint: str) -> None:
        """Register or update an instance record."""
        async with self._async_lock:
            self._load()
            record = InstanceRecord(instance_id=instance_id, name=name, endpoint=endpoint)
            self._instances[instance_id] = record
            self._persist_instances()
            # Pre-seed the rating row so the leaderboard shows new
            # entrants at the default rating even before their first match.
            self._rating_row(instance_id)
            self._persist_ratings()

    async def list_instances(self) -> list[InstanceRecord]:
        async with self._async_lock:
            self._load()
            return list(self._instances.values())

    async def enqueue_match(self, invite: MatchInvite) -> MatchInvite:
        """Persist ``invite`` (assigning a fresh id if needed) and return it."""
        async with self._async_lock:
            self._load()
            if invite.id in self._invites:
                # Avoid id collision with an existing invite.
                invite = invite.model_copy(update={"id": MatchInvite().id})
            self._invites[invite.id] = invite
            self._persist_invites()
            return invite

    async def pair_pending_matches(
        self,
    ) -> list[tuple[MatchInvite, MatchInvite]]:
        """Pair unmatched invites by goal, FIFO, two distinct instances."""
        async with self._async_lock:
            self._load()
            pairs: list[tuple[MatchInvite, MatchInvite]] = []
            # FIFO by created_at.
            pending = sorted(
                (inv for inv in self._invites.values() if inv.match_id is None),
                key=lambda i: i.created_at,
            )
            consumed: set[str] = set()
            for i, head in enumerate(pending):
                if head.id in consumed:
                    continue
                for j in range(i + 1, len(pending)):
                    cand = pending[j]
                    if cand.id in consumed:
                        continue
                    if cand.goal != head.goal:
                        continue
                    if cand.instance_id == head.instance_id:
                        continue
                    match_id = uuid.uuid4().hex
                    head.match_id = match_id
                    head.opponent_invite_id = cand.id
                    head.opponent_instance_id = cand.instance_id
                    cand.match_id = match_id
                    cand.opponent_invite_id = head.id
                    cand.opponent_instance_id = head.instance_id
                    self._invites[head.id] = head
                    self._invites[cand.id] = cand
                    consumed.add(head.id)
                    consumed.add(cand.id)
                    pairs.append((head, cand))
                    break
            if pairs:
                self._persist_invites()
            return pairs

    async def submit_score(
        self, match_id: str, instance_id: str, kpi: dict[str, Any]
    ) -> Optional[MatchResult]:
        """Record one side's score. When both submit, resolve + return result."""
        async with self._async_lock:
            self._load()
            # Find both invites of this match.
            paired = [inv for inv in self._invites.values() if inv.match_id == match_id]
            if len(paired) != 2:
                logger.warning(
                    "[coordinator] submit_score: match %s has %d invites",
                    match_id,
                    len(paired),
                )
                return None
            participants = {inv.instance_id for inv in paired}
            if instance_id not in participants:
                logger.warning(
                    "[coordinator] submit_score: %s not in match %s",
                    instance_id,
                    match_id,
                )
                return None
            sub = MatchSubmission(match_id=match_id, instance_id=instance_id, kpi=dict(kpi or {}))
            self._submissions[f"{match_id}:{instance_id}"] = sub
            self._persist_submissions()

            # Already resolved? Return the cached result.
            if match_id in self._results:
                return self._results[match_id]

            # Both sides in?
            keys = {f"{match_id}:{inv.instance_id}" for inv in paired}
            if not keys.issubset(self._submissions.keys()):
                return None

            # Stable A/B order — alphabetical by instance_id keeps the
            # result deterministic regardless of submission order.
            inv_a, inv_b = sorted(paired, key=lambda i: i.instance_id)
            kpi_a = self._submissions[f"{match_id}:{inv_a.instance_id}"].kpi
            kpi_b = self._submissions[f"{match_id}:{inv_b.instance_id}"].kpi
            key_a, key_b = _kpi_key(kpi_a), _kpi_key(kpi_b)
            if key_a > key_b:
                winner = inv_a.instance_id
                score_a = 1.0
            elif key_b > key_a:
                winner = inv_b.instance_id
                score_a = 0.0
            else:
                winner = "draw"
                score_a = 0.5

            row_a = self._rating_row(inv_a.instance_id)
            row_b = self._rating_row(inv_b.instance_id)
            before_a, before_b = row_a["rating"], row_b["rating"]
            after_a, after_b = _elo_update(before_a, before_b, score_a)
            row_a["rating"] = after_a
            row_b["rating"] = after_b
            row_a["matches"] += 1
            row_b["matches"] += 1
            if winner == inv_a.instance_id:
                row_a["wins"] += 1
                row_b["losses"] += 1
            elif winner == inv_b.instance_id:
                row_b["wins"] += 1
                row_a["losses"] += 1
            else:
                row_a["draws"] += 1
                row_b["draws"] += 1
            self._persist_ratings()

            result = MatchResult(
                match_id=match_id,
                goal=inv_a.goal,
                instance_a=inv_a.instance_id,
                instance_b=inv_b.instance_id,
                kpi_a=kpi_a,
                kpi_b=kpi_b,
                winner=winner,
                elo_before_a=before_a,
                elo_before_b=before_b,
                elo_after_a=after_a,
                elo_after_b=after_b,
                elo_delta_a=after_a - before_a,
                elo_delta_b=after_b - before_b,
            )
            self._results[match_id] = result
            self._persist_results()

        # Emit outside the lock so handlers can call back in.
        await bus.emit(
            "coordinator.match_resolved",
            match_id=match_id,
            result=result.model_dump(mode="json"),
        )
        return result

    async def get_result(self, match_id: str) -> Optional[MatchResult]:
        async with self._async_lock:
            self._load()
            return self._results.get(match_id)

    async def get_leaderboard(self, limit: int = 50) -> list[LeaderboardEntry]:
        async with self._async_lock:
            self._load()
            entries: list[LeaderboardEntry] = []
            for inst_id, row in self._ratings.items():
                inst = self._instances.get(inst_id)
                entries.append(
                    LeaderboardEntry(
                        instance_id=inst_id,
                        name=inst.name if inst else "",
                        rating=float(row.get("rating", ELO_DEFAULT)),
                        matches=int(row.get("matches", 0)),
                        wins=int(row.get("wins", 0)),
                        losses=int(row.get("losses", 0)),
                        draws=int(row.get("draws", 0)),
                    )
                )
            entries.sort(key=lambda e: e.rating, reverse=True)
            if limit > 0:
                entries = entries[:limit]
            return entries


coordinator_store = CoordinatorStore()


# ── Module-level async API ─────────────────────────────────────────
# These thin wrappers match the signatures the spec asks for, while
# giving callers an easy seam for tests (instantiate a fresh store
# pointed at ``tmp_path`` and call methods on it directly).


async def register_instance(instance_id: str, name: str, endpoint: str) -> None:
    await coordinator_store.register_instance(instance_id, name, endpoint)


async def list_instances() -> list[InstanceRecord]:
    return await coordinator_store.list_instances()


async def enqueue_match(invite: MatchInvite) -> MatchInvite:
    return await coordinator_store.enqueue_match(invite)


async def pair_pending_matches() -> list[tuple[MatchInvite, MatchInvite]]:
    return await coordinator_store.pair_pending_matches()


async def submit_score(
    match_id: str, instance_id: str, kpi: dict[str, Any]
) -> Optional[MatchResult]:
    return await coordinator_store.submit_score(match_id, instance_id, kpi)


async def get_leaderboard(limit: int = 50) -> list[LeaderboardEntry]:
    return await coordinator_store.get_leaderboard(limit=limit)
