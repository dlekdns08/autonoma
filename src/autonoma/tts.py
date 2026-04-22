"""TTS provider abstraction — OmniVoice zero-shot cloning.

All speech that goes through ``agent._say`` is fanned out through this
module. The public surface is deliberately narrow:

    client = create_tts_client(config)
    async for chunk in client.synthesize(text=..., voice=profile_id,
                                         ref_audio=..., ref_text=...):
        await send_chunk(chunk)

``chunk`` is raw audio bytes (WAV). The browser feeds them through an
AudioElement + AnalyserNode to drive lipsync.

Azure / OpenAI TTS paths were removed in favor of self-hosted OmniVoice.
Reference audio + transcript per character are configured from the
/voice admin page and persisted in the ``voice_profiles`` /
``voice_bindings`` tables.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from autonoma.tts_base import BaseTTSClient, TTSError

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────


@dataclass
class TTSConfig:
    """Subset of settings the factory cares about. Kept tiny so tests can
    construct a client without loading the full Settings."""

    provider: Literal["omnivoice", "none"] = "none"


# ── Stub / dev provider ───────────────────────────────────────────────


class StubTTSClient(BaseTTSClient):
    """No-op provider. Emits a single empty chunk so the downstream
    pipeline still fires start/end events — useful while wiring the
    browser side without the heavy OmniVoice dep installed."""

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
        # Sleep proportional to text length so timing behaves like a real
        # provider (UI animations can key off duration).
        await asyncio.sleep(min(0.6, 0.03 * len(text)))
        if False:
            yield b""


# ── Factory ───────────────────────────────────────────────────────────


def create_tts_client(cfg: TTSConfig) -> BaseTTSClient:
    """Instantiate the right TTS client. Falls back to stub on bad config
    or missing optional deps (OmniVoice package not installed)."""
    if cfg.provider == "omnivoice":
        try:
            from autonoma.tts_omnivoice import OmniVoiceTTSClient
            return OmniVoiceTTSClient()
        except ImportError as exc:
            logger.warning(
                "omnivoice init failed, falling back to stub: %s", exc
            )
            return StubTTSClient()
    return StubTTSClient()


def tts_config_from_settings() -> TTSConfig:
    from autonoma.config import settings
    return TTSConfig(provider=settings.tts_provider)


__all__ = [
    "BaseTTSClient",
    "StubTTSClient",
    "TTSConfig",
    "TTSError",
    "create_tts_client",
    "tts_config_from_settings",
]
