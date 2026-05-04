"""Unit tests for ``autonoma.voice.consent`` — Feature #15.

The router is exercised separately by integration tests; here we lock
down the three building blocks we control end-to-end without ASR or DB:

  - ``normalize_text`` strips punctuation / case / whitespace correctly,
  - ``phrase_similarity`` returns ratios in the expected bands,
  - ``watermark_audio`` + ``detect_watermark`` round-trip on a silent WAV.
"""

from __future__ import annotations

import io
import wave

import pytest

from autonoma.voice.consent import (
    CONSENT_SIMILARITY_THRESHOLD,
    detect_watermark,
    normalize_text,
    phrase_similarity,
    watermark_audio,
)


# ── normalize_text ────────────────────────────────────────────────────


def test_normalize_text_strips_punctuation_and_lowercases() -> None:
    out = normalize_text("Hello, World!  It's me.")
    # Apostrophe is punctuation → becomes a space → collapses out.
    assert out == "hello world it s me"


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  a\tb\nc   d  ") == "a b c d"


def test_normalize_text_handles_empty_and_whitespace_only() -> None:
    assert normalize_text("") == ""
    assert normalize_text("    ") == ""


def test_normalize_text_keeps_korean_letters() -> None:
    # Hangul is Unicode category "Lo" — must survive normalisation
    # untouched, otherwise consent verification on Korean clips is dead
    # on arrival.
    out = normalize_text("안녕, 세계!")
    assert out == "안녕 세계"


# ── phrase_similarity ────────────────────────────────────────────────


def test_phrase_similarity_identical_is_one() -> None:
    assert phrase_similarity("autonoma consent", "autonoma consent") == 1.0


def test_phrase_similarity_punctuation_does_not_lower_score() -> None:
    a = "I consent to using this voice in Autonoma."
    b = "i consent to using this voice in autonoma"
    assert phrase_similarity(a, b) == 1.0


def test_phrase_similarity_close_match_clears_threshold() -> None:
    # Drop one common word — should still clear 0.75.
    expected = "I consent to using this voice in Autonoma"
    transcript = "I consent to using voice in Autonoma"
    score = phrase_similarity(transcript, expected)
    assert score >= CONSENT_SIMILARITY_THRESHOLD


def test_phrase_similarity_unrelated_text_is_low() -> None:
    score = phrase_similarity(
        "the cat sat on the mat",
        "I consent to using this voice in Autonoma",
    )
    assert score < CONSENT_SIMILARITY_THRESHOLD


def test_phrase_similarity_empty_inputs_return_zero() -> None:
    assert phrase_similarity("", "anything") == 0.0
    assert phrase_similarity("anything", "") == 0.0


# ── Watermark round-trip ─────────────────────────────────────────────


def _silent_wav(duration_s: float = 1.0, sample_rate: int = 24000) -> bytes:
    """A silent PCM-16 mono WAV — every sample is 0x0000."""
    frames = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def test_watermark_round_trip_on_silent_wav() -> None:
    src = _silent_wav(duration_s=1.0)
    # Untouched silence should NOT be flagged — its LSBs are all 0.
    assert detect_watermark(src) is False

    stamped = watermark_audio(src)
    assert stamped != src  # actually mutated
    assert detect_watermark(stamped) is True


def test_watermark_preserves_wav_header_dimensions() -> None:
    src = _silent_wav(duration_s=1.0, sample_rate=24000)
    stamped = watermark_audio(src)
    with wave.open(io.BytesIO(stamped), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24000
        # Same number of frames in vs out — we mutate samples, not count.
        assert wf.getnframes() == 24000


def test_watermark_skips_non_pcm16_inputs() -> None:
    # 8-bit WAV — sampwidth=1 — should pass through unchanged.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(8000)
        wf.writeframes(b"\x80" * 8000)
    eight_bit = buf.getvalue()
    assert watermark_audio(eight_bit) == eight_bit
    assert detect_watermark(eight_bit) is False


def test_watermark_handles_garbage_bytes_gracefully() -> None:
    junk = b"this is not a wav file at all"
    # Non-WAV input should be returned unchanged and not detected.
    assert watermark_audio(junk) == junk
    assert detect_watermark(junk) is False


@pytest.mark.parametrize("duration_s", [1.0, 2.5, 5.0])
def test_watermark_round_trip_at_multiple_durations(duration_s: float) -> None:
    src = _silent_wav(duration_s=duration_s)
    stamped = watermark_audio(src)
    assert detect_watermark(stamped) is True
