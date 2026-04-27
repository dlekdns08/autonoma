"""Filesystem-backed schedule store. Mirrors ``cutscenes.store``."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from autonoma.config import settings
from autonoma.scheduler.model import Schedule, ScheduleNotFound

logger = logging.getLogger(__name__)


class ScheduleStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or (settings.data_dir / "schedules")).resolve()
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, owner: str, sched_id: str) -> threading.Lock:
        with self._guard:
            key = (owner, sched_id)
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    def _user_dir(self, owner: str) -> Path:
        safe = "".join(c for c in owner if c.isalnum() or c in "-_") or "anon"
        return self._root / safe

    def _path(self, owner: str, sched_id: str) -> Path:
        if not sched_id or any(
            not (c.isalnum() or c in "-_") for c in sched_id
        ):
            raise ValueError(
                "schedule id must be alphanumeric / dash / underscore only"
            )
        return self._user_dir(owner) / f"{sched_id}.json"

    # ── Mutation ─────────────────────────────────────────────────────

    def save(self, schedule: Schedule) -> Schedule:
        schedule.touch()
        path = self._path(schedule.owner_user_id, schedule.id)
        with self._lock(schedule.owner_user_id, schedule.id):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(schedule.model_dump(mode="json"), indent=2))
            os.replace(tmp, path)
        return schedule

    def delete(self, owner: str, sched_id: str) -> bool:
        path = self._path(owner, sched_id)
        with self._lock(owner, sched_id):
            if not path.exists():
                return False
            try:
                path.unlink()
                return True
            except OSError as exc:
                logger.warning(f"[scheduler] delete failed: {exc}")
                return False

    # ── Read ─────────────────────────────────────────────────────────

    def get(self, owner: str, sched_id: str) -> Schedule:
        path = self._path(owner, sched_id)
        if not path.exists():
            raise ScheduleNotFound(f"{owner}/{sched_id}")
        return Schedule.model_validate_json(path.read_text())

    def list_for_owner(self, owner: str) -> list[Schedule]:
        d = self._user_dir(owner)
        if not d.exists():
            return []
        out: list[Schedule] = []
        for entry in sorted(d.glob("*.json")):
            try:
                out.append(Schedule.model_validate_json(entry.read_text()))
            except Exception as exc:
                logger.warning(f"[scheduler] skipping corrupt {entry.name}: {exc}")
        out.sort(key=lambda s: s.daily_at_utc)
        return out

    def iter_all(self) -> list[Schedule]:
        # Accumulate into a list so the runner's poll iteration is
        # decoupled from filesystem scans (we re-list once per minute).
        if not self._root.exists():
            return []
        out: list[Schedule] = []
        for owner_dir in self._root.iterdir():
            if not owner_dir.is_dir():
                continue
            for entry in owner_dir.glob("*.json"):
                try:
                    out.append(Schedule.model_validate_json(entry.read_text()))
                except Exception:
                    logger.warning(
                        "[scheduler] failed to load %s during iter_all", entry,
                        exc_info=True,
                    )
                    continue
        return out


schedule_store = ScheduleStore()
