"""LLM layer after the 2026-10 refresh: effort, tools, refusal, history
translation, and the mid-conversation ``role: system`` fallback."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autonoma import llm as llm_module
from autonoma.llm import (
    LLMConfig,
    ToolSpec,
    _fold_system_messages,
    _normalize_anthropic,
    supports_server_fallback,
    text_blocks_to_str,
    to_openai_messages,
)

_TOOL = ToolSpec(
    name="idle",
    description="do nothing",
    input_schema={
        "type": "object",
        "properties": {"speech": {"type": "string"}},
        "required": ["speech"],
        "additionalProperties": False,
    },
)


def _client() -> llm_module.AnthropicLLMClient:
    client = llm_module.AnthropicLLMClient.__new__(llm_module.AnthropicLLMClient)
    client._client = MagicMock()
    return client


def _request(client, **overrides):
    kwargs = dict(
        model="claude-sonnet-5-5",
        max_tokens=100,
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        cache_system_prompt=False,
        effort="low",
        tools=[_TOOL],
        single_tool_call=True,
        streaming=True,
    )
    kwargs.update(overrides)
    return client._build_request(**kwargs)


def test_anthropic_request_has_no_sampling_params_and_maps_effort() -> None:
    client = _client()
    _, kw = _request(client)
    assert "temperature" not in kw
    assert "top_p" not in kw
    assert kw["output_config"] == {"effort": "low"}


def test_anthropic_tools_are_strict_eager_and_single_call() -> None:
    client = _client()
    _, kw = _request(client)
    tool = kw["tools"][0]
    assert tool["strict"] is True
    assert tool["eager_input_streaming"] is True
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_anthropic_non_streaming_tools_skip_eager_flag() -> None:
    client = _client()
    _, kw = _request(client, streaming=False)
    assert "eager_input_streaming" not in kw["tools"][0]


def test_server_fallback_only_on_supported_models() -> None:
    client = _client()
    ns, kw = _request(client, model="claude-sonnet-5-5")
    assert kw["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in kw["betas"]
    assert ns is client._client.beta.messages

    ns, kw = _request(client, model="claude-haiku-5-5")
    assert "fallbacks" not in kw
    assert "betas" not in kw
    assert ns is client._client.messages

    assert supports_server_fallback("claude-opus-5-5")
    assert supports_server_fallback("claude-fable-5-1")
    assert not supports_server_fallback("claude-sonnet-4-6")


def test_cache_flag_adds_system_breakpoint_and_auto_cache() -> None:
    client = _client()
    _, kw = _request(client, cache_system_prompt=True)
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["cache_control"] == {"type": "ephemeral"}


def test_normalize_reads_blocks_by_type_not_position() -> None:
    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="sig"),
            SimpleNamespace(type="text", text="Hello "),
            SimpleNamespace(type="text", text="world"),
            SimpleNamespace(type="tool_use", id="t1", name="idle", input={"speech": "zzz"}),
        ],
        stop_reason="tool_use",
        stop_details=None,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=7),
        model="claude-sonnet-5-5",
    )
    out = _normalize_anthropic(resp)
    assert out.text == "Hello world"
    assert [c.name for c in out.tool_calls] == ["idle"]
    assert out.tool_calls[0].input == {"speech": "zzz"}
    assert len(out.raw_content) == 4  # thinking block kept for replay
    assert out.cache_read_input_tokens == 7
    assert not out.refused


def test_normalize_surfaces_refusal_category() -> None:
    resp = SimpleNamespace(
        content=[],
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="nope"),
        usage=SimpleNamespace(input_tokens=1, output_tokens=0),
        model="m",
    )
    out = _normalize_anthropic(resp)
    assert out.refused
    assert out.refusal_category == "cyber"
    assert out.text == ""


def test_fold_system_messages_merges_into_previous_user_turn() -> None:
    folded = _fold_system_messages(
        [
            {"role": "user", "content": "situation"},
            {"role": "system", "content": "be terse"},
        ]
    )
    assert len(folded) == 1
    assert folded[0]["role"] == "user"
    assert "[OPERATOR NOTE] be terse" in folded[0]["content"]

    folded = _fold_system_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "s"}]},
            {"role": "system", "content": "note"},
        ]
    )
    assert folded[0]["content"][-1] == {"type": "text", "text": "[OPERATOR NOTE] note"}


@pytest.mark.asyncio
async def test_create_retries_without_system_role_when_model_rejects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic
    import httpx2

    client = _client()
    calls: list[dict] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            resp = httpx2.Response(400, request=httpx2.Request("POST", "https://x/y"))
            raise anthropic.BadRequestError(
                "role 'system' is not supported on this model", response=resp, body=None
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            stop_details=None,
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            model="m",
        )

    monkeypatch.setattr(client._client.messages, "create", fake_create)
    out = await client.create(
        model="claude-haiku-5-5",
        max_tokens=10,
        system="SYS",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "note"},
        ],
    )
    assert out.text == "ok"
    assert len(calls) == 2
    assert all(m["role"] != "system" for m in calls[1]["messages"])


def test_to_openai_messages_translates_tool_history() -> None:
    history = [
        {"role": "user", "content": "round 1"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "thinking aloud"},
                {"type": "tool_use", "id": "c1", "name": "idle", "input": {"speech": "hm"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "ok", "is_error": False},
                {"type": "text", "text": "round 2"},
            ],
        },
        {"role": "system", "content": "operator note"},
    ]
    out = to_openai_messages("SYS", history)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "round 1"}
    assert out[2]["role"] == "assistant"
    assert out[2]["tool_calls"][0]["function"]["name"] == "idle"
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}
    assert out[4] == {"role": "user", "content": "round 2"}
    assert out[5] == {"role": "system", "content": "operator note"}


def test_text_blocks_to_str_flattens_tool_results() -> None:
    assert text_blocks_to_str("plain") == "plain"
    assert (
        text_blocks_to_str(
            [{"type": "tool_result", "content": "ok"}, {"type": "text", "text": "hi"}]
        )
        == "[tool_result] ok\nhi"
    )


def test_llm_config_resolves_worker_model_and_protocol() -> None:
    cfg = LLMConfig(provider="anthropic", api_key="k", model="claude-sonnet-5-5")
    assert cfg.resolved_worker_model() == "claude-sonnet-5-5"
    assert cfg.resolved_decision_protocol() == "tool_use"
    cfg = LLMConfig(
        provider="vllm", api_key="", model="llama", base_url="http://x", worker_model="small"
    )
    assert cfg.resolved_worker_model() == "small"
    assert cfg.resolved_decision_protocol() == "json"
    cfg.decision_protocol = "tool_use"
    assert cfg.resolved_decision_protocol() == "tool_use"


def test_openai_build_kwargs_tools_and_effort() -> None:
    client = llm_module.OpenAILLMClient.__new__(llm_module.OpenAILLMClient)
    client._provider = "openai"
    kw = client._build_kwargs(
        model="o3-mini",
        max_tokens=50,
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        effort="low",
        tools=[_TOOL],
        single_tool_call=True,
    )
    assert kw["max_completion_tokens"] == 50
    assert kw["reasoning_effort"] == "low"
    assert "temperature" not in kw
    assert kw["tools"][0]["function"]["strict"] is True
    assert kw["parallel_tool_calls"] is False

    kw = client._build_kwargs(
        model="gpt-4o",
        max_tokens=50,
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        effort=None,
        tools=None,
        single_tool_call=True,
    )
    assert kw["temperature"] == 0.2
    assert "tools" not in kw
