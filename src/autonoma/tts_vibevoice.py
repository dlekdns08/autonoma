"""VibeVoice TTS backend — Microsoft frontier multi-speaker model.

https://huggingface.co/microsoft/VibeVoice-1.5B

VibeVoice is conceptually similar to OmniVoice (zero-shot voice
cloning from a reference clip) but tuned for *long-form* multi-
speaker dialogue — perfect for the /podcast feature. We expose it
through the same ``BaseTTSClient`` contract so flipping
``settings.tts_provider`` is the only switch needed.

Inference shape (subject to confirmation with the model card):
  * Loaded via ``transformers.AutoModel.from_pretrained(..., trust_remote_code=True)``.
    VibeVoice ships its own model class on HF Hub, not yet upstream
    in transformers, so trust_remote_code is required.
  * Inputs: text + (optional) speaker reference waveform(s).
  * Output: float32 PCM at ``SAMPLE_RATE`` (24 kHz unless the model
    config says otherwise).

If your VibeVoice fork uses a different inference call (e.g.
``model.synthesize(...)`` instead of ``model.generate(...)``), tweak
``_run_inference`` below — that's the only spot tightly coupled to
the model API. Everything else (lifecycle, audio framing, warmup,
device selection) is reusable.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import wave
from typing import Any, AsyncIterator

from autonoma.config import settings
from autonoma.tts_base import BaseTTSClient, TTSError

logger = logging.getLogger(__name__)

# Defaults tuned for VibeVoice-1.5B; overridable via settings so a
# heavier (or smaller) variant doesn't require a code change.
DEFAULT_MODEL_ID = "microsoft/VibeVoice-1.5B"
DEFAULT_SAMPLE_RATE = 24_000

# Same singleton pattern as ``tts_omnivoice``: every worker reuses one
# instance because the underlying model serialises generate() against
# its own GPU buffers.
_shared_client: "VibeVoiceClient | None" = None


def get_shared_client() -> "VibeVoiceClient":
    global _shared_client
    if _shared_client is None:
        _shared_client = VibeVoiceClient(
            model_id=getattr(settings, "vibevoice_model_id", "") or DEFAULT_MODEL_ID,
        )
    return _shared_client


def shared_client_status() -> dict[str, Any]:
    """Cheap snapshot for /api/health. Avoids triggering a model load."""
    if _shared_client is None:
        return {"loaded": False, "device": "", "dtype": ""}
    return {
        "loaded": _shared_client.is_loaded(),
        "device": _shared_client.device,
        "dtype": str(_shared_client.dtype) if _shared_client.dtype else "",
    }


async def warmup_shared_client() -> None:
    """Pre-load the model on FastAPI startup so the first synthesis
    doesn't pay the multi-GB load cost in front of an HTTP timeout.
    Mirrors ``tts_omnivoice.warmup_shared_client``.
    """
    client = get_shared_client()
    try:
        await client._ensure_model()
        logger.info(
            "[tts] VibeVoice warm-load complete (device=%s dtype=%s)",
            client.device,
            client.dtype,
        )
    except TTSError as exc:
        logger.warning("[tts] VibeVoice warm-load skipped: %s", exc)
    except Exception:  # pragma: no cover — startup path
        logger.exception("[tts] VibeVoice warm-load failed")


class VibeVoiceClient(BaseTTSClient):
    """Streaming TTS client backed by ``microsoft/VibeVoice-1.5B``."""

    def __init__(self, model_id: str = DEFAULT_MODEL_ID) -> None:
        self.model_id = model_id
        self._model: Any = None
        self._processor: Any = None
        self._device: str = "cpu"
        self._dtype: Any = None
        self._load_lock = asyncio.Lock()
        self._gen_lock = asyncio.Lock()  # serialise generate() calls
        self._load_error: str | None = None

    # ── Public introspection ─────────────────────────────────────────
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def device(self) -> str:
        return self._device

    @property
    def dtype(self) -> Any:
        return self._dtype

    # ── Device + dtype selection (matches tts_omnivoice) ────────────
    @staticmethod
    def _select_device_and_dtype() -> tuple[str, Any]:
        """Pick the best available accelerator. CUDA > MPS > CPU.

        VibeVoice is a 1.5B param transformer; it fits comfortably in
        bfloat16 / float16 on accelerators and falls back to float32 on
        CPU. We default to bfloat16 on CUDA for stability and float16
        on MPS because Apple's bfloat16 path is slower in practice.
        """
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError:
            return "cpu", None
        if torch.cuda.is_available():
            return "cuda", torch.bfloat16
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available() and mps.is_built():
            return "mps", torch.float16
        return "cpu", torch.float32

    # ── Model lifecycle ─────────────────────────────────────────────
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
        """Synchronous heavy lift: import torch + transformers, pull
        weights from the HF cache, move to the chosen device.

        Run inside ``asyncio.to_thread`` so the event loop keeps
        serving other endpoints while a multi-GB load is in flight.
        """
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError as exc:
            self._load_error = "torch is required for VibeVoice"
            logger.error("[tts/vibevoice] %s: %s", self._load_error, exc)
            return

        # VibeVoice ships as its own git package — *not* upstream in
        # transformers. The Cohere ASR fork running in production
        # also drops top-level ``AutoModel``, so trying to load
        # VibeVoice via ``transformers.AutoModel`` will fail there
        # regardless. Operator install:
        #
        #   uv pip install 'vibevoice[streamingtts] @ \
        #       git+https://github.com/microsoft/VibeVoice.git@main'
        #
        # Class names have shifted across releases; we try the
        # canonical exports first, then the submodule paths the
        # streamingtts extra exposes.
        ModelCls: Any = None
        ProcessorCls: Any = None
        try:
            import vibevoice  # type: ignore[import-not-found]
        except ImportError as exc:
            self._load_error = (
                "vibevoice package not installed. Run: "
                "uv pip install 'vibevoice[streamingtts] @ "
                "git+https://github.com/microsoft/VibeVoice.git@main'"
            )
            logger.error("[tts/vibevoice] %s: %s", self._load_error, exc)
            return

        for name in (
            "VibeVoiceForConditionalGenerationInference",
            "VibeVoiceForConditionalGeneration",
        ):
            cls = getattr(vibevoice, name, None)
            if cls is not None:
                ModelCls = cls
                break
        if ModelCls is None:
            try:
                from vibevoice.modular_modeling_vibevoice import (  # type: ignore[import-not-found]
                    VibeVoiceForConditionalGenerationInference as ModelCls,
                )
            except ImportError:
                try:
                    from vibevoice.modular_modeling_vibevoice import (  # type: ignore[import-not-found]
                        VibeVoiceForConditionalGeneration as ModelCls,
                    )
                except ImportError:
                    ModelCls = None

        ProcessorCls = getattr(vibevoice, "VibeVoiceProcessor", None)
        if ProcessorCls is None:
            try:
                from vibevoice.modular_processing_vibevoice import (  # type: ignore[import-not-found]
                    VibeVoiceProcessor as ProcessorCls,
                )
            except ImportError:
                ProcessorCls = None

        if ModelCls is None or ProcessorCls is None:
            self._load_error = (
                "vibevoice installed but VibeVoiceForConditionalGeneration"
                " / VibeVoiceProcessor not exposed. Update from "
                "https://github.com/microsoft/VibeVoice."
            )
            logger.error("[tts/vibevoice] %s", self._load_error)
            return

        device, dtype = self._select_device_and_dtype()
        self._device = device
        self._dtype = dtype
        logger.info(
            "[tts/vibevoice] loading %s on device=%s dtype=%s",
            self.model_id,
            device,
            dtype,
        )
        try:
            # The VibeVoice package owns these classes — no
            # ``trust_remote_code`` needed because the code lives in
            # the installed package, not the HF repo. Revision pin is
            # still useful when an operator wants reproducibility.
            revision = os.environ.get("VIBEVOICE_REVISION") or None
            proc_kwargs: dict[str, Any] = {}
            model_kwargs: dict[str, Any] = {}
            if revision:
                proc_kwargs["revision"] = revision
                model_kwargs["revision"] = revision
            if dtype is not None and device != "cpu":
                model_kwargs["torch_dtype"] = dtype
            self._processor = ProcessorCls.from_pretrained(self.model_id, **proc_kwargs)
            model = ModelCls.from_pretrained(self.model_id, **model_kwargs)
            if device != "cpu":
                try:
                    model = model.to(device)
                except Exception as exc:  # pragma: no cover — runtime quirk
                    logger.warning(
                        "[tts/vibevoice] couldn't move model to %s, staying on cpu: %s",
                        device,
                        exc,
                    )
                    self._device = "cpu"
            try:
                model.eval()
            except AttributeError:
                pass  # some custom model classes don't expose eval()
            self._model = model
        except Exception as exc:
            self._load_error = f"VibeVoice load failed: {exc}"
            logger.exception("[tts/vibevoice] load failed")

    # ── Inference ───────────────────────────────────────────────────
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
        # We don't have an intra-utterance streaming path from VibeVoice's
        # public API — yield the whole WAV in one chunk. Downstream
        # browser playback already handles single-blob delivery, and
        # the worker's sequence (audio_start → chunk → audio_end) is
        # contract-faithful regardless of chunk count.
        yield wav_bytes

    def _run_inference(
        self,
        *,
        text: str,
        ref_audio: bytes | None,
        ref_audio_mime: str,
        ref_text: str,
        language: str,
    ) -> bytes:
        """Bridge to VibeVoice's actual API. ⚠ Verify call shape against
        the HF model card — the lines below use the most common
        ``processor → model.generate → processor.batch_decode`` shape
        used by audio transformers, but VibeVoice may expose a
        purpose-built ``synthesize`` helper or a different keyword for
        the speaker reference. Adjust here only; everything else is
        backend-agnostic.
        """
        import numpy as np  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]

        t0 = time.perf_counter()
        ref_array = self._decode_ref_audio(ref_audio, ref_audio_mime)

        # Build the processor input. We try a few keyword shapes the
        # VibeVoice family is known to use, in priority order — first
        # match wins. ``voice_samples`` is the HF model-card name; the
        # HF ``audio`` kwarg is the transformers-canonical fallback.
        proc_kwargs: dict[str, Any] = {
            "text": text,
            "return_tensors": "pt",
        }
        if ref_array is not None:
            for key in ("voice_samples", "audio", "speaker_audio"):
                try:
                    inputs = self._processor(**{**proc_kwargs, key: [ref_array]})
                    break
                except (TypeError, KeyError):
                    continue
            else:
                # No keyword the processor accepted — drop the reference
                # silently. VibeVoice should still synthesise with its
                # default voice rather than fail outright.
                inputs = self._processor(**proc_kwargs)
        else:
            inputs = self._processor(**proc_kwargs)

        # Move tensors to the model's device + dtype.
        try:
            inputs = inputs.to(self._model.device, dtype=self._dtype)
        except (AttributeError, TypeError):
            # Some processors return a dict of tensors, not a
            # BatchEncoding — handle that shape too.
            try:
                inputs = {
                    k: (v.to(self._model.device) if hasattr(v, "to") else v)
                    for k, v in inputs.items()
                }
            except Exception:
                pass

        # Inference. We try ``model.generate`` first (most common) and
        # fall back to ``model.synthesize`` if the wrapped class
        # exposes a tts-specific entry point.
        with torch.no_grad():
            if hasattr(self._model, "generate"):
                outputs = self._model.generate(
                    **(inputs if isinstance(inputs, dict) else inputs.data),
                    max_new_tokens=getattr(settings, "vibevoice_max_new_tokens", 4096),
                )
            elif hasattr(self._model, "synthesize"):
                outputs = self._model.synthesize(
                    **(inputs if isinstance(inputs, dict) else inputs.data),
                )
            else:
                raise TTSError(
                    "VibeVoice model exposes neither .generate nor .synthesize — "
                    "update _run_inference for this revision."
                )

        # Decode. Audio TTS models conventionally return either:
        #   (a) a tensor / ndarray of audio samples, or
        #   (b) a structure decoded by the processor's batch_decode.
        audio_array: Any
        if hasattr(self._processor, "batch_decode"):
            try:
                decoded = self._processor.batch_decode(outputs)
                audio_array = decoded[0] if decoded else None
            except Exception:
                audio_array = outputs
        else:
            audio_array = outputs

        # Convert torch tensor → numpy float32.
        if hasattr(audio_array, "detach"):
            audio_array = audio_array.detach().cpu().to(torch.float32).numpy()
        if audio_array is None:
            return b""
        if not isinstance(audio_array, np.ndarray):
            audio_array = np.asarray(audio_array, dtype=np.float32)
        # Squeeze leading batch / channel dims down to mono 1-D.
        while audio_array.ndim > 1 and audio_array.shape[0] == 1:
            audio_array = audio_array[0]
        if audio_array.ndim > 1:
            # Stereo → mono by mean
            audio_array = audio_array.mean(axis=0)

        sr = getattr(settings, "vibevoice_sample_rate", 0) or DEFAULT_SAMPLE_RATE
        wav_bytes = self._float32_to_wav(audio_array.astype(np.float32), sr)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "[tts/vibevoice] synth ok text_len=%d bytes=%d elapsed_ms=%d sr=%d",
            len(text),
            len(wav_bytes),
            elapsed_ms,
            sr,
        )
        return wav_bytes

    @staticmethod
    def _decode_ref_audio(ref_audio: bytes | None, ref_audio_mime: str) -> Any:
        """Decode the voice profile's reference audio to a 24 kHz mono
        float32 numpy array. Returns ``None`` if no reference is
        available — VibeVoice will then fall back to its default voice.
        """
        if not ref_audio:
            return None
        try:
            import numpy as np  # type: ignore[import-not-found]

            try:
                # Fast path: ``soundfile`` reads WAV/FLAC straight from
                # bytes and is already a TTS extras dependency.
                import soundfile as sf  # type: ignore[import-not-found]

                data, src_sr = sf.read(io.BytesIO(ref_audio), dtype="float32", always_2d=False)
                if data.ndim > 1:
                    data = data.mean(axis=1)
                if src_sr != DEFAULT_SAMPLE_RATE:
                    # Resample only when needed to keep the dependency
                    # cost off the hot path. ``librosa.resample`` is the
                    # least-surprising choice and is already present.
                    import librosa  # type: ignore[import-not-found]

                    data = librosa.resample(
                        data, orig_sr=src_sr, target_sr=DEFAULT_SAMPLE_RATE
                    )
                return data.astype(np.float32)
            except Exception:
                # Fallback for non-WAV containers (mp3/ogg/webm) —
                # spill to a tempfile and let librosa+ffmpeg handle it.
                import tempfile

                import librosa  # type: ignore[import-not-found]

                with tempfile.NamedTemporaryFile(
                    suffix=".bin", delete=False
                ) as tf:
                    tf.write(ref_audio)
                    tmp = tf.name
                try:
                    data, _ = librosa.load(tmp, sr=DEFAULT_SAMPLE_RATE, mono=True)
                    return data.astype(np.float32)
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[tts/vibevoice] ref_audio decode failed: %s", exc)
            return None

    @staticmethod
    def _float32_to_wav(samples: Any, sample_rate: int) -> bytes:
        """16-bit PCM WAV from a 1-D float32 array in [-1, 1].

        We write WAV (not raw PCM) so the streaming dispatch on the
        worker side can reuse the same audio frame the OmniVoice
        backend emits — both formats are interchangeable at the
        audio-element level.
        """
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
