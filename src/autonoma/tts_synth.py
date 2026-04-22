"""Shared synthesis helpers for OmniVoice.

Both ``/api/voice-profiles/*/test`` (one-shot) and the ``tts_worker``
(streaming) go through here so concurrency, timing metrics, and error
classification are handled in one place.

The semaphore caps how many synthesis jobs run at once in this process.
OmniVoice holds the model in a single shared client; parallel calls
thrash CPU caches + contend for the GIL around numpy/torch ops. One
concurrent job per process yields the best throughput for utterance-
level requests (a long request blocks short ones briefly, but total
wall-clock is still shorter than interleaving).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from autonoma.tts_base import BaseTTSClient, TTSError

logger = logging.getLogger(__name__)


# Process-wide cap on concurrent OmniVoice synthesis jobs. Exposed at
# module scope so tests can monkeypatch ``synth_semaphore`` to a larger
# value if they want to exercise the concurrency path.
synth_semaphore = asyncio.Semaphore(1)


@dataclass
class SynthResult:
    audio: bytes
    duration_ms: int
    chunk_count: int


async def synthesize_collected(
    client: BaseTTSClient,
    *,
    text: str,
    voice: str,
    ref_audio: bytes,
    ref_audio_mime: str,
    ref_text: str,
    mood: str = "",
    language: str = "ko",
) -> SynthResult:
    """Run a full synthesis and return the concatenated WAV bytes.

    Wraps the underlying streaming iterator under the process semaphore
    and records timing. Used by the /test endpoint where we send back a
    single ``audio/wav`` response (no streaming contract to clients).
    """
    async with synth_semaphore:
        started = time.monotonic()
        buf = bytearray()
        chunks = 0
        async for chunk in client.synthesize(
            text=text,
            voice=voice,
            mood=mood,
            language=language,
            ref_audio=ref_audio,
            ref_audio_mime=ref_audio_mime,
            ref_text=ref_text,
        ):
            if not chunk:
                continue
            buf.extend(chunk)
            chunks += 1
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "[tts] synth ok voice=%s text_len=%d bytes=%d chunks=%d elapsed_ms=%d",
            voice,
            len(text),
            len(buf),
            chunks,
            elapsed_ms,
        )
        return SynthResult(audio=bytes(buf), duration_ms=elapsed_ms, chunk_count=chunks)


async def synthesize_streaming(
    client: BaseTTSClient,
    *,
    text: str,
    voice: str,
    ref_audio: bytes | None,
    ref_audio_mime: str,
    ref_text: str,
    mood: str = "",
    language: str = "ko",
) -> AsyncIterator[bytes]:
    """Streaming variant for the worker pipeline. Yields chunks under the
    same semaphore so /test and /run share one concurrency budget.

    Unlike ``synthesize_collected`` this surfaces ``TTSError`` to the
    caller — the worker wants to translate those into event-bus signals,
    not collapse them into a single failure code.
    """
    async with synth_semaphore:
        started = time.monotonic()
        chunks = 0
        total = 0
        try:
            async for chunk in client.synthesize(
                text=text,
                voice=voice,
                mood=mood,
                language=language,
                ref_audio=ref_audio,
                ref_audio_mime=ref_audio_mime,
                ref_text=ref_text,
            ):
                if not chunk:
                    continue
                chunks += 1
                total += len(chunk)
                yield chunk
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "[tts] stream ok voice=%s text_len=%d bytes=%d chunks=%d elapsed_ms=%d",
                voice,
                len(text),
                total,
                chunks,
                elapsed_ms,
            )


def classify_synth_error(exc: BaseException) -> tuple[str, str]:
    """Map a raised exception to (code, user_message).

    Used by the /test endpoint to produce structured 503 responses the
    admin UI can branch on without string-matching the python repr.
    """
    if isinstance(exc, TTSError):
        msg = str(exc)
        if "missing ref_audio" in msg or "missing ref_text" in msg:
            return ("missing_reference", "레퍼런스 오디오/대본이 설정되지 않았습니다.")
        if "not installed" in msg:
            return ("omnivoice_missing", "서버에 OmniVoice 패키지가 설치되지 않았습니다.")
        return ("tts_error", "음성 합성 중 오류가 발생했습니다.")
    name = type(exc).__name__
    if name == "OutOfMemoryError" or "CUDA out of memory" in str(exc):
        return ("out_of_memory", "모델 메모리가 부족합니다. 잠시 후 다시 시도해 주세요.")
    if name in ("TimeoutError", "asyncio.TimeoutError"):
        return ("timeout", "음성 합성이 시간 내에 끝나지 않았습니다.")
    return ("internal_error", "음성 합성에 실패했습니다. 서버 로그를 확인해 주세요.")


__all__ = [
    "SynthResult",
    "classify_synth_error",
    "synth_semaphore",
    "synthesize_collected",
    "synthesize_streaming",
]
