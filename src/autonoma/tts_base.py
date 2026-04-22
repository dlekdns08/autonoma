"""Shared TTS types — base client + error.

Split out from ``autonoma.tts`` so ``autonoma.tts_omnivoice`` can import
without dragging in the factory (which in turn imports OmniVoice — a
heavy dep we want lazy). The factory lives in ``autonoma.tts``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


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
