"""Session-scoped TTS worker: budget + rate limit + fan-out to the WS.

Why a worker instead of synthesizing inline?

- ``agent._say`` fires from a tight agent loop; we don't want it to
  block on a 300-700ms HTTP round-trip. A worker lets us fire-and-forget.
- A small in-process queue gives us one place to enforce budgets and
  rate limits so we don't blow up Azure bills.
- The worker owns a concurrency limit (semaphore), so two agents
  speaking simultaneously produce two parallel synth calls, not a
  head-of-line block.

Protocol (events emitted on the bus):

- ``agent.speech_audio_start {agent, seq, voice, mood, mime}``
- ``agent.speech_audio_chunk {agent, seq, index, b64}``
- ``agent.speech_audio_end   {agent, seq, total_bytes}``
- ``agent.speech_audio_dropped {agent, reason}``

``seq`` is a per-agent monotonic counter so the browser can discard
orphaned chunks from a superseded utterance. Each request gets a fresh
seq, atomically assigned before any chunk goes out.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from autonoma.config import settings
from autonoma.event_bus import bus
from autonoma.tts import BaseTTSClient, TTSError, create_tts_client, tts_config_from_settings

logger = logging.getLogger(__name__)


MAX_TEXT_CHARS = 500       # hard ceiling per utterance (safety rail)
MAX_QUEUE_DEPTH = 64       # drop new jobs once the queue is this long
SYNTH_CONCURRENCY = 2      # how many parallel synth HTTP calls allowed


@dataclass
class _SpeechJob:
    agent: str
    text: str
    voice: str
    mood: str
    language: str


@dataclass
class _AgentSpeechCounter:
    seq: int = 0


@dataclass
class TTSBudget:
    """Tracks char budgets + RPM rate limit for a single session."""

    session_round_chars: int = 0
    session_total_chars: int = 0
    # sliding-window timestamps of recently issued synth requests
    issued_timestamps: deque[float] = field(default_factory=deque)

    def reset_round(self) -> None:
        self.session_round_chars = 0

    def try_consume(self, text_len: int) -> Optional[str]:
        """Return a reason-string when the job should be dropped, else None."""
        now = time.monotonic()
        # prune timestamps older than 60s
        while self.issued_timestamps and now - self.issued_timestamps[0] > 60:
            self.issued_timestamps.popleft()

        if len(self.issued_timestamps) >= settings.tts_rate_limit_per_minute:
            return "rate_limited"
        if self.session_round_chars + text_len > settings.tts_char_budget_per_round:
            return "round_budget"
        if self.session_total_chars + text_len > settings.tts_char_budget_per_session:
            return "session_budget"
        # allowed
        self.session_round_chars += text_len
        self.session_total_chars += text_len
        self.issued_timestamps.append(now)
        return None


class TTSWorker:
    """Single-consumer async worker. Start one per session when TTS is on."""

    def __init__(self, client: BaseTTSClient | None = None) -> None:
        self._client = client or create_tts_client(tts_config_from_settings())
        self._queue: asyncio.Queue[_SpeechJob] = asyncio.Queue(maxsize=MAX_QUEUE_DEPTH)
        self._task: asyncio.Task | None = None
        self._sema = asyncio.Semaphore(SYNTH_CONCURRENCY)
        self._counters: dict[str, _AgentSpeechCounter] = {}
        self._budget = TTSBudget()
        self._stopped = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="tts-worker")

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def enqueue(self, *, agent: str, text: str, voice: str, mood: str = "", language: str = "ko") -> bool:
        """Schedule a line. Returns False if dropped upfront.

        Drops happen for three reasons: worker stopped, queue full, or
        text too long. Budget / rate-limit are checked at pop-time (after
        waiting a moment so we can collapse bursts).
        """
        if self._stopped or not settings.tts_enabled:
            return False
        if not text or not text.strip():
            return False
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
        job = _SpeechJob(agent=agent, text=text, voice=voice, mood=mood, language=language)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            logger.debug("tts queue full; dropping %r for %s", text[:40], agent)
            asyncio.create_task(
                bus.emit(
                    "agent.speech_audio_dropped",
                    agent=agent,
                    reason="queue_full",
                )
            )
            return False
        self.start()
        return True

    def reset_round_budget(self) -> None:
        self._budget.reset_round()

    # ── Internals ──────────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._stopped:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                async with self._sema:
                    await self._process(job)
            except Exception as exc:  # pragma: no cover — defensive
                logger.exception("tts worker: job failed: %s", exc)
                await bus.emit(
                    "agent.speech_audio_dropped",
                    agent=job.agent,
                    reason=f"error:{type(exc).__name__}",
                )
            finally:
                self._queue.task_done()

    async def _process(self, job: _SpeechJob) -> None:
        drop_reason = self._budget.try_consume(len(job.text))
        if drop_reason:
            await bus.emit(
                "agent.speech_audio_dropped",
                agent=job.agent,
                reason=drop_reason,
            )
            return

        counter = self._counters.setdefault(job.agent, _AgentSpeechCounter())
        counter.seq += 1
        seq = counter.seq

        await bus.emit(
            "agent.speech_audio_start",
            agent=job.agent,
            seq=seq,
            voice=job.voice,
            mood=job.mood,
            mime="audio/mpeg",
        )

        total = 0
        index = 0
        try:
            async for chunk in self._client.synthesize(
                text=job.text, voice=job.voice, mood=job.mood, language=job.language,
            ):
                if not chunk:
                    continue
                total += len(chunk)
                await bus.emit(
                    "agent.speech_audio_chunk",
                    agent=job.agent,
                    seq=seq,
                    index=index,
                    b64=base64.b64encode(chunk).decode("ascii"),
                )
                index += 1
        except TTSError as exc:
            logger.warning("tts synth failed for %s: %s", job.agent, exc)
            await bus.emit(
                "agent.speech_audio_dropped",
                agent=job.agent,
                reason=f"tts_error:{exc}",
            )
            return
        except Exception as exc:  # pragma: no cover — network flake etc.
            logger.warning("tts synth error for %s: %s", job.agent, exc)
            await bus.emit(
                "agent.speech_audio_dropped",
                agent=job.agent,
                reason=f"error:{type(exc).__name__}",
            )
            return

        await bus.emit(
            "agent.speech_audio_end",
            agent=job.agent,
            seq=seq,
            total_bytes=total,
        )


# ── Module-level convenience ──────────────────────────────────────────

# Current swarm always runs one session in-process. If that ever becomes
# "multiple rooms per server", move this to a per-session attribute on
# RoomState (Phase 4 will do exactly this).
_default_worker: TTSWorker | None = None


def get_default_worker() -> TTSWorker:
    global _default_worker
    if _default_worker is None:
        _default_worker = TTSWorker()
    return _default_worker


def shutdown_default_worker() -> asyncio.Task | None:
    global _default_worker
    if _default_worker is None:
        return None
    task = asyncio.create_task(_default_worker.stop())
    _default_worker = None
    return task
