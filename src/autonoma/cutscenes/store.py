"""Filesystem-backed cutscene store.

Layout::

    {data_dir}/cutscenes/{owner_user_id}/{cutscene_id}.json

We keep one file per cutscene so concurrent edits to different
cutscenes don't fight over a lock. Concurrent edits to the same
cutscene aren't a real concern — only the owner can write theirs — but
we still write atomically (tmp + rename) to avoid leaving a partial
file on a crash.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterator

from autonoma.config import settings
from autonoma.cutscenes.model import Cutscene

logger = logging.getLogger(__name__)


class CutsceneNotFound(LookupError):
    pass


class CutsceneStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or (settings.data_dir / "cutscenes")).resolve()
        # One write lock per (owner, id) pair — enough granularity for a
        # single-host app and avoids a global mutex.
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _key_lock(self, owner: str, cutscene_id: str) -> threading.Lock:
        with self._locks_guard:
            key = (owner, cutscene_id)
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    def _user_dir(self, owner: str) -> Path:
        # ``owner`` comes from the cookie session — it's already a
        # validated UUID/string, but normalise for filesystem safety.
        safe = "".join(c for c in owner if c.isalnum() or c in "-_") or "anon"
        return self._root / safe

    def _path(self, owner: str, cutscene_id: str) -> Path:
        safe_id = "".join(c for c in cutscene_id if c.isalnum() or c in "-_")
        if not safe_id:
            raise ValueError("cutscene_id contains no safe characters")
        return self._user_dir(owner) / f"{safe_id}.json"

    # ── Mutating ops ─────────────────────────────────────────────────

    def save(self, cutscene: Cutscene) -> Cutscene:
        cutscene.touch()
        path = self._path(cutscene.owner_user_id, cutscene.id)
        lk = self._key_lock(cutscene.owner_user_id, cutscene.id)
        with lk:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cutscene.model_dump(mode="json"), indent=2))
            os.replace(tmp, path)
        return cutscene

    def delete(self, owner: str, cutscene_id: str) -> bool:
        path = self._path(owner, cutscene_id)
        lk = self._key_lock(owner, cutscene_id)
        with lk:
            if not path.exists():
                return False
            try:
                path.unlink()
                return True
            except OSError as exc:
                logger.warning(f"[cutscenes] delete failed: {exc}")
                return False

    # ── Read ops ──────────────────────────────────────────────────────

    def get(self, owner: str, cutscene_id: str) -> Cutscene:
        path = self._path(owner, cutscene_id)
        if not path.exists():
            raise CutsceneNotFound(f"{owner}/{cutscene_id}")
        try:
            return Cutscene.model_validate_json(path.read_text())
        except Exception as exc:
            raise ValueError(f"corrupt cutscene file {path}: {exc}") from exc

    def list_for_owner(self, owner: str) -> list[Cutscene]:
        d = self._user_dir(owner)
        if not d.exists():
            return []
        out: list[Cutscene] = []
        for entry in sorted(d.glob("*.json")):
            try:
                out.append(Cutscene.model_validate_json(entry.read_text()))
            except Exception as exc:
                logger.warning(f"[cutscenes] skipping corrupt {entry.name}: {exc}")
        # Most-recently-edited first — matches user intuition when
        # opening the composer.
        out.sort(key=lambda c: c.updated_at, reverse=True)
        return out

    def iter_all(self) -> Iterator[Cutscene]:
        if not self._root.exists():
            return
        for owner_dir in sorted(self._root.iterdir()):
            if not owner_dir.is_dir():
                continue
            for entry in sorted(owner_dir.glob("*.json")):
                try:
                    yield Cutscene.model_validate_json(entry.read_text())
                except Exception:
                    continue


# Module-level singleton for the FastAPI routes.
cutscene_store = CutsceneStore()
