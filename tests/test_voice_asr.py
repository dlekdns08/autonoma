"""Tests for the voice ASR provider scaffold (Phase 2-#4 prep)."""

from __future__ import annotations

from autonoma.voice.asr import (
    CohereAsrProvider,
    NoopAsrProvider,
    TranscriptionResult,
    get_asr_provider,
    set_asr_provider_for_tests,
)


def test_noop_provider_is_ready_and_returns_empty():
    p = NoopAsrProvider()
    assert p.is_ready()
    result = p.transcribe(b"")
    assert isinstance(result, TranscriptionResult)
    assert result.text == ""
    assert result.model == "noop"


def test_cohere_provider_pinned_to_correct_model_id():
    # Guards against accidental rename — the user explicitly chose this
    # model and silently swapping it would be a regression.
    assert (
        CohereAsrProvider.MODEL_ID
        == "CohereLabs/cohere-transcribe-03-2026"
    )
    p = CohereAsrProvider()
    assert p.model_id == "CohereLabs/cohere-transcribe-03-2026"
    assert not p.is_ready()  # lazy-loaded — not loaded until first call


def test_set_provider_for_tests_overrides_singleton():
    set_asr_provider_for_tests(NoopAsrProvider())
    try:
        provider = get_asr_provider()
        assert isinstance(provider, NoopAsrProvider)
    finally:
        # Clear the override so other tests get the configured default.
        set_asr_provider_for_tests(None)
