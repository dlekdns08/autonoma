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

    provider: Literal["omnivoice", "vibevoice", "none"] = "none"


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
    or missing optional deps (provider package not installed)."""
    if cfg.provider == "omnivoice":
        try:
            from autonoma.tts_omnivoice import get_shared_client
            return get_shared_client()
        except ImportError as exc:
            logger.error(
                "omnivoice client import failed (%s). Falling back to "
                "StubTTSClient — all synthesis will return empty audio. "
                "Install with: pip install omnivoice torch numpy soundfile",
                exc,
            )
            return StubTTSClient()
    if cfg.provider == "vibevoice":
        # Same fallback discipline as omnivoice — surface the real
        # ImportError loudly so an operator can tell apart "bad config"
        # from "transformers/torch missing".
        try:
            from autonoma.tts_vibevoice import get_shared_client as get_vv_client
            return get_vv_client()
        except ImportError as exc:
            logger.error(
                "vibevoice client import failed (%s). Falling back to "
                "StubTTSClient — synthesis will return empty audio. "
                "Install with: uv sync --extra tts",
                exc,
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
