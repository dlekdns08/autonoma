"""TTS provider abstraction.

All speech that goes through ``agent._say`` is fanned out through this
module. Providers share a tiny async interface, so adding ElevenLabs or
a local model later is just another subclass.

The public surface is kept narrow on purpose:

    client = create_tts_client(config)
    async for chunk in client.synthesize(text=..., voice=..., style="happy"):
        await send_chunk(chunk)

``chunk`` is raw audio bytes (usually mp3 or webm/opus) — the browser
feeds them through MediaSource + an AnalyserNode to drive lipsync.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Literal

logger = logging.getLogger(__name__)


# ── Voice pools (deterministic per-character selection) ───────────────

# Azure neural voice names. Kept small — the goal is variety per
# character, not comprehensive coverage. Swap/extend as product taste
# evolves. Keys match ``tts_default_language``.
AZURE_VOICE_POOL: dict[str, list[str]] = {
    "ko": [
        "ko-KR-SunHiNeural",
        "ko-KR-InJoonNeural",
        "ko-KR-BongJinNeural",
        "ko-KR-GookMinNeural",
        "ko-KR-JiMinNeural",
        "ko-KR-SeoHyeonNeural",
        "ko-KR-SoonBokNeural",
        "ko-KR-YuJinNeural",
    ],
    "en": [
        "en-US-JennyNeural",
        "en-US-GuyNeural",
        "en-US-AriaNeural",
        "en-US-DavisNeural",
        "en-US-AmberNeural",
        "en-US-AnaNeural",
        "en-US-BrandonNeural",
        "en-US-ChristopherNeural",
    ],
}

# OpenAI TTS voices. The SDK exposes a fixed catalog; picking one per
# character keeps the "same agent → same voice" invariant for free.
OPENAI_VOICE_POOL: list[str] = [
    "alloy", "echo", "fable", "onyx", "nova", "shimmer",
]


# Mood → Azure SSML "express-as" style. Unlisted moods fall back to the
# provider default. Free to tune per product feel.
MOOD_TO_AZURE_STYLE: dict[str, str] = {
    "happy": "cheerful",
    "excited": "excited",
    "frustrated": "angry",
    "worried": "sad",
    "tired": "gentle",
    "proud": "hopeful",
    "nostalgic": "gentle",
    "inspired": "hopeful",
    "curious": "friendly",
    "determined": "serious",
    "relaxed": "calm",
    "focused": "serious",
    "mischievous": "cheerful",
}


def pick_voice_for(seed_hash: str, provider: str, language: str = "ko") -> str:
    """Deterministic voice assignment from an agent's seed hash.

    The same agent character always maps to the same voice string, so a
    viewer who hears "Zara" speak in run N hears the same voice in run
    N+1. We use a secondary md5 round on the seed to avoid aliasing with
    the personality hash (different selection space).
    """
    salt = f"voice::{provider}::{language}::{seed_hash}"
    digest = hashlib.md5(salt.encode()).digest()
    idx = int.from_bytes(digest[:4], "big")
    if provider == "azure":
        pool = AZURE_VOICE_POOL.get(language, AZURE_VOICE_POOL["en"])
        return pool[idx % len(pool)]
    if provider == "openai":
        return OPENAI_VOICE_POOL[idx % len(OPENAI_VOICE_POOL)]
    # ``none`` / unknown: return a synthetic token. The stub provider
    # echoes it back; it's never shown to the user.
    return f"stub-{idx % 8}"


# ── Config & errors ───────────────────────────────────────────────────


@dataclass
class TTSConfig:
    provider: Literal["azure", "openai", "none"] = "none"
    azure_key: str = ""
    azure_region: str = ""
    openai_api_key: str = ""


class TTSError(Exception):
    """Base class for provider-raised TTS errors."""


# ── Base client ───────────────────────────────────────────────────────


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
    ) -> AsyncIterator[bytes]:
        """Yield successive audio bytes for ``text`` spoken by ``voice``."""
        ...


# ── Stub / dev provider ───────────────────────────────────────────────


class StubTTSClient(BaseTTSClient):
    """No-op provider. Emits a single empty chunk so the downstream
    pipeline still fires start/end events — useful while wiring the
    browser side without burning API credits."""

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        mood: str = "",
        language: str = "ko",
    ) -> AsyncIterator[bytes]:
        # Sleep proportional to text length so timing behaves like a real
        # provider (UI animations can key off duration).
        await asyncio.sleep(min(0.6, 0.03 * len(text)))
        # Yield nothing — the browser treats this as silence but still
        # gets ``start`` / ``end`` events.
        if False:
            yield b""


# ── Azure Neural TTS ──────────────────────────────────────────────────


class AzureTTSClient(BaseTTSClient):
    """Azure Cognitive Services — Neural TTS over HTTP REST.

    We do the REST dance ourselves (no ``azure-cognitiveservices-speech``
    dep): one token fetch, then POST SSML to ``/tts``. Audio comes back
    as mp3 in a single response; we chunk it into ~8KB blocks to let the
    browser start decoding before the full buffer arrives.
    """

    def __init__(self, key: str, region: str) -> None:
        if not key or not region:
            raise TTSError("azure key + region required")
        self._key = key
        self._region = region
        self._token: str = ""
        self._token_lock = asyncio.Lock()

    async def _get_token(self) -> str:
        # Azure tokens last 10 minutes; we just refetch lazily rather
        # than tracking expiry. Good enough for the request rate we
        # expect (seconds-apart, not millis-apart).
        async with self._token_lock:
            if self._token:
                return self._token
            import aiohttp  # lazy; not a hard dep
            url = f"https://{self._region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers={"Ocp-Apim-Subscription-Key": self._key}) as resp:
                    resp.raise_for_status()
                    self._token = await resp.text()
            return self._token

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        mood: str = "",
        language: str = "ko",
    ) -> AsyncIterator[bytes]:
        import aiohttp  # lazy import
        token = await self._get_token()
        ssml = _build_azure_ssml(text=text, voice=voice, mood=mood, language=language)
        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "autonoma",
        }
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, headers=headers, data=ssml.encode("utf-8")) as resp:
                if resp.status == 401:
                    # Token expired mid-flight; invalidate and bubble up.
                    self._token = ""
                    raise TTSError("azure auth failed (retry)")
                resp.raise_for_status()
                async for chunk in resp.content.iter_chunked(8192):
                    yield chunk


def _build_azure_ssml(*, text: str, voice: str, mood: str, language: str) -> str:
    """Construct SSML with <mstts:express-as> when we have a mood → style."""
    safe_text = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    style = MOOD_TO_AZURE_STYLE.get(mood, "")
    lang_code = "ko-KR" if language == "ko" else "en-US"
    if style:
        body = (
            f'<mstts:express-as style="{style}">{safe_text}</mstts:express-as>'
        )
    else:
        body = safe_text
    return (
        f'<speak version="1.0" xml:lang="{lang_code}" '
        f'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="http://www.w3.org/2001/mstts">'
        f'<voice name="{voice}">{body}</voice></speak>'
    )


# ── OpenAI TTS ────────────────────────────────────────────────────────


class OpenAITTSClient(BaseTTSClient):
    """OpenAI TTS via the official async SDK.

    Doesn't support mood/style directly — mood is ignored. Good cheap
    fallback when you just want *a voice* and don't care about emotion.
    """

    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI
        if not api_key:
            raise TTSError("openai api key required")
        self._client = AsyncOpenAI(api_key=api_key)

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        mood: str = "",
        language: str = "ko",
    ) -> AsyncIterator[bytes]:
        # The async streaming context manager yields the full response;
        # we pull bytes in chunks.
        async with self._client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3",
        ) as resp:
            async for chunk in resp.iter_bytes(chunk_size=8192):
                yield chunk


# ── Factory ───────────────────────────────────────────────────────────


def create_tts_client(cfg: TTSConfig) -> BaseTTSClient:
    """Instantiate the right TTS client. Falls back to stub on bad config."""
    if cfg.provider == "azure":
        try:
            return AzureTTSClient(key=cfg.azure_key, region=cfg.azure_region)
        except TTSError as exc:
            logger.warning("azure tts init failed, falling back to stub: %s", exc)
            return StubTTSClient()
    if cfg.provider == "openai":
        try:
            return OpenAITTSClient(api_key=cfg.openai_api_key)
        except TTSError as exc:
            logger.warning("openai tts init failed, falling back to stub: %s", exc)
            return StubTTSClient()
    return StubTTSClient()


def tts_config_from_settings() -> TTSConfig:
    from autonoma.config import settings
    return TTSConfig(
        provider=settings.tts_provider,
        azure_key=settings.tts_azure_key,
        azure_region=settings.tts_azure_region,
        openai_api_key=settings.openai_api_key,
    )
