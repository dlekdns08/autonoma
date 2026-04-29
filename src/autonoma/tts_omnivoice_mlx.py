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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator

from autonoma.config import settings
from autonoma.tts_base import BaseTTSClient, TTSError, trim_ref_cache

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
        # Pinned single-thread executor for ALL MLX work. Load and
        # every subsequent ``model.generate`` must execute on the
        # SAME OS thread because:
        #   * MLX streams are thread-local — a fresh worker thread
        #     has no default ``Stream(gpu, 0)``.
        #   * Model parameters (mx.array) are bound to the stream
        #     they were materialised on; using them from another
        #     thread raises ``RuntimeError: There is no Stream(...)``.
        # ``asyncio.to_thread`` defaults to a multi-worker pool, so
        # successive calls would scatter across threads. Dedicating
        # one thread keeps the model + every generate on a single
        # stream and cleanly serialises the MLX ops without an
        # extra lock.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="autonoma-mlx"
        )

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
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._load_blocking)
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
        # Create + bind a stream on this thread before load_model
        # touches anything — load_model evaluates weights and that
        # eval has the same thread-local stream requirement as
        # ``model.generate`` does at inference time. ``set_default_device``
        # alone is not enough: it updates only the device pointer;
        # the worker thread still has no stream to dispatch ops on.
        try:
            stream = mx.new_stream(mx.gpu)
        except Exception as exc:
            logger.warning(
                "[tts/omnivoice-mlx] new_stream(gpu) failed: %s; trying cpu",
                exc,
            )
            try:
                stream = mx.new_stream(mx.cpu)
            except Exception as exc2:
                self._load_error = f"OmniVoice MLX stream init failed: {exc2}"
                logger.error("[tts/omnivoice-mlx] %s", self._load_error)
                return
        try:
            mx.set_default_stream(stream)
        except Exception as exc:
            logger.debug(
                "[tts/omnivoice-mlx] set_default_stream skipped: %s", exc,
            )
        try:
            logger.info("[tts/omnivoice-mlx] loading %s …", self.model_id)
            with mx.stream(stream):
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
            loop = asyncio.get_running_loop()
            wav_bytes = await loop.run_in_executor(
                self._executor,
                lambda: self._run_inference(
                    text=text,
                    voice=voice,
                    ref_audio=ref_audio,
                    ref_audio_mime=ref_audio_mime,
                    ref_text=ref_text,
                    language=language,
                ),
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
        voice: str,
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

        # Spill the reference audio onto a STABLE per-profile path the
        # model can open by name. Previously we used
        # ``tempfile.NamedTemporaryFile`` per call — but ``$TMPDIR`` on
        # macOS isn't auto-cleaned, and any path that didn't reach the
        # finally-unlink (e.g. cancellation, OS crash) leaked into
        # ``/var/folders/.../T/``. The stable path overwrites in place,
        # so re-uploads of the same profile replace the file and any
        # given inference run leaves at most one file per profile_id
        # behind on disk.
        ref_path: str | None = None
        if ref_audio:
            suffix = ".wav"
            if "ogg" in ref_audio_mime:
                suffix = ".ogg"
            elif "mp3" in ref_audio_mime or "mpeg" in ref_audio_mime:
                suffix = ".mp3"
            elif "webm" in ref_audio_mime:
                suffix = ".webm"
            cache_dir = Path(settings.data_dir) / "tts_ref_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            # Fall back to a generic name when the caller didn't pass a
            # profile id — keeps the stable-path invariant intact.
            stem = voice or "mlx_default"
            target = cache_dir / f"{stem}{suffix}"
            target.write_bytes(ref_audio)
            ref_path = str(target)
            # Keep at most ~10 most-recent ref files. The trim runs
            # AFTER write so the file we're about to hand to MLX is
            # always the freshest on disk (top of the keep list).
            trim_ref_cache(cache_dir)

        t0 = time.perf_counter()
        try:
            # MLX streams are *thread-local*. ``asyncio.to_thread`` ran
            # us on a worker thread that does NOT inherit the main
            # thread's default stream — and unlike ``set_default_device``
            # (which only updates a thread-local device pointer),
            # ``mx.stream(mx.gpu)`` tries to *look up* an existing
            # default stream on the current thread and raises
            # ``RuntimeError: There is no Stream(gpu, 0) in current
            # thread`` when none exists. The fix is to *create* a
            # stream explicitly via ``new_stream`` and bind it as the
            # active stream for this call. We also set it as the
            # thread's default so any lazy ``mx.eval`` that fires
            # *outside* our ``with`` block (e.g. when ``np.array()``
            # forces evaluation of an mx.array post-context-exit) has
            # a stream to dispatch on.
            import mlx.core as mx  # type: ignore[import-not-found]
            import numpy as np

            try:
                stream = mx.new_stream(mx.gpu)
            except Exception as exc:
                # Macs without Metal (very rare on the deploy target)
                # fall back to CPU — mlx_audio still works there, just
                # ~3-5× slower.
                logger.warning(
                    "[tts/omnivoice-mlx] new_stream(gpu) failed: %s; trying cpu",
                    exc,
                )
                try:
                    stream = mx.new_stream(mx.cpu)
                except Exception as exc2:
                    raise TTSError(
                        f"mlx stream init failed: {exc2}"
                    ) from exc2

            try:
                mx.set_default_stream(stream)
            except Exception as exc:
                # Some MLX builds expose set_default_stream only on
                # newer wheels — non-fatal, the ``with`` block below
                # still binds the stream for the generate path.
                logger.debug(
                    "[tts/omnivoice-mlx] set_default_stream skipped: %s", exc,
                )

            kwargs: dict[str, Any] = {"text": text}
            if ref_path is not None:
                kwargs["ref_audio"] = ref_path
            if ref_text:
                kwargs["ref_text"] = ref_text

            # ``model.generate`` is a generator; collect everything
            # into a list so we get the full utterance up front. For
            # short text (single line agent / podcast turn) this is
            # one or two yields and a few-hundred-ms wall clock. We
            # also materialise the audio arrays into numpy WHILE the
            # stream is bound so any lazy eval finishes here, not on
            # context exit when the stream is gone.
            audio_chunks: list[Any] = []
            try:
                with mx.stream(stream):
                    try:
                        results = list(self._model.generate(**kwargs))
                    except TypeError as exc:
                        # The Qwen3-TTS family takes the kwargs above;
                        # older OmniVoice-style models didn't. Fall
                        # back to text-only so the caller still gets
                        # *something* back instead of a hard fail.
                        logger.warning(
                            "[tts/omnivoice-mlx] generate kwargs rejected "
                            "(%s); retrying with text-only call", exc,
                        )
                        results = list(self._model.generate(text))
                    for r in results:
                        audio = getattr(r, "audio", None)
                        if audio is None:
                            # Some builds return raw mx.array directly.
                            audio = r
                        # Force eval before leaving the stream context.
                        if hasattr(audio, "shape"):
                            try:
                                mx.eval(audio)
                            except Exception:
                                pass
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
            except ValueError as exc:
                raise TTSError(f"mlx model.generate failed: {exc}") from exc
            except RuntimeError as exc:
                # Stream / device errors land here. Surface as TTSError
                # so the podcast orchestrator emits ``line_failed``
                # cleanly instead of crashing into the generic
                # exception path.
                raise TTSError(f"mlx model.generate failed: {exc}") from exc

            if not results:
                logger.warning(
                    "[tts/omnivoice-mlx] generate produced no results"
                )
                return b""

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
            # No unlink — the stable cache dir ``{data_dir}/tts_ref_cache``
            # survives across calls and re-uploads overwrite in place.
            pass
