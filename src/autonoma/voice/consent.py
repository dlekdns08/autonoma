"""Voice clone consent gate + inaudible LSB watermark — Feature #15.

Two pieces live here, deliberately framed as plain functions so the
router (``routers/voice_consent.py``) and any future synthesis hook can
both use them without going through extra plumbing:

1.  ``verify_consent`` — runs the uploaded "consent phrase" clip through
    the configured ASR provider and compares the transcript to the
    expected phrase from settings. A profile is only safe to use as a
    cloning reference once a caller-owned ``ConsentResult`` with
    ``ok=True`` exists for it.

2.  ``watermark_audio`` / ``detect_watermark`` — a *minimum-viable*
    fingerprint scheme: every 200th PCM-16 sample has its least-
    significant bit toggled to 1. The change is well below the noise
    floor of any real recording (≈ -90 dBFS perturbation, applied to
    one in 200 samples) but a round-trip detector can recover it. This
    lets us tag synthesised audio as "originated by Autonoma" without
    licensing a real watermark library.

Heavy ASR deps are imported lazily inside ``verify_consent`` so the
router can import this module at startup without forcing transformers
into the import graph.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import struct
import unicodedata
import wave
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# Threshold above which a transcript is considered to match the
# expected consent phrase. Picked empirically: SequenceMatcher.ratio()
# ≥ 0.75 tolerates the usual ASR hiccups (dropped particles, minor
# substitutions) while still rejecting "the cat sat on the mat" against
# the consent phrase.
CONSENT_SIMILARITY_THRESHOLD: float = 0.75

# Watermark cadence — every Nth PCM-16 sample is mutated. 200 samples at
# 24 kHz = ~8.3 ms between marks, so a 1-second clip carries ~120 bits
# of footprint. Large enough to survive light editing, small enough to
# stay inaudible.
_WATERMARK_STRIDE: int = 200


@dataclass(slots=True)
class ConsentResult:
    """Outcome of a single consent-phrase verification attempt.

    Persisted as JSON next to the profile so the consent-status endpoint
    can answer without re-running ASR.
    """

    ok: bool
    transcript: str
    expected_phrase: str
    similarity: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# ── Text normalisation ────────────────────────────────────────────────


# Anything that's not a letter, digit, or whitespace gets stripped.
# We use the Unicode category table so this works for Korean (Hangul
# syllables are ``Lo``) without us having to enumerate ranges.
def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    # L* = letters, N* = numbers, Zs handled separately as whitespace.
    return cat[0] in ("L", "N")


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Kept Unicode-aware so it handles Korean as cleanly as English.
    Hangul has no case so ``casefold`` is a no-op there; on English we
    fall back to ``casefold`` (handles "ß" → "ss" better than ``lower``).
    """
    if not s:
        return ""
    # Normalise to NFKC first so e.g. fullwidth ASCII collapses to plain
    # ASCII before we strip punctuation.
    s = unicodedata.normalize("NFKC", s).casefold()
    cleaned = "".join(ch if _is_word_char(ch) or ch.isspace() else " " for ch in s)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


# ── Similarity scoring ────────────────────────────────────────────────


def phrase_similarity(transcript: str, expected: str) -> float:
    """Return a 0..1 similarity between two phrases.

    Uses ``SequenceMatcher.ratio()`` over normalised text. SequenceMatcher
    is reasonable on character-level Korean (Hangul syllables compare
    cleanly) and forgiving on whitespace variation in English. Both
    inputs are normalised first so punctuation differences ("Autonoma."
    vs "autonoma") don't depress the score.
    """
    a = normalize_text(transcript)
    b = normalize_text(expected)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ── Consent verification ──────────────────────────────────────────────


def _expected_phrase_for(language: str) -> str:
    from autonoma.config import settings

    lang = (language or "").lower().strip()
    if lang.startswith("ko"):
        return settings.voice_consent_phrase_ko
    return settings.voice_consent_phrase_en


