"""TTS worker, provider, and budget tests.

We exercise the worker end-to-end with the StubTTSClient so the tests
never hit a real provider. The stub yields zero audio bytes but still
fires start/end events, which is exactly the contract the browser sees
when a user has ``tts_provider="none"``.
"""

from __future__ import annotations

import asyncio

import pytest

from autonoma.event_bus import bus
from autonoma.tts import (
    AZURE_VOICE_POOL,
    OPENAI_VOICE_POOL,
    StubTTSClient,
    TTSConfig,
    _build_azure_ssml,
    create_tts_client,
    pick_voice_for,
)
from autonoma.tts_worker import TTSBudget, TTSWorker


# ── Provider abstraction ──────────────────────────────────────────────


def test_pick_voice_is_deterministic() -> None:
    """Same seed must always map to the same voice — the audience
    invariant is 'Zara always sounds like Zara'."""
    a1 = pick_voice_for("seed-zara", provider="azure", language="ko")
    a2 = pick_voice_for("seed-zara", provider="azure", language="ko")
    assert a1 == a2
    assert a1 in AZURE_VOICE_POOL["ko"]

    o1 = pick_voice_for("seed-noah", provider="openai", language="en")
    assert o1 == pick_voice_for("seed-noah", provider="openai", language="en")
    assert o1 in OPENAI_VOICE_POOL


def test_pick_voice_varies_across_seeds() -> None:
    """Different seeds should at least sometimes pick different voices."""
    voices = {
        pick_voice_for(f"seed-{i}", provider="azure", language="ko")
        for i in range(20)
    }
    assert len(voices) > 1, "deterministic mapping should still spread across pool"


def test_create_tts_client_falls_back_to_stub_on_bad_config() -> None:
    """An empty key must not raise — we silently fall back to the stub
    so the swarm keeps running even when TTS is misconfigured."""
    client = create_tts_client(TTSConfig(provider="azure", azure_key="", azure_region=""))
    assert isinstance(client, StubTTSClient)


def test_azure_ssml_escapes_xml_and_applies_mood() -> None:
    """SSML must escape user text and inject <mstts:express-as> for moods
    we have a style for. Otherwise we emit plain voice content."""
    happy = _build_azure_ssml(
        text="hi <there> & friends", voice="ko-KR-SunHiNeural", mood="happy", language="ko"
    )
    assert "&lt;there&gt;" in happy
    assert "&amp;" in happy
    assert 'style="cheerful"' in happy
    assert 'xml:lang="ko-KR"' in happy

    # Unknown mood → no express-as wrapper.
    plain = _build_azure_ssml(
        text="hello", voice="en-US-JennyNeural", mood="unknown_mood", language="en"
    )
    assert "express-as" not in plain
    assert 'xml:lang="en-US"' in plain


# ── Budget ────────────────────────────────────────────────────────────


def test_budget_enforces_round_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_round", 100)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_session", 10000)
    monkeypatch.setattr(tts_worker.settings, "tts_rate_limit_per_minute", 1000)

    b = TTSBudget()
    assert b.try_consume(60) is None
    # 60 + 50 > 100 → round budget hit.
    assert b.try_consume(50) == "round_budget"
    b.reset_round()
    # After a round flip the next utterance fits again.
    assert b.try_consume(50) is None


def test_budget_enforces_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_round", 100000)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_session", 100000)
    monkeypatch.setattr(tts_worker.settings, "tts_rate_limit_per_minute", 2)

    b = TTSBudget()
    assert b.try_consume(10) is None
    assert b.try_consume(10) is None
    assert b.try_consume(10) == "rate_limited"


