"""Lightweight observability: per-run checkpoints + LLM call traces.

Each swarm run gets its own folder under `settings.trace_dir`:

    traces/
      2026-04-11_14-30-45_hello-world/
        meta.json            -- run metadata (goal, model, timings)
        round-0001.json      -- ProjectState snapshot after round 1
        round-0002.json
        ...
        llm-calls.jsonl      -- one JSON object per messages.create call

Enabled via `settings.trace_enabled`. Failure to record never raises — we
only log at WARNING so tracing never breaks the swarm loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from autonoma.config import settings

if TYPE_CHECKING:
    from autonoma.llm import BaseLLMClient, LLMResponse
    from autonoma.models import ProjectState

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slugify(text: str, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug[:max_len] or "run"


class RunRecorder:
    """Owns a single run's trace directory and serializes writes to it."""

    def __init__(self, run_dir: Path, goal: str, model: str) -> None:
        self.run_dir = run_dir
        self.goal = goal
        self.model = model
        self.started_at = datetime.now()
        self._llm_path = run_dir / "llm-calls.jsonl"
        self._meta_path = run_dir / "meta.json"
        self._lock = asyncio.Lock()
        self._call_seq = 0
        self._checkpoints: list[int] = []

        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_meta(finished_at=None)

    # ── Metadata ──────────────────────────────────────────────────────

    def _write_meta(self, finished_at: datetime | None) -> None:
        meta = {
            "goal": self.goal,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished_at.isoformat() if finished_at else None,
            "checkpoints": self._checkpoints,
            "llm_calls": self._call_seq,
        }
        try:
            self._meta_path.write_text(json.dumps(meta, indent=2))
        except OSError as e:
            logger.warning(f"[tracing] failed to write meta: {e}")

    def finalize(self) -> None:
        self._write_meta(finished_at=datetime.now())

    # ── Checkpoints ───────────────────────────────────────────────────

    def checkpoint(self, round_num: int, project: "ProjectState") -> None:
        path = self.run_dir / f"round-{round_num:04d}.json"
        try:
            payload = project.model_dump(mode="json")
            path.write_text(json.dumps(payload, indent=2, default=str))
            if round_num not in self._checkpoints:
                self._checkpoints.append(round_num)
            self._write_meta(finished_at=None)
        except Exception as e:
            logger.warning(f"[tracing] checkpoint round={round_num} failed: {e}")

    # ── LLM call log ──────────────────────────────────────────────────

    async def log_llm_call(self, record: dict[str, Any]) -> None:
        # The asyncio.Lock serializes writes from coroutines sharing this
        # recorder. On top of that, we use os.open(O_APPEND | O_WRONLY |
        # O_CREAT) + a single os.write() of the fully pre-formatted line so
        # that even cross-recorder writers targeting the same path cannot
        # interleave mid-line — POSIX guarantees that a single write() under
        # O_APPEND is atomic for payloads up to PIPE_BUF and, in practice,
        # for typical log line sizes to regular files.
        async with self._lock:
            self._call_seq += 1
            record = {"seq": self._call_seq, **record}
            try:
                line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
                payload = line.encode("utf-8")
                fd = os.open(
                    str(self._llm_path),
                    os.O_APPEND | os.O_WRONLY | os.O_CREAT,
                    0o644,
                )
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
            except Exception as e:
                logger.warning(f"[tracing] llm log failed: {e}")


# ── Active recorder (per asyncio context) ────────────────────────────

_active: ContextVar[RunRecorder | None] = ContextVar("autonoma_recorder", default=None)


def get_active_recorder() -> RunRecorder | None:
    return _active.get()


def set_active_recorder(recorder: RunRecorder | None) -> None:
    _active.set(recorder)


def start_run(goal: str, model: str) -> RunRecorder | None:
    """Create a new recorder and mark it active. Returns None if disabled."""
    if not settings.trace_enabled:
        return None
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    slug = _slugify(goal)
    run_dir = settings.trace_dir / f"{ts}_{slug}"
    try:
        recorder = RunRecorder(run_dir=run_dir, goal=goal, model=model)
    except OSError as e:
        logger.warning(f"[tracing] could not create run dir: {e}")
        return None
    set_active_recorder(recorder)
    logger.info(f"[tracing] run started: {run_dir}")
    return recorder


def finish_run(recorder: RunRecorder | None) -> None:
    if recorder is None:
        return
    recorder.finalize()
    if get_active_recorder() is recorder:
        set_active_recorder(None)
    logger.info(f"[tracing] run finished: {recorder.run_dir}")


# ── Traced LLM call helper ────────────────────────────────────────────

async def traced_messages_create(
    client: "BaseLLMClient",
    *,
    agent: str,
    phase: str,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str,
    messages: list[dict[str, Any]],
) -> "LLMResponse":
    """Call ``client.create(...)`` and record the interaction.

    ``agent`` is the caller's name (e.g. "Director", "Alice"), ``phase``
    labels what the call is for (e.g. "decide", "decompose_goal").
    The normalized ``LLMResponse`` is returned unchanged — the recorder is
    purely additive and never raises.
    """
    recorder = get_active_recorder()
    started = time.perf_counter()
    started_iso = datetime.now().isoformat()

    req_summary = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [
            {"role": m.get("role", ""), "content": _flatten_content(m.get("content", ""))}
            for m in messages
        ],
    }

    try:
        response = await client.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )
    except Exception as exc:
        duration = round(time.perf_counter() - started, 3)
        if recorder is not None:
            await recorder.log_llm_call({
                "ts": started_iso,
                "agent": agent,
                "phase": phase,
                "duration_sec": duration,
                "request": req_summary,
                "error": f"{type(exc).__name__}: {exc}",
            })
        raise

    duration = round(time.perf_counter() - started, 3)
    if recorder is not None:
        await recorder.log_llm_call({
            "ts": started_iso,
            "agent": agent,
            "phase": phase,
            "duration_sec": duration,
            "request": req_summary,
            "response": {
                "stop_reason": response.stop_reason,
                "usage": {
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                },
                "text": response.text,
            },
        })
    return response


def _flatten_content(content: Any) -> str:
    """Flatten Anthropic-style content blocks to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)
