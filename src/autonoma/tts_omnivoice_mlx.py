"""OmniVoice TTS via MLX — Apple-Silicon-native inference path.

Uses ``mlx-community/OmniVoice-bf16`` through the ``mlx_audio`` helper
package, which is a pure-MLX rewrite of OmniVoice's PyTorch inference.
Roughly 1.5–3× faster than the PyTorch+MPS path on M-series Macs and
uses noticeably less memory (bf16 weights), at the cost of a separate
operator-side install:

    uv pip install --python .venv/bin/python mlx mlx-lm mlx-audio

Selected at runtime via ``AUTONOMA_TTS_PROVIDER=omnivoice-mlx``. The
original ``omnivoice`` provider stays in place for fallback.

Inference contract (from the model card example):

    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.generate import generate_audio
    model = load_model("mlx-community/OmniVoice-bf16")
    generate_audio(
        model=model,
        text="...",
        ref_audio="path_to_ref.wav",
        file_prefix="out",
    )

``generate_audio`` writes a WAV (or several) to disk under
``file_prefix``. We feed it a private temp directory, then read the
resulting WAV bytes back so the rest of the pipeline (event bus,
worker, browser playback) doesn't need to know it came from a file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any, AsyncIterator

from autonoma.config import settings
from autonoma.tts_base import BaseTTSClient, TTSError

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "mlx-community/OmniVoice-bf16"

_shared_client: "OmniVoiceMlxClient | None" = None


def get_shared_client() -> "OmniVoiceMlxClient":
    global _shared_client
    if _shared_client is None:
        _shared_client = OmniVoiceMlxClient(
            model_id=getattr(settings, "omnivoice_mlx_model_id", "")
            or DEFAULT_MODEL_ID,
        )
    return _shared_client


def shared_client_status() -> dict[str, Any]:
    """Cheap snapshot for /api/health. Avoids triggering a model load."""
    if _shared_client is None:
        return {"loaded": False, "device": "", "dtype": ""}
    return {
        "loaded": _shared_client.is_loaded(),
        "device": "mlx",  # mlx_audio always targets the system's MLX device
        "dtype": "bfloat16",
    }


async def warmup_shared_client() -> None:
    """Pre-load the MLX model on FastAPI startup so the first /test
    or agent utterance doesn't pay the multi-second model-load cost
    in front of nginx's read timeout. Mirrors
    ``tts_omnivoice.warmup_shared_client``.
    """
    client = get_shared_client()
    try:
        await client._ensure_model()
        logger.info("[tts] OmniVoice-MLX warm-load complete (model=%s)", client.model_id)
    except TTSError as exc:
        logger.warning("[tts] OmniVoice-MLX warm-load skipped: %s", exc)
    except Exception:  # pragma: no cover — startup path
        logger.exception("[tts] OmniVoice-MLX warm-load failed")


class OmniVoiceMlxClient(BaseTTSClient):
    """Streaming TTS client backed by ``mlx_audio``."""

    def __init__(self, model_id: str = DEFAULT_MODEL_ID) -> None:
        self.model_id = model_id
        self._model: Any = None
        self._load_lock = asyncio.Lock()
        # ``mlx_audio.generate_audio`` is not documented as thread-safe
        # and the underlying MLX ops aren't reentrant on a single
        # device. Serialise calls.
        self._gen_lock = asyncio.Lock()
        self._load_error: str | None = None

    def is_loaded(self) -> bool:
        return self._model is not None

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            raise TTSError(self._load_error)
        async with self._load_lock:
            if self._model is not None:
                return self._model
            await asyncio.to_thread(self._load_blocking)
            if self._load_error is not None:
                raise TTSError(self._load_error)
            return self._model

    def _load_blocking(self) -> None:
        try:
            from mlx_audio.tts.utils import load_model  # type: ignore[import-not-found]
        except ImportError as exc:
            self._load_error = (
                "mlx_audio package not installed. Run:\n"
                "  uv pip install --python .venv/bin/python mlx mlx-lm mlx-audio"
            )
            logger.error("[tts/omnivoice-mlx] %s: %s", self._load_error, exc)
            return
        try:
            logger.info("[tts/omnivoice-mlx] loading %s …", self.model_id)
            self._model = load_model(self.model_id)
            logger.info("[tts/omnivoice-mlx] %s loaded", self.model_id)
        except Exception as exc:
            self._load_error = f"OmniVoice MLX load failed: {exc}"
            logger.exception("[tts/omnivoice-mlx] load failed")

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
        if not text or not text.strip():
            return
        await self._ensure_model()
        async with self._gen_lock:
            wav_bytes = await asyncio.to_thread(
                self._run_inference,
                text=text,
                ref_audio=ref_audio,
                ref_audio_mime=ref_audio_mime,
                ref_text=ref_text,
                language=language,
            )
        if not wav_bytes:
            return
        # mlx_audio returns the entire utterance at once. Yield as a
        # single chunk — the worker's audio-start/chunk/end event
        # contract holds regardless of chunk count.
        yield wav_bytes

    def _run_inference(
        self,
        *,
        text: str,
        ref_audio: bytes | None,
        ref_audio_mime: str,
        ref_text: str = "",
        language: str = "ko",
    ) -> bytes:
        """Bridge to ``mlx_audio.tts.generate.generate_audio``.

        Inspected signature (mlx-audio 0.4.3):

            generate_audio(
                text: str,
                model: Module | str | None = None,
                max_tokens: int = 1200,
                voice: str = 'af_heart',
                ref_audio: str | None = None,
                ref_text: str | None = None,
                stt_model: str | Module | None = 'mlx-community/whisper-...',
                output_path: str | None = None,
                file_prefix: str = 'audio',
                audio_format: str = 'wav',
                lang_code: str = 'en',
                save: bool = False,        # ← critical: defaults to NOT writing files
                ...
            )

        Three quirks the docs didn't telegraph and we have to handle:

        1. ``save=False`` is the default. Without ``save=True`` no file
           is ever written, regardless of ``output_path`` /
           ``file_prefix``. Our caller wants WAV bytes back, so we
           have to flip it.
        2. ``ref_text`` lets us bypass the built-in STT step. Voice
           profiles already carry the transcript, so passing it makes
           the call deterministic and skips the multi-second whisper
           round trip.
        3. ``lang_code`` defaults to English. We pass through the
           caller's language so Korean / Japanese profiles get the
           right tokenizer.
        """
        from mlx_audio.tts.generate import generate_audio  # type: ignore[import-not-found]

        # Spill the reference audio to a temp file ``mlx_audio`` can
        # open by path. Ref-audio guides voice cloning; if the caller
        # didn't supply one (only the swarm's stub flow does that)
        # we pass ``None`` and let the model use its default voice.
        ref_path: str | None = None
        if ref_audio:
            suffix = ".wav"
            if "ogg" in ref_audio_mime:
                suffix = ".ogg"
            elif "mp3" in ref_audio_mime or "mpeg" in ref_audio_mime:
                suffix = ".mp3"
            elif "webm" in ref_audio_mime:
                suffix = ".webm"
            with tempfile.NamedTemporaryFile(
                prefix="autonoma_mlx_ref_", suffix=suffix, delete=False
            ) as tf:
                tf.write(ref_audio)
                ref_path = tf.name

        try:
            with tempfile.TemporaryDirectory(prefix="autonoma_mlx_out_") as outdir:
                output_path = os.path.join(outdir, "out.wav")
                kwargs: dict[str, Any] = {
                    "text": text,
                    "model": self._model,
                    "output_path": output_path,
                    "file_prefix": "out",
                    "audio_format": "wav",
                    "lang_code": language or "en",
                    # ``save=True`` is mandatory — without it
                    # generate_audio runs inference, throws away the
                    # tensor, and returns ``None`` with no file written.
                    "save": True,
                    # Quiet the per-call progress logging; we already
                    # log start/end ourselves.
                    "verbose": False,
                }
                if ref_path is not None:
                    kwargs["ref_audio"] = ref_path
                # ``ref_text`` makes the synth deterministic and skips
                # the built-in whisper STT step. The processor accepts
                # an empty string as "use STT instead" so we only set
                # it when the caller actually has the transcript.
                if ref_text:
                    kwargs["ref_text"] = ref_text
                generate_audio(**kwargs)

                # ``output_path`` is what we asked for, but mlx_audio
                # also occasionally emits ``{file_prefix}_NNN.wav`` for
                # chunked output. Prefer the explicit path, fall back
                # to a glob.
                if os.path.exists(output_path):
                    with open(output_path, "rb") as f:
                        wav_bytes = f.read()
                else:
                    wav_paths = sorted(
                        os.path.join(outdir, n)
                        for n in os.listdir(outdir)
                        if n.lower().endswith(".wav")
                    )
                    if not wav_paths:
                        logger.warning(
                            "[tts/omnivoice-mlx] generate_audio produced no "
                            "WAV files under %s",
                            outdir,
                        )
                        return b""
                    with open(wav_paths[0], "rb") as f:
                        wav_bytes = f.read()
                logger.info(
                    "[tts/omnivoice-mlx] synth ok text_len=%d bytes=%d",
                    len(text),
                    len(wav_bytes),
                )
                return wav_bytes
        finally:
            if ref_path:
                try:
                    os.unlink(ref_path)
                except OSError:
                    pass