# ── Worker end-to-end ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_emits_start_and_end_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub yields no chunks but the worker must still bracket each
    utterance with start/end events so the browser can show a
    'speaking' indicator."""
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_enabled", True)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_round", 1000)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_session", 10000)
    monkeypatch.setattr(tts_worker.settings, "tts_rate_limit_per_minute", 100)

    events: list[tuple[str, dict]] = []

    async def capture(name: str):
        async def h(**kw):
            events.append((name, kw))
        return h

    for ev in (
        "agent.speech_audio_start",
        "agent.speech_audio_chunk",
        "agent.speech_audio_end",
        "agent.speech_audio_dropped",
    ):
        bus.on(ev, await capture(ev))

    worker = TTSWorker(client=StubTTSClient())
    assert worker.enqueue(agent="Zara", text="hello", voice="stub-1", mood="happy")
    # Let the worker drain. Stub sleeps ~0.03s/char so "hello" needs ~150ms.
    await asyncio.sleep(0.3)
    await worker.stop()

    names = [e[0] for e in events]
    assert "agent.speech_audio_start" in names
    assert "agent.speech_audio_end" in names
    # Stub yields no chunks → no chunk events (and that's fine).
    assert "agent.speech_audio_dropped" not in names


@pytest.mark.asyncio
async def test_worker_assigns_monotonic_seq_per_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each utterance for a given agent gets a fresh, increasing seq so
    the browser can drop chunks from a superseded line."""
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_enabled", True)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_round", 1000)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_session", 10000)
    monkeypatch.setattr(tts_worker.settings, "tts_rate_limit_per_minute", 100)

    seqs: dict[str, list[int]] = {}

    async def on_start(**kw):
        seqs.setdefault(kw["agent"], []).append(kw["seq"])

    bus.on("agent.speech_audio_start", on_start)

    worker = TTSWorker(client=StubTTSClient())
    worker.enqueue(agent="Zara", text="a", voice="stub")
    worker.enqueue(agent="Zara", text="b", voice="stub")
    worker.enqueue(agent="Noah", text="c", voice="stub")
    await asyncio.sleep(0.4)
    await worker.stop()

    assert seqs["Zara"] == [1, 2]
    assert seqs["Noah"] == [1]


@pytest.mark.asyncio
async def test_worker_drops_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Master kill-switch: when tts_enabled is False, enqueue must
    refuse the job upfront rather than silently consuming budget."""
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_enabled", False)

    worker = TTSWorker(client=StubTTSClient())
    assert worker.enqueue(agent="Zara", text="hi", voice="stub") is False
    await worker.stop()


@pytest.mark.asyncio
async def test_worker_drops_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the round budget is exhausted the worker must emit a
    `dropped` event with reason='round_budget' instead of synthesizing.
    This is the contract the UI uses to show a 'budget hit' badge."""
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_enabled", True)
    # Allow the first line, drop the second.
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_round", 6)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_session", 10000)
    monkeypatch.setattr(tts_worker.settings, "tts_rate_limit_per_minute", 100)

    drops: list[dict] = []

    async def on_drop(**kw):
        drops.append(kw)

    bus.on("agent.speech_audio_dropped", on_drop)

    worker = TTSWorker(client=StubTTSClient())
    worker.enqueue(agent="Zara", text="hello!", voice="stub")  # 6 chars — fits
    worker.enqueue(agent="Zara", text="extra", voice="stub")   # over → dropped
    await asyncio.sleep(0.4)
    await worker.stop()

    reasons = [d["reason"] for d in drops]
    assert "round_budget" in reasons


@pytest.mark.asyncio
async def test_worker_drops_when_queue_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backpressure: when the queue saturates, the new job is dropped
    upfront with reason='queue_full'. We don't want to grow memory
    without bound when an agent goes wild on _say."""
    from autonoma import tts_worker

    monkeypatch.setattr(tts_worker.settings, "tts_enabled", True)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_round", 100000)
    monkeypatch.setattr(tts_worker.settings, "tts_char_budget_per_session", 100000)
    monkeypatch.setattr(tts_worker.settings, "tts_rate_limit_per_minute", 1000)
    # Tiny queue so we hit the cap immediately.
    monkeypatch.setattr(tts_worker, "MAX_QUEUE_DEPTH", 2)

    drops: list[dict] = []

    async def on_drop(**kw):
        drops.append(kw)

    bus.on("agent.speech_audio_dropped", on_drop)

    # Don't start the worker — we want jobs to pile in the queue.
    worker = TTSWorker(client=StubTTSClient())
    # Replace the internal queue with one bound by our patched constant.
    worker._queue = asyncio.Queue(maxsize=2)
    assert worker.enqueue(agent="A", text="x", voice="v") is True
    assert worker.enqueue(agent="A", text="x", voice="v") is True
    assert worker.enqueue(agent="A", text="x", voice="v") is False
    # Let the dropped emit settle (it's scheduled via create_task).
    await asyncio.sleep(0.05)
    await worker.stop()

    assert any(d["reason"] == "queue_full" for d in drops)
