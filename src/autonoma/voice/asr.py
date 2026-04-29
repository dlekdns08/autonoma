"""Server-side automatic speech recognition (ASR) for voice commands.

Phase 2-#4 wires the browser's microphone capture to this module so
``ExternalInputRouter`` can ingest the transcribed text through the same
plumbing that handles live chat.

Default backend: ``CohereLabs/cohere-transcribe-03-2026`` via HuggingFace
transformers. The model is gated, so the operator must:

  1. Accept the model card on huggingface.co.
  2. Export ``HF_TOKEN`` (or ``HUGGING_FACE_HUB_TOKEN``) before launch.

We do NOT silently fall back to a different model on auth failure —
that's a deliberate choice (the user picked Cohere for a reason).
"""

from __future__ import annotations

import io
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_ms: int = 0
    model: str = ""


class AsrProvider(ABC):
    """Synchronous ASR provider — call ``transcribe`` from a worker thread."""

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> TranscriptionResult:
        ...

    @abstractmethod
    def is_ready(self) -> bool:
        ...


class NoopAsrProvider(AsrProvider):
    """Stub for tests and environments without GPU/HF access."""

    def is_ready(self) -> bool:
        return True

    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> TranscriptionResult:
        return TranscriptionResult(text="", language=language, model="noop")


class CohereAsrProvider(AsrProvider):
    """HuggingFace ``CohereLabs/cohere-transcribe-03-2026`` backend.

    The model is heavy (~B-scale params) so we lazy-load it on first
    ``transcribe`` and reuse the same instance across calls. A lock
    serialises concurrent transcriptions because the underlying
    ``model.generate`` is not safe to share across threads on the same
    accelerator stream without queuing.

    Device selection is explicit: we run on Apple Metal (``mps``) when
    available — the user runs Autonoma on Apple Silicon for development
    so CUDA isn't an option. Fall back to CPU if MPS isn't built into
    this PyTorch install (rare on recent macOS wheels). We DON'T use
    ``device_map="auto"`` because HF's auto path can scatter layers
    across CPU and MPS in surprising ways.
    """

    MODEL_ID: str = "CohereLabs/cohere-transcribe-03-2026"
    DEFAULT_SAMPLING_RATE: int = 16_000
    DEFAULT_MAX_NEW_TOKENS: int = 256

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or self.MODEL_ID
        self._processor: Any = None
        self._model: Any = None
        self._device: str = "cpu"
        self._lock = threading.Lock()
        # Stored as a plain ``str`` so each ``_ensure_loaded`` call that
        # follows a prior failure raises a *fresh* ``RuntimeError`` —
        # we used to stash the original exception object and ``raise``
        # it on every call, which made each retry chain a new
        # ``__cause__`` onto the same instance. The traceback then
        # grew without bound across the warmup retries logged on
        # startup. Keeping just the message keeps each raise atomic.
        self._load_error: str | None = None
        # Reusable scratch tempfile path for the ``transcribe`` audio
        # spill. Created lazily on first call and overwritten in place
        # on every subsequent call — avoids the "thousands of /tmp
        # files per minute" pattern you'd get from a fresh
        # NamedTemporaryFile per transcribe. Safe because ``self._lock``
        # serialises all writers, and the API runs --workers=1 (see
        # Dockerfile.api) so no other process shares the path.
        self._scratch_path: str | None = None

    def is_ready(self) -> bool:
        return self._model is not None

    @staticmethod
    def _select_device() -> str:
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError:
            return "cpu"
        # Apple Silicon path: prefer MPS. ``is_available`` catches the
        # "torch built without MPS" case; ``is_built`` distinguishes
        # platform support from runtime availability.
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available() and mps.is_built():
            return "mps"
        return "cpu"

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._load_error is not None:
            # Fresh exception object per call — see the dataclass field
            # comment for why we no longer re-raise the saved instance.
            raise RuntimeError(self._load_error)
        try:
            # Imported lazily so importing this module without the ASR
            # extras installed never fails at startup.
            from transformers import (  # type: ignore[import-not-found]
                AutoProcessor,
                CohereAsrForConditionalGeneration,
            )
        except ImportError as exc:
            self._load_error = (
                "transformers with CohereAsrForConditionalGeneration is required "
                f"for {self.MODEL_ID}. Install a version that ships the Cohere ASR "
                "class (>= the release that added it in 2026-Q1)."
            )
            raise RuntimeError(self._load_error) from exc

        device = self._select_device()
        self._device = device
        logger.info(f"[asr] loading {self.model_id} on device={device}…")
        self._processor = AutoProcessor.from_pretrained(self.model_id)
        # Load on CPU first then move to the target device. Some HF
        # weights aren't directly loadable on MPS due to dtype-init
        # ordering, but ``.to("mps")`` after construction is reliable.
        model = CohereAsrForConditionalGeneration.from_pretrained(self.model_id)
        if device != "cpu":
            try:
                model = model.to(device)
            except Exception as exc:  # pragma: no cover — runtime quirk
                logger.warning(
                    f"[asr] failed to move model to {device}, staying on CPU: {exc}"
                )
                self._device = "cpu"
        self._model = model
        logger.info(
            f"[asr] {self.model_id} ready on {self._device} dtype={getattr(model, 'dtype', None)}"
        )

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str = "",
    ) -> TranscriptionResult:
        """Run ASR on ``audio_bytes`` and return the transcript.

        ``language`` is a hint passed to the processor. Pass an empty
        string to ask the model to auto-detect — Cohere's processor
        accepts ``language=None`` (or absence) for unspecified-language
        decoding. With the hint set, the model biases its output
        toward that language; without it, accuracy is slightly lower
        on ambiguous utterances but multilingual input works.
        """
        import os
        import tempfile
        import time

        self._ensure_loaded()
        # Lazy import: the audio loader is heavy and only needed when a
        # caller actually transcribes something.
        from transformers.audio_utils import load_audio  # type: ignore[import-not-found]

        # Newer transformers ``load_audio`` rejects file-like objects
        # ("Should be an url, a local path, or numpy array"). Spill the
        # raw bytes to a *reusable* scratch file (created once by
        # ``_ensure_loaded`` above) rather than a fresh
        # NamedTemporaryFile per call — the latter creates and unlinks
        # a file on every transcribe, which adds up under load (each
        # WS partial pass = one transcribe = one file).
        #
        # The write + load_audio sequence is serialised by ``self._lock``
        # below, which also serialises ``model.generate`` against
        # concurrent transcribes — so we don't race on the scratch file
        # contents.

        with self._lock:
            t0 = time.perf_counter()
            # Lazily create the scratch path on first transcribe, under
            # the same lock that serialises every transcribe. Subsequent
            # calls overwrite the same file.
            if self._scratch_path is None:
                tf = tempfile.NamedTemporaryFile(
                    suffix=".bin", prefix="autonoma_asr_", delete=False
                )
                tf.close()
                self._scratch_path = tf.name
            with open(self._scratch_path, "wb") as f:
                f.write(audio_bytes)
            audio = load_audio(
                self._scratch_path, sampling_rate=self.DEFAULT_SAMPLING_RATE
            )
            # Empty/whitespace language → omit the kwarg entirely so
            # the processor uses its built-in language detection. We
            # don't pass ``language=None`` because some processor
            # versions interpret None as "english" rather than auto.
            processor_kwargs: dict[str, Any] = {
                "sampling_rate": self.DEFAULT_SAMPLING_RATE,
                "return_tensors": "pt",
            }
            if language and language.strip():
                processor_kwargs["language"] = language
            inputs = self._processor(audio, **processor_kwargs)
            # ``self._model.device`` is the canonical target after our
            # explicit ``.to(device)`` move in ``_ensure_loaded``. The
            # MPS path is happiest when input dtype matches the model.
            inputs = inputs.to(self._model.device, dtype=self._model.dtype)
            outputs = self._model.generate(
                **inputs, max_new_tokens=self.DEFAULT_MAX_NEW_TOKENS
            )
            # ``model.generate`` returns a 2D tensor ``(batch, seq_len)``.
            # Newer transformers return a list from ``processor.decode``
            # when given a batch tensor — use ``batch_decode`` so the
            # contract is explicit, then pull the single utterance.
            decoded = self._processor.batch_decode(outputs, skip_special_tokens=True)
            text = decoded[0] if decoded else ""
            duration_ms = int((time.perf_counter() - t0) * 1000)

        return TranscriptionResult(
            text=text.strip() if isinstance(text, str) else "",
            language=language,
            duration_ms=duration_ms,
            model=self.model_id,
        )


