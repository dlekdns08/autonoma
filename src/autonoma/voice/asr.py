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
        self._load_error: Exception | None = None

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
            raise self._load_error
        try:
            # Imported lazily so importing this module without the ASR
            # extras installed never fails at startup.
            from transformers import (  # type: ignore[import-not-found]
                AutoProcessor,
                CohereAsrForConditionalGeneration,
            )
        except ImportError as exc:
            self._load_error = RuntimeError(
                "transformers with CohereAsrForConditionalGeneration is required "
                f"for {self.MODEL_ID}. Install a version that ships the Cohere ASR "
                "class (>= the release that added it in 2026-Q1)."
            )
            raise self._load_error from exc

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
        language: str = "en",
    ) -> TranscriptionResult:
        import time

        self._ensure_loaded()
        # Lazy import: the audio loader is heavy and only needed when a
        # caller actually transcribes something.
        from transformers.audio_utils import load_audio  # type: ignore[import-not-found]

        # ``load_audio`` accepts a file-like object; wrap raw bytes so
        # callers don't need to spill to disk just to transcribe.
        audio = load_audio(io.BytesIO(audio_bytes), sampling_rate=self.DEFAULT_SAMPLING_RATE)

        with self._lock:
            t0 = time.perf_counter()
            inputs = self._processor(
                audio,
                sampling_rate=self.DEFAULT_SAMPLING_RATE,
                return_tensors="pt",
                language=language,
            )
            # ``self._model.device`` is the canonical target after our
            # explicit ``.to(device)`` move in ``_ensure_loaded``. The
            # MPS path is happiest when input dtype matches the model.
            inputs = inputs.to(self._model.device, dtype=self._model.dtype)
            outputs = self._model.generate(
                **inputs, max_new_tokens=self.DEFAULT_MAX_NEW_TOKENS
            )
            text = self._processor.decode(outputs, skip_special_tokens=True)
            duration_ms = int((time.perf_counter() - t0) * 1000)

        return TranscriptionResult(
            text=text.strip(),
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