async def verify_consent(
    audio_bytes: bytes,
    language: str,
    asr_client: object | None = None,
) -> ConsentResult:
    """Transcribe ``audio_bytes`` and compare to the expected phrase.

    ``asr_client`` is optional: if omitted, we lazily fetch the global
    provider from ``autonoma.voice.asr`` (lazy so importing this module
    doesn't pull transformers into the graph). Tests pass an explicit
    stub to bypass the real model.

    The provider's ``.transcribe`` is synchronous — we run it on a worker
    thread so we don't block the event loop while the model crunches.
    """
    expected = _expected_phrase_for(language)

    if not audio_bytes:
        return ConsentResult(
            ok=False,
            transcript="",
            expected_phrase=expected,
            similarity=0.0,
            reason="empty_audio",
        )

    if asr_client is None:
        # Lazy import keeps consent.py importable without the ASR extras.
        from autonoma.voice.asr import get_asr_provider

        asr_client = get_asr_provider()

    try:
        # transcribe is sync (see CohereAsrProvider). Push it off the
        # event loop. We accept any object exposing ``.transcribe`` so
        # tests can pass a plain stub without subclassing AsrProvider.
        transcribe = asr_client.transcribe  # type: ignore[attr-defined]
        result = await asyncio.to_thread(
            transcribe, audio_bytes, language=language
        )
    except Exception as exc:
        logger.warning("[consent] ASR failed: %s", exc, exc_info=True)
        return ConsentResult(
            ok=False,
            transcript="",
            expected_phrase=expected,
            similarity=0.0,
            reason=f"asr_error: {type(exc).__name__}",
        )

    # The ASR provider returns a TranscriptionResult dataclass with
    # ``.text``; for stubs we accept either that or a raw string.
    transcript = getattr(result, "text", None)
    if transcript is None and isinstance(result, str):
        transcript = result
    transcript = (transcript or "").strip()

    score = phrase_similarity(transcript, expected)
    if score >= CONSENT_SIMILARITY_THRESHOLD:
        return ConsentResult(
            ok=True,
            transcript=transcript,
            expected_phrase=expected,
            similarity=score,
            reason="match",
        )
    return ConsentResult(
        ok=False,
        transcript=transcript,
        expected_phrase=expected,
        similarity=score,
        reason="phrase_mismatch",
    )


# ── Watermarking ──────────────────────────────────────────────────────


def _read_pcm16_wav(wav_bytes: bytes) -> tuple[wave._wave_params, bytes] | None:
    """Open ``wav_bytes`` if it's PCM-16; return (params, frames) or None."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            # Only mutate PCM-16 inputs. Anything else (24-bit PCM,
            # ADPCM, A-law, …) is left untouched — the safe-by-default
            # behaviour the spec asks for.
            if wf.getsampwidth() != 2:
                return None
            if wf.getcomptype() != "NONE":
                return None
            params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError):
        return None
    return params, frames


def watermark_audio(wav_bytes: bytes, payload: str = "autonoma") -> bytes:
    """Stamp a 1-LSB fingerprint onto every Nth sample.

    ``payload`` is accepted for API symmetry / future extension but not
    yet encoded — we only need a yes/no fingerprint right now, so every
    marked sample's LSB is forced to 1. Adding a per-bit payload later
    is a matter of XORing a hash of ``payload`` into the LSB stride.

    Non-PCM-16 inputs are returned unchanged so callers can pipe any TTS
    output through this without sniffing the format first.
    """
    parsed = _read_pcm16_wav(wav_bytes)
    if parsed is None:
        return wav_bytes
    params, frames = parsed
    nchannels = params.nchannels
    if nchannels < 1:
        return wav_bytes

    # Interpret as little-endian signed 16-bit. ``array`` would also
    # work but ``struct`` keeps us explicit about endianness, which the
    # WAV spec mandates.
    sample_count = len(frames) // 2
    if sample_count == 0:
        return wav_bytes
    samples = list(struct.unpack(f"<{sample_count}h", frames))

    # Stride across channel-interleaved samples — using the raw sample
    # index (not frame index) keeps the algorithm trivial and channel
    # alignment doesn't matter for a fingerprint.
    for i in range(0, sample_count, _WATERMARK_STRIDE):
        s = samples[i]
        # Force the LSB to 1. For negative values we operate on the
        # two's-complement representation by going through the unsigned
        # 16-bit view and re-signing afterwards.
        u = s & 0xFFFF
        u |= 0x0001
        if u >= 0x8000:
            s = u - 0x10000
        else:
            s = u
        samples[i] = s

    new_frames = struct.pack(f"<{sample_count}h", *samples)

    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(params.nchannels)
        wf.setsampwidth(params.sampwidth)
        wf.setframerate(params.framerate)
        wf.writeframes(new_frames)
    return out.getvalue()


def detect_watermark(wav_bytes: bytes) -> bool:
    """Return True if ``wav_bytes`` carries our LSB fingerprint.

    Heuristic: count how many of the strided positions have LSB=1. On
    untouched random/silent audio the expected ratio is ~0.5; our
    watermark forces it to ~1.0. We require ≥ 95% to call it a match,
    leaving slack for one or two samples that happened to round-trip
    through a re-encode with the LSB flipped.
    """
    parsed = _read_pcm16_wav(wav_bytes)
    if parsed is None:
        return False
    _params, frames = parsed
    sample_count = len(frames) // 2
    if sample_count < _WATERMARK_STRIDE:
        # Too short to carry a meaningful fingerprint.
        return False
    samples = struct.unpack(f"<{sample_count}h", frames)

    checked = 0
    hits = 0
    for i in range(0, sample_count, _WATERMARK_STRIDE):
        checked += 1
        if samples[i] & 0x0001:
            hits += 1
    if checked == 0:
        return False
    return (hits / checked) >= 0.95
