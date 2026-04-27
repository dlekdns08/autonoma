"""OmniVoice zero-shot TTS — k2-fsa/OmniVoice.

Replaces Azure Neural TTS. Zero-shot means each voice is conditioned by
a short reference audio + its transcript; we store those per profile in
the ``voice_profiles`` table and bind them per VRM in ``voice_bindings``.

Device picking: cuda > mps (Apple Silicon) > cpu, auto-detected on first
``synthesize`` call. fp16 on cuda/mps (memory + speed win), fp32 on cpu
(fp16 on cpu is usually slower, never faster).

The model is heavy (multi-GB) so we lazy-load on first use, hold a
single instance per process, and short-circuit if the package isn't
installed (falls back to StubTTSClient upstream).

── Perf tuning (see ``autonoma.config.Settings`` for env vars) ────────
OmniVoice is a diffusion-style iterative decoder, not autoregressive.
Three knobs dominate latency, listed by impact:

1. ``num_step`` — linear scale factor on decode time. Upstream default
   32; we default 16.
2. ``guidance_scale`` + ``skip_uncond_forward`` — CFG doubles the
   forward pass because the model runs a 2B batch (cond + uncond).
   guidance_scale=0 cuts the mixing math; the monkey-patch below also
   cuts the 2B forward down to B for an additional ~2× win.
3. ``voice_clone_prompt`` caching — pre-computing the reference audio
   encoding per profile and reusing it across utterances removes the
   ~100-200 ms encoder pass from every call.

Combined with ``audio_chunk_threshold_s`` (5 s instead of 30 s so
dialogue-length utterances also benefit from OmniVoice's internal
streaming chunking), this brings first-byte latency from ~1.5 s into
the ~300-500 ms range on Apple Silicon MPS.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import tempfile
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

import numpy as np

from autonoma.config import settings
from autonoma.tts_base import BaseTTSClient, TTSError

logger = logging.getLogger(__name__)


# Model card: https://huggingface.co/k2-fsa/OmniVoice
# Reads from ``AUTONOMA_OMNIVOICE_MODEL_ID`` so the Dockerfile ARG can
# pin a specific revision at build time without editing the source.
# Falls back to the upstream default for local dev.
OMNIVOICE_MODEL_ID = os.environ.get("AUTONOMA_OMNIVOICE_MODEL_ID", "k2-fsa/OmniVoice")
OMNIVOICE_SAMPLE_RATE = 24000


class _OmniVoiceModel(Protocol):
    """Structural type for the OmniVoice model. Kept as a Protocol so the
    package import stays optional — we only actually touch the real class
    inside ``_ensure_model``."""

    def generate(self, *args: Any, **kwargs: Any) -> list[np.ndarray]: ...
    def create_voice_clone_prompt(self, *args: Any, **kwargs: Any) -> Any: ...


def _pick_device() -> tuple[str, str]:
    """Return (device, dtype_str). Order: cuda > mps > cpu.

    Dtype is fp16 on GPU paths (halves memory + faster on tensor cores
    and Apple Neural Engine), fp32 on CPU because fp16 on CPU typically
    falls back to software emulation and runs *slower* than fp32.

    The dtype comes back as a string — the caller resolves it via
    ``getattr(torch, dtype_str)`` at model-load time so this module
    doesn't need torch imported at import time.
    """
    try:
        import torch
    except ImportError:
        return "cpu", "float32"

    if torch.cuda.is_available():
        return "cuda", "float16"
    mps_ok = (
        hasattr(torch.backends, "mps")
        and torch.backends.mps.is_available()
        and torch.backends.mps.is_built()
    )
    if mps_ok:
        return "mps", "float16"
    return "cpu", "float32"


def _pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int) -> bytes:
    """Encode a float32 mono numpy array to PCM16 WAV bytes."""
    clipped = np.clip(pcm, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


# ── Monkey-patch: skip unconditional forward when CFG is disabled ─────
#
# OmniVoice's ``_generate_iterative`` builds a 2B batch (cond rows
# 0..B-1, uncond rows B..2B-1) and runs every decode step on that full
# batch.  When ``guidance_scale=0`` the uncond half's logits are
# discarded by ``_predict_tokens_with_scoring``, so running the forward
# on it is wasted compute.  The patch below intercepts the inner
# forward call: for each step, we run the model on the cond half only
# and pad the missing uncond half with zeros so all the downstream
# indexing (``batch_logits[B + i: ...]``) still works.  The zero
# padding is safe because those slices become ``u_logits`` which
# ``_predict_tokens_with_scoring`` ignores when guidance_scale is 0.
#
# Gated behind ``settings.tts_skip_uncond_forward`` + effective
# guidance_scale == 0 so it's a pure no-op when the aggressive path
# isn't enabled.


def _install_skip_uncond_patch(model: Any) -> None:
    """Patch ``model`` in place so its ``__call__`` (forward) runs only
    the conditional half of the 2B batch when CFG is effectively off.

    The batching sentinel is simple: when called with input_ids shaped
    (2B, ...) AND the module has ``_autonoma_skip_uncond`` truthy, we
    slice to the first B rows, run once, and return a namedtuple with
    ``logits`` padded back to (2B, ...) using zeros for the uncond half.
    """
    if getattr(model, "_autonoma_skip_uncond_installed", False):
        return
    try:
        import torch
    except ImportError:
        return

    original_forward = model.forward

    def patched_forward(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        input_ids = kwargs.get("input_ids")
        if input_ids is None and args:
            input_ids = args[0]
        # Only shortcut when the aggressive flag is set AND the batch
        # shape matches the 2B contract (even size ≥ 2).  Any other
        # call path (e.g. training, different batch geometry) falls
        # through to the unmodified forward.
        skip = getattr(model, "_autonoma_skip_uncond", False)
        if (
            skip
            and input_ids is not None
            and isinstance(input_ids, torch.Tensor)
            and input_ids.dim() == 3
            and input_ids.size(0) >= 2
            and input_ids.size(0) % 2 == 0
        ):
            full_b = input_ids.size(0)
            b = full_b // 2
            sliced_kwargs = dict(kwargs)
            sliced_kwargs["input_ids"] = input_ids[:b]
            if "audio_mask" in kwargs and kwargs["audio_mask"] is not None:
                sliced_kwargs["audio_mask"] = kwargs["audio_mask"][:b]
            if "attention_mask" in kwargs and kwargs["attention_mask"] is not None:
                sliced_kwargs["attention_mask"] = kwargs["attention_mask"][:b]
            # Run forward on cond half only.  *args dropped because
            # OmniVoice always calls this with kwargs in
            # _generate_iterative; if training code path calls with
            # positionals we'd have already bailed out above.
            out = original_forward(**sliced_kwargs)
            cond_logits = out.logits
            pad = torch.zeros_like(cond_logits)
            out.logits = torch.cat([cond_logits, pad], dim=0)
            return out
        return original_forward(*args, **kwargs)

    # bound-method style so ``model(...)`` still dispatches through
    # nn.Module.__call__ → forward normally.
    model.forward = patched_forward
    model._autonoma_skip_uncond_installed = True


class OmniVoiceTTSClient(BaseTTSClient):
    """Streaming-compatible wrapper around OmniVoice zero-shot synthesis.

    ``synthesize`` takes the *voice profile id* as the ``voice`` arg; the
    tts_worker resolves the id to (ref_audio_bytes, ref_text) via the
    voice store and passes those through via the ``ref_audio`` / ``ref_text``
    kwargs.  We materialise the reference audio to a per-process cache
    file (OmniVoice's API takes a filesystem path, not bytes), and
    additionally cache the GPU-resident ``VoiceClonePrompt`` object so
    the reference-audio encoder only runs once per profile.

    Model load + inference are blocking CPU/GPU work, so we offload to a
    thread via ``asyncio.to_thread``.  One semaphore above us in the
    worker caps concurrency; we don't add a second one here.
    """

    def __init__(self) -> None:
        self._model: _OmniVoiceModel | None = None
        self._model_lock = asyncio.Lock()
        self._device: str = ""
        self._dtype: str = ""
        # uuid → path of materialised ref audio. Cleared on process
        # exit. Capped implicitly at the number of voice profiles
        # uploaded (~ dozens in practice).
        self._ref_cache: dict[str, Path] = {}
        # (profile_id, content_hash) → pre-computed VoiceClonePrompt.
        # Including the hash in the key invalidates the cache when a
        # profile's ref audio gets re-uploaded.
        self._prompt_cache: dict[tuple[str, str], Any] = {}
        self._prompt_lock = asyncio.Lock()
        self._cache_dir: Path = Path(tempfile.mkdtemp(prefix="autonoma_tts_ref_"))

    async def _ensure_model(self) -> _OmniVoiceModel:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is not None:
                return self._model
            device, dtype_name = _pick_device()
            logger.info("[tts] loading OmniVoice on device=%s dtype=%s", device, dtype_name)
            try:
                import torch
                from omnivoice import OmniVoice  # type: ignore[import-not-found]
            except ImportError as exc:
                raise TTSError(
                    f"OmniVoice package not installed ({exc}). "
                    "Install with: pip install omnivoice torch"
                ) from exc

            dtype = getattr(torch, dtype_name)
            # ``device_map`` expects "cuda:0" / "mps" / "cpu" — normalise
            # the bare "cuda" the picker returns.
            device_map = "cuda:0" if device == "cuda" else device
            model = await asyncio.to_thread(
                OmniVoice.from_pretrained,
                OMNIVOICE_MODEL_ID,
                device_map=device_map,
                dtype=dtype,
            )
            # Install the skip-uncond forward patch.  It's a no-op
            # unless ``_autonoma_skip_uncond`` is flipped true at
            # generate-time (see ``synthesize`` below).
            if settings.tts_skip_uncond_forward:
                _install_skip_uncond_patch(model)

            # Opt-in torch.compile.  Gate behind settings flag because
            # the first compile call is multi-second and some MPS
            # graph captures still regress on newer macOS builds — we
            # want a way to turn it off fast if anything breaks.
            if settings.tts_compile:
                try:
                    logger.info("[tts] compiling OmniVoice speech LM (torch.compile)…")
                    model.forward = torch.compile(model.forward, mode="reduce-overhead")
                except Exception as exc:  # pragma: no cover — depends on torch/MPS build
                    logger.warning("[tts] torch.compile failed, falling back to eager: %s", exc)

            self._model = model
            self._device = device
            self._dtype = dtype_name
            return model

    def _ref_audio_path(self, profile_id: str, ref_audio: bytes, mime: str) -> Path:
        """Write ref audio to disk once; return the cached path."""
        cached = self._ref_cache.get(profile_id)
        if cached and cached.exists():
            return cached
        suffix = ".wav" if "wav" in (mime or "") else ".ogg"
        out = self._cache_dir / f"{profile_id}{suffix}"
        out.write_bytes(ref_audio)
        self._ref_cache[profile_id] = out
        return out

    async def _get_voice_clone_prompt(
        self,
        model: _OmniVoiceModel,
        profile_id: str,
        ref_audio_bytes: bytes,
        ref_audio_path: Path,
        ref_text: str,
    ) -> Any:
        """Pre-compute and cache the ``VoiceClonePrompt`` for a profile.

        OmniVoice's ``create_voice_clone_prompt`` runs the audio
        tokeniser (a small neural encoder) on the reference waveform
        and produces a reusable prompt tensor.  Without caching, every
        ``model.generate`` call re-runs this encoder — ~100-200 ms on
        MPS per call.  With the cache, that cost is paid once per
        profile and amortised across all subsequent utterances.
        """
        content_hash = hashlib.sha1(ref_audio_bytes).hexdigest()[:16]
        key = (profile_id, content_hash)
        cached = self._prompt_cache.get(key)
        if cached is not None:
            return cached

        async with self._prompt_lock:
            cached = self._prompt_cache.get(key)
            if cached is not None:
                return cached

            prompt = await asyncio.to_thread(
                model.create_voice_clone_prompt,
                ref_audio=str(ref_audio_path),
                ref_text=ref_text,
            )
            self._prompt_cache[key] = prompt
            return prompt

    def _build_gen_config(self) -> Any:
        """Assemble an ``OmniVoiceGenerationConfig`` from ``settings``.

        Imported lazily so the omnivoice package dependency stays
        optional at module import time.
        """
        from omnivoice.models.omnivoice import OmniVoiceGenerationConfig  # type: ignore[import-not-found]

        return OmniVoiceGenerationConfig(
            num_step=settings.tts_num_step,
            guidance_scale=settings.tts_guidance_scale,
            postprocess_output=settings.tts_postprocess,
            audio_chunk_duration=settings.tts_chunk_duration_s,
            audio_chunk_threshold=settings.tts_chunk_threshold_s,
        )

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
        if not ref_audio or not ref_text:
            raise TTSError("omnivoice: missing ref_audio or ref_text for voice")
        model = await self._ensure_model()
        ref_path = self._ref_audio_path(voice, ref_audio, ref_audio_mime)
        voice_prompt = await self._get_voice_clone_prompt(
            model, voice, ref_audio, ref_path, ref_text
        )
        gen_config = self._build_gen_config()

        # Flip the skip-uncond flag iff config says skip AND guidance
        # is effectively 0.  Flag is read inside the monkey-patched
        # forward — we set it per-call so a single process can still
        # run non-skip generations (e.g. for a test profile that
        # needs CFG for quality).
        skip_uncond = (
            settings.tts_skip_uncond_forward and gen_config.guidance_scale == 0
        )

        def _run() -> bytes:
            prev = getattr(model, "_autonoma_skip_uncond", False)
            if skip_uncond:
                model._autonoma_skip_uncond = True  # type: ignore[attr-defined]
            try:
                audio_list = model.generate(
                    text=text,
                    voice_clone_prompt=voice_prompt,
                    generation_config=gen_config,
                )
            finally:
                if skip_uncond:
                    model._autonoma_skip_uncond = prev  # type: ignore[attr-defined]
            if not audio_list:
                return b""
            # ``generate`` returns one ndarray per input text in the
            # batch; internal chunking (``audio_chunk_duration``) is
            # already cross-faded + concatenated inside each entry,
            # so for a single-text call ``audio_list[0]`` is the
            # complete utterance. Concatenating across the list is a
            # no-op today (we always submit a single text) but keeps
            # the code correct if a future caller submits a batch.
            parts: list[np.ndarray] = []
            for p in audio_list:
                arr = p if isinstance(p, np.ndarray) else np.asarray(p)
                if arr.dtype != np.float32:
                    arr = arr.astype(np.float32)
                parts.append(arr)
            pcm = parts[0] if len(parts) == 1 else np.concatenate(parts)
            return _pcm_to_wav_bytes(pcm, OMNIVOICE_SAMPLE_RATE)

        wav = await asyncio.to_thread(_run)
        if not wav:
            return
        # Emit in 32 KB slices so the browser MediaElement can start
        # decoding early. OmniVoice returns the full utterance (all
        # internal chunks concatenated) as a single buffer here, so
        # "streaming" is pipelined upload, not incremental synthesis —
        # still a latency win on large lines.
        SLICE = 32 * 1024
        for i in range(0, len(wav), SLICE):
            yield wav[i : i + SLICE]

    @property
    def device(self) -> str:
        return self._device or "(not yet loaded)"

    @property
    def dtype(self) -> str:
        return self._dtype or "(not yet loaded)"

    def cleanup(self) -> None:
        """Best-effort temp cleanup on shutdown."""
        try:
            for p in self._ref_cache.values():
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                os.rmdir(self._cache_dir)
            except OSError:
                pass
        except Exception:  # pragma: no cover — shutdown path
            logger.debug(
                "[tts] OmniVoice cleanup raised on shutdown (best-effort)",
                exc_info=True,
            )


# ── Process-wide singleton ────────────────────────────────────────────
# Model load is multi-GB and takes tens of seconds on CPU. Creating a
# new client per request pays that cost every time — nginx then cuts
# the request at its 60s proxy_read_timeout (504). Share one instance.

_shared_client: OmniVoiceTTSClient | None = None


def get_shared_client() -> OmniVoiceTTSClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = OmniVoiceTTSClient()
    return _shared_client


def shared_client_status() -> dict[str, object]:
    """Snapshot the shared client's load state for /api/health.

    Returns ``{"loaded": bool, "device": str, "dtype": str}``. Never
    instantiates the client — if nothing has touched the singleton yet
    we report ``loaded=False`` without triggering a load.
    """
    if _shared_client is None:
        return {"loaded": False, "device": "", "dtype": ""}
    return {
        "loaded": _shared_client._model is not None,
        "device": _shared_client._device,
        "dtype": _shared_client._dtype,
    }


async def warmup_shared_client() -> None:
    """Load the model into memory so the first real request doesn't
    pay the cold-load cost. Safe to call multiple times — ``_ensure_model``
    short-circuits once loaded. Intended for FastAPI startup hook."""
    client = get_shared_client()
    try:
        await client._ensure_model()
        logger.info(
            "[tts] OmniVoice warm-load complete (device=%s dtype=%s)",
            client.device,
            client.dtype,
        )
    except TTSError as exc:
        logger.warning("[tts] OmniVoice warm-load skipped: %s", exc)
    except Exception:  # pragma: no cover — startup path
        logger.exception("[tts] OmniVoice warm-load failed")
