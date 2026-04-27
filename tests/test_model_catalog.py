"""Tests for the dynamic model discovery / fallback in ``model_catalog``.

We mock the upstream SDK calls (``anthropic.Anthropic.models.list``,
``openai.OpenAI.models.list``) so the tests never touch the real network
and run deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from autonoma import model_catalog


@pytest.fixture(autouse=True)
def _clear_cache():
    """The module caches results across calls; isolate every test."""
    model_catalog._cache.clear()
    yield
    model_catalog._cache.clear()


# ── Anthropic ────────────────────────────────────────────────────────────


@dataclass
class _FakeAnthropicModel:
    id: str
    display_name: str | None = None


class _FakeAnthropicPage:
    def __init__(self, data: list[_FakeAnthropicModel]) -> None:
        self.data = data


class _FakeAnthropicClient:
    def __init__(self, *, api_key: str, page: _FakeAnthropicPage) -> None:
        self._page = page

        class _Models:
            def __init__(self, page: _FakeAnthropicPage) -> None:
                self._page = page

            def list(self, limit: int = 100) -> _FakeAnthropicPage:
                return self._page

        self.models = _Models(page)


def test_list_anthropic_parses_models(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakeAnthropicPage(
        [
            _FakeAnthropicModel("claude-haiku-4-5", "Claude Haiku 4.5"),
            _FakeAnthropicModel("claude-opus-4-7", "Claude Opus 4.7"),
            _FakeAnthropicModel("claude-sonnet-4-6", None),
        ]
    )

    import anthropic as _anthropic_mod

    def _factory(api_key: str) -> _FakeAnthropicClient:
        return _FakeAnthropicClient(api_key=api_key, page=page)

    monkeypatch.setattr(_anthropic_mod, "Anthropic", _factory)

    items, is_live = model_catalog.list_models("anthropic", api_key="sk-ant-fake")
    assert is_live is True
    values = [m["value"] for m in items]
    # All three IDs flow through.
    assert set(values) == {
        "claude-haiku-4-5",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
    }
    # Ordering: newest_first puts opus > sonnet > haiku.
    assert values.index("claude-opus-4-7") < values.index("claude-sonnet-4-6")
    assert values.index("claude-sonnet-4-6") < values.index("claude-haiku-4-5")
    # Display names are preserved when present, derived when absent.
    by_value = {m["value"]: m["label"] for m in items}
    assert by_value["claude-opus-4-7"] == "Claude Opus 4.7"
    assert "Sonnet" in by_value["claude-sonnet-4-6"]


def test_anthropic_missing_key_falls_back() -> None:
    items, is_live = model_catalog.list_models("anthropic", api_key="")
    assert is_live is False
    # Fallback list is the curated catalog — must include opus 4.7.
    values = [m["value"] for m in items]
    assert "claude-opus-4-7" in values


def test_anthropic_network_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the SDK raises, return the hardcoded fallback with is_live=False."""

    def _boom(api_key: str):
        raise RuntimeError("DNS unreachable")

    import anthropic as _anthropic_mod
    monkeypatch.setattr(_anthropic_mod, "Anthropic", _boom)

    items, is_live = model_catalog.list_models("anthropic", api_key="sk-ant-x")
    assert is_live is False
    assert any(m["value"].startswith("claude-") for m in items)


def test_cache_hit_returns_live_without_calling_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second call within TTL should not invoke the SDK at all."""
    page = _FakeAnthropicPage([_FakeAnthropicModel("claude-opus-4-7", None)])
    call_count = {"n": 0}

    def _factory(api_key: str) -> _FakeAnthropicClient:
        call_count["n"] += 1
        return _FakeAnthropicClient(api_key=api_key, page=page)

    import anthropic as _anthropic_mod
    monkeypatch.setattr(_anthropic_mod, "Anthropic", _factory)

    items1, live1 = model_catalog.list_models("anthropic", api_key="k")
    items2, live2 = model_catalog.list_models("anthropic", api_key="k")
    assert call_count["n"] == 1
    assert live1 is True and live2 is True
    assert items1 == items2


# ── OpenAI ───────────────────────────────────────────────────────────────


@dataclass
class _FakeOpenAIModel:
    id: str


class _FakeOpenAIPage:
    def __init__(self, data: list[_FakeOpenAIModel]) -> None:
        self.data = data


class _FakeOpenAIClient:
    def __init__(self, *, api_key: str = "", base_url: str | None = None,
                 page: _FakeOpenAIPage) -> None:
        self.api_key = api_key
        self.base_url = base_url

        class _Models:
            def __init__(self, page: _FakeOpenAIPage) -> None:
                self._page = page

            def list(self) -> _FakeOpenAIPage:
                return self._page

        self.models = _Models(page)


def test_openai_filters_non_chat_models(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakeOpenAIPage(
        [
            _FakeOpenAIModel("gpt-4o"),
            _FakeOpenAIModel("gpt-4o-mini"),
            _FakeOpenAIModel("text-embedding-3-large"),
            _FakeOpenAIModel("whisper-1"),
            _FakeOpenAIModel("tts-1"),
            _FakeOpenAIModel("o1-mini"),
        ]
    )

    import openai as _openai_mod

    def _factory(**kwargs):
        return _FakeOpenAIClient(page=page, **kwargs)

    monkeypatch.setattr(_openai_mod, "OpenAI", _factory)

    items, is_live = model_catalog.list_models("openai", api_key="sk-test")
    assert is_live is True
    values = [m["value"] for m in items]
    assert "gpt-4o" in values
    assert "gpt-4o-mini" in values
    assert "o1-mini" in values
    # Embedding / whisper / tts must be filtered out.
    assert "text-embedding-3-large" not in values
    assert "whisper-1" not in values
    assert "tts-1" not in values