# ── Provider singleton ────────────────────────────────────────────────

_provider: AsrProvider | None = None
_provider_lock = threading.Lock()


def get_asr_provider() -> AsrProvider:
    """Return the configured ASR provider, instantiating on first call."""
    global _provider
    with _provider_lock:
        if _provider is None:
            from autonoma.config import settings

            kind = getattr(settings, "voice_asr_provider", "cohere")
            if kind == "cohere":
                _provider = CohereAsrProvider(
                    model_id=getattr(settings, "voice_asr_model", None),
                )
            else:
                _provider = NoopAsrProvider()
        return _provider


def set_asr_provider_for_tests(provider: AsrProvider | None) -> None:
    """Test hook — bypass the singleton initialisation."""
    global _provider
    with _provider_lock:
        _provider = provider


async def warmup_asr_provider() -> None:
    """Load the ASR model into memory at startup.

    Cohere's transcribe model is multi-GB, so the first call pays
    HuggingFace download (often 30–120s on a cold cache) plus model
    load (5–15s on MPS/CPU). Triggering ``_ensure_loaded`` in the
    startup hook means the user's first push-to-talk only pays the
    transcription cost itself, not the warm-up.
    Mirrors ``warmup_shared_client`` in ``tts_omnivoice``.
    """
    import anyio

    from autonoma.config import settings

    if getattr(settings, "voice_asr_provider", "cohere") == "none":
        return
    provider = get_asr_provider()
    # Only the Cohere backend has a heavy lazy-load step worth warming.
    # ``NoopAsrProvider`` short-circuits in ``is_ready``.
    if not isinstance(provider, CohereAsrProvider):
        return
    try:
        # Heavy IO + model construction — push to a worker thread so
        # the FastAPI startup hook stays responsive (other startup
        # tasks like the TTS warmup run alongside).
        await anyio.to_thread.run_sync(provider._ensure_loaded)
        logger.info(
            f"[asr] {provider.model_id} warm-load complete (device={provider._device})"
        )
    except Exception:  # pragma: no cover — startup path
        logger.exception("[asr] warm-load failed; falling back to first-call lazy load")
