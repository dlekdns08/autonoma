"""Shared TTS types — base client + error.

Split out from ``autonoma.tts`` so ``autonoma.tts_omnivoice`` can import
without dragging in the factory (which in turn imports OmniVoice — a
heavy dep we want lazy). The factory lives in ``autonoma.tts``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)


def trim_ref_cache(cache_dir: Path, keep: int = 10) -> None:
    """Keep only the ``keep`` most-recently-modified files in the cache.

    Voice profiles are typically a small fixed set (one file per
    profile_id × MIME extension), so the cache rarely outgrows the cap
    in steady state. But profile deletes and MIME changes leave stale
    entries behind; without a trim, a long-lived process slowly
    accumulates them. Both TTS backends call this on every ref-audio
    write so cleanup is amortised across normal usage — no background
    task needed.

    Errors are logged but never propagated: a failed unlink during
    cleanup must not bubble up into the synthesis path.
    """
    try:
        files = sorted(
            (p for p in cache_dir.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("[tts] ref cache scan failed at %s: %s", cache_dir, exc)
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError as exc:
            logger.warning("[tts] ref cache unlink failed for %s: %s", stale, exc)


__all__ = ["BaseTTSClient", "TTSError", "trim_ref_cache"]


class TTSError(Exception):
    """Base class for provider-raised TTS errors."""


class BaseTTSClient(ABC):
    """Streaming TTS contract: yields audio-bytes chunks."""

    @abstractmethod
    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        mood: str = "",
        language: str = "ko",
        ref_audio: bytes | None = None,
        ref_audio_mime: str = "audio/wav",
        ref_text: str = "",
    ) -> AsyncIterator[bytes]:
        """Yield successive audio bytes for ``text`` spoken as ``voice``.

        ``voice`` semantics depend on the backend:
        - OmniVoice: voice_profile uuid; caller also passes ``ref_audio`` +
          ``ref_text`` resolved from the profile.
        - Stub: ignored; emits nothing but still honours the start/end
          event contract.
        """
        ...
