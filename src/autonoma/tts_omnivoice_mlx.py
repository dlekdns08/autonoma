"""TTS via MLX — Apple-Silicon-native inference path.

Wraps any ``mlx_audio``-compatible TTS model behind the project's
``BaseTTSClient`` contract. Currently defaults to
``mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`` — the bf16 build
matches the architecture mlx_audio expects out of the box. We
tried the 4-bit fork (``aufklarer/Qwen3-TTS-12Hz-1.7B-Base-MLX-4bit``)
first for the memory/speed win, but its ``speaker_encoder.fc``
shape diverges from upstream (2048 vs the expected 1024) and
load_model rejects it with a ValueError. We chose Qwen3-TTS over
mlx-community/OmniVoice-bf16 because that OmniVoice conversion is
missing the HiggsAudioTokenizer required for voice cloning, while
Qwen3-TTS ships its full inference path in the same checkpoint.

Roughly 1.5–3× faster than the PyTorch+MPS path on M-series Macs
and uses noticeably less memory (bf16 weights), at the cost of a
separate operator-side install (already wired via the
``omnivoice-mlx`` extra in pyproject):

    uv sync --extra tts --extra omnivoice-mlx

Selected at runtime via ``AUTONOMA_TTS_PROVIDER=omnivoice-mlx``. The
PyTorch ``omnivoice`` provider stays in place for fallback.

Inference contract (from the model-card example we follow):

    from mlx_audio.tts.utils import load_model
    model = load_model("mlx-community/Qwen3-TTS-...-bf16")
    results = list(model.generate(
        text="...",
        ref_audio="path_to_ref.wav",
        ref_text="레퍼런스 오디오의 전사 텍스트",
    ))
    audio = results[0].audio  # mx.array, 24 kHz mono float32

We call ``model.generate`` directly (not the higher-level
``generate_audio`` helper) so we get the raw mx.array back, convert
to a 16-bit WAV in memory, and yield it as a single chunk to the
worker — no temp files, no STT round-trip on every call.

The class name is kept as ``OmniVoiceMlxClient`` for backwards
compatibility with the factory's ``omnivoice-mlx`` provider key,
even though the default model is now Qwen3-TTS. Changing the
provider name would force every operator to re-edit ``.env``.
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

DEFAULT_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
DEFAULT_SAMPLE_RATE = 24_000  # Qwen3-TTS-12Hz emits 24 kHz mono


def _float32_to_wav(samples: Any, sample_rate: int) -> bytes:
    """16-bit PCM WAV from a 1-D float32 array in [-1, 1].

    Same encoder shape we use in ``tts_vibevoice`` — kept local to
    this module so the MLX backend has no cross-backend imports.
    """
    import io
    import wave

    import numpy as np  # type: ignore[import-not-found]

    clamped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clamped * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()

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
            import mlx.core as mx  # type: ignore[import-not-found]
        except ImportError as exc:
            self._load_error = (
                "mlx_audio package not installed. Run:\n"
                "  uv pip install --python .venv/bin/python mlx mlx-lm mlx-audio"
            )
            logger.error("[tts/omnivoice-mlx] %s: %s", self._load_error, exc)
            return
        # Pin the default device for this thread before load_model
        # touches anything — load_model evaluates weights and that
        # eval has the same thread-local stream requirement as
        # ``model.generate`` does at inference time.
        try:
            mx.set_default_device(mx.gpu)
        except Exception:
            try:
                mx.set_default_device(mx.cpu)
            except Exception:
                pass
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
        """Direct ``model.generate`` call — bypass ``generate_audio``.

        The ``mlx_audio.tts.generate.generate_audio`` helper handles
        file IO + a built-in whisper STT step that we don't want
        (we already have ref_text in the voice profile, and writing
        to disk just to read it back wastes time). The model's own
        ``generate`` method returns an mx.array generator we can
        convert to WAV bytes in memory.

        Reference call from the Qwen3-TTS model card:

            results = list(model.generate(
                text="...",
                ref_audio="path/to/ref.wav",
                ref_text="레퍼런스 오디오의 전사 텍스트",
            ))
            audio = results[0].audio  # mx.array, 24 kHz mono float32
        """
        import time

        # Spill the reference audio to a temp file the model can open
        # by path. Skipped when the caller has no profile attached —
        # the model then uses its default voice.
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

        t0 = time.perf_counter()
        try:
            # MLX streams are *thread-local*. ``asyncio.to_thread`` ran
            # us on a worker thread that has no default stream — calls
            # like ``mx.eval(ref_codes)`` deep inside the model raise
            # ``RuntimeError: There is no Stream(gpu, 0) in current
            # thread`` until we attach one explicitly. Setting the
            # default device on this thread + wrapping the generate
            # call in ``mx.stream(mx.gpu)`` gives mlx_audio a GPU
            # stream to dispatch on. ``set_default_device`` is a
            # cheap idempotent op so re-running on every synth is
            # fine.
            import mlx.core as mx  # type: ignore[import-not-found]

            try:
                mx.set_default_device(mx.gpu)
            except Exception as exc:
                # On a Mac without Metal (very rare on the deploy
                # target) we'd fall through here. Continue with cpu —
                # mlx_audio still works on CPU just slower.
                logger.warning(
                    "[tts/omnivoice-mlx] mx.set_default_device(gpu) failed: %s; "
                    "trying cpu", exc,
                )
                try:
                    mx.set_default_device(mx.cpu)
                except Exception:
                    pass

            kwargs: dict[str, Any] = {"text": text}
            if ref_path is not None:
                kwargs["ref_audio"] = ref_path
            if ref_text:
                kwargs["ref_text"] = ref_text

            # ``model.generate`` is a generator; collect everything
            # into a list so we get the full utterance up front. For
            # short text (single line agent / podcast turn) this is
            # one or two yields and a few-hundred-ms wall clock.
            try:
                with mx.stream(mx.gpu):
                    results = list(self._model.generate(**kwargs))
            except ValueError as exc:
                raise TTSError(f"mlx model.generate failed: {exc}") from exc
            except TypeError as exc:
                # The Qwen3-TTS family takes the kwargs above; older
                # OmniVoice-style models didn't. If we hit a kwarg
                # mismatch fall through to a positional + minimal
                # call so the caller still gets *something* back.
                logger.warning(
                    "[tts/omnivoice-mlx] generate kwargs rejected (%s); "
                    "retrying with text-only call", exc,
                )
                try:
                    with mx.stream(mx.gpu):
                        results = list(self._model.generate(text))
                except Exception as exc2:
                    raise TTSError(
                        f"mlx model.generate failed (retry): {exc2}"
                    ) from exc2

            if not results:
                logger.warning(
                    "[tts/omnivoice-mlx] generate produced no results"
                )
                return b""

            # Each result is a small object with an ``.audio``
            # attribute that's an mx.array of float32 samples at the
            # model's native sample rate (24 kHz for Qwen3-TTS-12Hz).
            # Concatenate across yields so multi-segment utterances
            # come out as one WAV.
            import numpy as np

            audio_chunks: list[Any] = []
            for r in results:
                audio = getattr(r, "audio", None)
                if audio is None:
                    # Some builds return raw mx.array directly.
                    audio = r
                if hasattr(audio, "shape"):
                    np_chunk = np.array(audio)
                else:
                    np_chunk = np.asarray(audio, dtype=np.float32)
                # Squeeze leading batch / channel dim so chunks
                # concat cleanly along the sample axis.
                while np_chunk.ndim > 1 and np_chunk.shape[0] == 1:
                    np_chunk = np_chunk[0]
                if np_chunk.ndim > 1:
                    # Multichannel → mono by mean.
                    np_chunk = np_chunk.mean(axis=0)
                audio_chunks.append(np_chunk.astype(np.float32))

            if not audio_chunks:
                return b""
            np_audio = np.concatenate(audio_chunks, axis=0)
            sr = getattr(settings, "vibevoice_sample_rate", 0) or DEFAULT_SAMPLE_RATE
            wav_bytes = _float32_to_wav(np_audio, sr)

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "[tts/omnivoice-mlx] synth ok text_len=%d samples=%d bytes=%d sr=%d ms=%d",
                len(text),
                np_audio.shape[0],
                len(wav_bytes),
                sr,
                elapsed_ms,
            )
            return wav_bytes
        finally:
            if ref_path:
                try:
                    os.unlink(ref_path)
                except OSError:
                    pass
