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
import re
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from autonoma.config import settings

if TYPE_CHECKING:
    import anthropic
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
        async with self._lock:
            self._call_seq += 1
            record = {"seq": self._call_seq, **record}
            try:
                line = json.dumps(record, default=str, ensure_ascii=False)
                with self._llm_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
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


# ── Traced messages.create helper ────────────────────────────────────

async def traced_messages_create(
    client: "anthropic.AsyncAnthropic",
    *,
    agent: str,
    phase: str,
    **kwargs: Any,
) -> Any:
    """Call `client.messages.create(**kwargs)` and record the interaction.

    `agent` is the name of the caller (e.g. "Director", "Alice"), `phase`
    is a short label describing what the call is for (e.g. "decide",
    "decompose_goal"). All other kwargs are passed through unchanged, and
    the anthropic response is returned unchanged — the recorder is purely
    additive and never raises.
    """
    recorder = get_active_recorder()
    started = time.perf_counter()
    started_iso = datetime.now().isoformat()

    try:
        response = await client.messages.create(**kwargs)
    except Exception as exc:
        duration = round(time.perf_counter() - started, 3)
        if recorder is not None:
            await recorder.log_llm_call({
                "ts": started_iso,
                "agent": agent,
                "phase": phase,
                "duration_sec": duration,
                "request": _summarize_request(kwargs),
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
            "request": _summarize_request(kwargs),
            "response": _summarize_response(response),
        })
    return response


def _summarize_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Compact, JSON-safe view of a messages.create request."""
    messages = kwargs.get("messages") or []
    flat_messages = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            # content blocks -> join text parts
            parts = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
                else:
                    parts.append(str(block))
            content = "\n".join(parts)
        flat_messages.append({"role": m.get("role", ""), "content": content})
    return {
        "model": kwargs.get("model"),
        "max_tokens": kwargs.get("max_tokens"),
        "temperature": kwargs.get("temperature"),
        "system": kwargs.get("system"),
        "messages": flat_messages,
    }


def _summarize_response(response: Any) -> dict[str, Any]:
    """Compact, JSON-safe view of a messages.create response."""
    text_parts: list[str] = []
    try:
        for block in getattr(response, "content", []) or []:
            txt = getattr(block, "text", None)
            if txt:
                text_parts.append(txt)
    except Exception:
        pass

    usage = getattr(response, "usage", None)
    usage_dict: dict[str, Any] | None = None
    if usage is not None:
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }

    return {
        "stop_reason": getattr(response, "stop_reason", None),
        "usage": usage_dict,
        "text": "\n".join(text_parts),
    }
