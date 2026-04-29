"""Session-scoped TTS worker: budget + rate limit + fan-out to the WS.

Why a worker instead of synthesizing inline?

- ``agent._say`` fires from a tight agent loop; we don't want it to
  block on OmniVoice inference (hundreds of ms on MPS, multi-second
  on CPU). A worker lets us fire-and-forget.
- A small in-process queue gives us one place to enforce budgets and
  rate limits so a misbehaving agent can't monopolise the GPU.
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


MAX_TEXT_CHARS = 2000      # hard ceiling per utterance (safety rail).
                           # Raised from 500 after OmniVoice perf tuning
                           # made long-form synthesis feasible — 500
                           # silently truncated the tail of anything
                           # longer than ~3-4 sentences. OmniVoice's
                           # internal chunker (audio_chunk_duration)
                           # handles long text natively; this ceiling
                           # is here only to cap obviously-runaway
                           # input (e.g. agent emitting an entire wiki
                           # page).
MAX_QUEUE_DEPTH = 64       # drop new jobs once the queue is this long
SYNTH_CONCURRENCY = 2      # how many parallel synth HTTP calls allowed


@dataclass
class _SpeechJob:
    agent: str
    text: str
    voice: str
    mood: str
    language: str
    # OmniVoice zero-shot path: profile_id is carried in ``voice``; the
    # worker resolves it to (ref_audio, ref_text) at pop-time so a
    # profile update picks up on the next utterance without restarting
    # the worker. Empty when no binding exists (worker drops the job).
    vrm_file: str = ""


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
        # Bus listener for cross-worker barge-in (feature #2). Stored so
        # ``stop()`` can ``bus.off`` it during shutdown — otherwise the
        # listener leaks across test boots that re-instantiate the
        # worker without tearing down the bus.
        self._cancel_listener: Any = None
        # Track fire-and-forget ``bus.emit`` tasks. Without a strong
        # reference, ``asyncio.create_task`` results are eligible for
        # GC mid-execution per CPython's task lifecycle docs, which
        # silently drops bus events that listeners depended on. Tasks
        # remove themselves on completion via the done-callback below.
        self._pending_emits: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="tts-worker")
        # Register the bus listener AFTER the task is created so a
        # cancel event arriving instantly (e.g. from a saved replay)
        # has a worker to act on. Idempotent — only registers once
        # per instance even across start/stop/start cycles.
        # Listen for site-wide cancel events. The handler is bound
        # to ``self`` so worker instances don't cross-cancel each
        # other's queues — but bus.on() is global, so every active
        # worker receives the event and clears its own backlog.
        # This is intentional for the PoC: a barge-in from any
        # tab/user effectively mutes the swarm.
        if self._cancel_listener is None:
            async def _on_cancel(reason: str = "interrupt", **_: Any) -> None:
                self.cancel_all(reason=reason)

            try:
                bus.on("tts.cancel", _on_cancel)
                # Only retain the reference *after* successful
                # registration so ``stop()``'s ``bus.off`` path won't
                # try to deregister a listener that never landed.
                self._cancel_listener = _on_cancel
            except Exception:
                logger.warning(
                    "[tts] failed to register tts.cancel listener; "
                    "barge-in will not drain queue",
                    exc_info=True,
                )

    async def stop(self) -> None:
        self._stopped = True
        if self._cancel_listener is not None:
            try:
                bus.off("tts.cancel", self._cancel_listener)
            except Exception:
                logger.warning("[tts] failed to deregister tts.cancel listener", exc_info=True)
            self._cancel_listener = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("[tts] worker task raised on shutdown", exc_info=True)
            self._task = None
        # Drain pending bus emits so listeners aren't left mid-await.
        # ``return_exceptions=True`` so a single emit failure doesn't
        # block the shutdown of the rest.
        if self._pending_emits:
            await asyncio.gather(*self._pending_emits, return_exceptions=True)
            self._pending_emits.clear()

    def enqueue(
        self,
        *,
        agent: str,
        text: str,
        voice: str,
        mood: str = "",
        language: str = "ko",
        vrm_file: str = "",
    ) -> bool:
        """Schedule a line. Returns False if dropped upfront.

        Drops happen for three reasons: worker stopped, queue full, or
        text too long. Budget / rate-limit are checked at pop-time (after
        waiting a moment so we can collapse bursts). Profile lookup is
        deferred to pop-time too so binding edits take effect immediately.
        """
        if self._stopped or not settings.tts_enabled:
            return False
        if not text or not text.strip():
            return False
        if len(text) > MAX_TEXT_CHARS:
            # Silent truncation used to strip the tail without any
            # trace, producing the "일부 Speech로 안 되는" class of
            # bug. Log it at WARN so the mismatch between the text
            # the agent emitted and the audio the listener hears is
            # surfaced in the API log for future diagnosis.
            logger.warning(
                "[tts] text truncated from %d to %d chars for %s — "
                "raise MAX_TEXT_CHARS if this is legitimate input",
                len(text),
                MAX_TEXT_CHARS,
                agent,
            )
            text = text[:MAX_TEXT_CHARS]
        job = _SpeechJob(
            agent=agent,
            text=text,
            voice=voice,
            mood=mood,
            language=language,
            vrm_file=vrm_file,
        )
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            logger.debug("tts queue full; dropping %r for %s", text[:40], agent)
            # Strong-reference the emit so the runtime can't collect it
            # before the bus delivers (per ``_spawn_emit`` docstring).
            self._spawn_emit(
                "agent.speech_audio_dropped",
                agent=agent,
                reason="queue_full",
            )
            return False
        self.start()
        return True

    def reset_round_budget(self) -> None:
        self._budget.reset_round()

    def _spawn_emit(self, event: str, **kwargs: Any) -> None:
        """Schedule a ``bus.emit`` and hold a strong reference until it
        completes. Replaces bare ``asyncio.create_task(bus.emit(...))``
        which the runtime can GC mid-flight when no caller holds the
        task.
        """
        task = asyncio.create_task(bus.emit(event, **kwargs))
        self._pending_emits.add(task)
        task.add_done_callback(self._pending_emits.discard)

    def cancel_all(self, *, reason: str = "interrupt") -> int:
        """Drain pending speech jobs without stopping the worker — feature #2.

        Returns the number of jobs actually dropped (handy for logging
        and tests). The currently-streaming utterance, if any, is NOT
        cancelled here — OmniVoice's ``synthesize_streaming`` is a
        synchronous generator inside a thread and yanking it mid-chunk
        risks corrupting model state on the next call. The client-side
        ``useAgentVoice.interruptAll`` already pauses playback the
        instant the user starts speaking, so the listener is silent
        regardless; this server-side drain just ensures the pending
        backlog (the next 5–10 lines) doesn't replay after the user
        finishes their interruption.
        """
        if self._stopped:
            return 0
        dropped = 0
        try:
            while True:
                job = self._queue.get_nowait()
                self._queue.task_done()
                dropped += 1
                # Tell the frontend this one was killed before synthesis
                # so the speaking flag clears cleanly. Tracked task so
                # the emit isn't GC'd mid-await.
                self._spawn_emit(
                    "agent.speech_audio_dropped",
                    agent=job.agent,
                    reason=reason,
                )
        except asyncio.QueueEmpty:
            pass
        if dropped:
            logger.info(f"[tts] cancel_all dropped {dropped} pending job(s) reason={reason}")
        return dropped

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
            # Budget drops used to be DEBUG-silent, which is how
            # sessions could burn through 800 chars and then every
            # subsequent line came back silent with no user-visible
            # cause. WARN makes this auditable from the API log.
            logger.warning(
                "[tts] dropping utterance for %s: reason=%s text_len=%d "
                "round_chars=%d session_chars=%d",
                job.agent,
                drop_reason,
                len(job.text),
                self._budget.session_round_chars,
                self._budget.session_total_chars,
            )
            await bus.emit(
                "agent.speech_audio_dropped",
                agent=job.agent,
                reason=drop_reason,
            )
            return

        # Resolve the voice profile at pop-time so edits to the binding
        # or the profile audio take effect on the next utterance without
        # restarting the worker. job.voice already carries the profile id.
        ref_audio: bytes | None = None
        ref_mime = "audio/wav"
        ref_text = ""
        if job.voice:
            from autonoma.voice import get_profile

            profile = await get_profile(job.voice)
            if profile is None:
                await bus.emit(
                    "agent.speech_audio_dropped",
                    agent=job.agent,
                    reason="profile_not_found",
                )
                return
            ref_audio = profile.ref_audio
            ref_mime = profile.ref_audio_mime
            ref_text = profile.ref_text

        counter = self._counters.setdefault(job.agent, _AgentSpeechCounter())
        counter.seq += 1
        seq = counter.seq

        await bus.emit(
            "agent.speech_audio_start",
            agent=job.agent,
            seq=seq,
            voice=job.voice,
            mood=job.mood,
            mime="audio/wav",
        )

        total = 0
        index = 0
        try:
            from autonoma.tts_synth import synthesize_streaming
            async for chunk in synthesize_streaming(
                self._client,
                text=job.text,
                voice=job.voice,
                mood=job.mood,
                language=job.language,
                ref_audio=ref_audio,
                ref_audio_mime=ref_mime,
                ref_text=ref_text,
            ):
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


# ── Per-room worker registry ──────────────────────────────────────────
#
# Keyed by room_id (== owner session_id).  One TTSWorker per active room
# so two concurrent swarms can't share a budget or a rate-limit window.
#
# ``get_default_worker()`` reads the current room_id from the ContextVar
# that ``api.py`` sets before launching a swarm task.  Agent code (base.py,
# swarm.py) calls ``get_default_worker()`` unchanged — the routing is now
# transparent.

from autonoma.context import current_session_id as _current_session_id

_workers: dict[int, TTSWorker] = {}


def get_worker(room_id: int) -> TTSWorker:
    """Return (creating if needed) the TTSWorker for *room_id*."""
    worker = _workers.get(room_id)
    if worker is None:
        worker = TTSWorker()
        _workers[room_id] = worker
    return worker


def get_default_worker() -> TTSWorker:
    """Return the worker for the current session (read from ContextVar).

    Falls back to room_id=0 when called outside a swarm context (tests,
    CLI invocations) so existing call-sites need no changes.
    """
    room_id = _current_session_id.get() or 0
    return get_worker(room_id)


def shutdown_worker(room_id: int) -> asyncio.Task | None:
    """Stop and remove the worker for *room_id*. Safe to call if absent."""
    worker = _workers.pop(room_id, None)
    if worker is None:
        return None
    return asyncio.create_task(worker.stop())


def shutdown_default_worker() -> asyncio.Task | None:
    """Backward-compat shim — shuts down the current session's worker."""
    room_id = _current_session_id.get() or 0
    return shutdown_worker(room_id)
