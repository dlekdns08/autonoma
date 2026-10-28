"""LLM provider abstraction — supports Anthropic Claude, OpenAI, and vLLM.

All agents interact with a `BaseLLMClient` rather than the Anthropic SDK
directly, so the rest of the codebase stays provider-agnostic.

Usage:
    config = LLMConfig(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-5-5")
    client = create_llm_client(config)
    response = await client.create(system="...", messages=[...], model=..., max_tokens=...)
    text = response.text

Design notes (2026-10 refresh):

- Sampling parameters (``temperature`` / ``top_p`` / ``top_k``) are gone
  from the Claude API as of ``anthropic-version: 2026-10-21`` (SDK 2.x).
  The abstraction still accepts an optional ``temperature`` for the
  OpenAI-compatible backends; the Anthropic client ignores it.
- Thinking depth is controlled with ``effort`` (``output_config.effort``
  on Anthropic, ``reasoning_effort`` on OpenAI reasoning models).
- Native tool use is the primary decision protocol. ``ToolSpec`` is the
  provider-neutral tool definition; ``LLMResponse.tool_calls`` carries
  the parsed calls and ``LLMResponse.raw_content`` the provider-native
  assistant blocks to replay in an append-only history.
- Responses are read by block *type*, never by position: 5.x models put
  ``thinking`` blocks ahead of text.
- ``stop_reason == "refusal"`` is surfaced (``LLMResponse.refused``) and,
  on models that support it, the server-side ``fallbacks: "default"``
  retry is enabled so a safety decline is re-run on a substitute model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, TypeVar

logger = logging.getLogger(__name__)

# ── Log sanitization ──────────────────────────────────────────────────────
#
# Provider SDKs occasionally surface the failing request payload in error
# messages. Without scrubbing, a 401/400 traceback in the logs can leak the
# API key (or the full Bearer header). These patterns cover Anthropic
# (``sk-ant-…``), OpenAI (``sk-…``/``sk-proj-…``), and generic bearer tokens.
_API_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"(?i)authorization['\"]?\s*[:=]\s*['\"][^'\"]{12,}"),
)


def _sanitize(text: Any) -> str:
    """Return ``str(text)`` with plausible API-key substrings redacted.

    This is a defence in depth — we'd rather leak "***REDACTED***" than a
    real token into rotating log files or stdout piped to a third-party
    aggregator.
    """
    s = str(text)
    for pat in _API_KEY_PATTERNS:
        s = pat.sub("***REDACTED***", s)
    return s


# ── Retry / timeout helper ────────────────────────────────────────────────

# Default timeouts tuned for streaming completions — thinking-on models
# can take 30-60s for a single pass, so we give plenty of runway.
LLM_REQUEST_TIMEOUT_SECONDS = 90.0
LLM_MAX_RETRIES = 2
LLM_RETRY_BASE_DELAY = 1.0

_T = TypeVar("_T")


async def _call_with_retry(
    do_call: Callable[[], Awaitable[_T]],
    *,
    label: str,
    timeout: float = LLM_REQUEST_TIMEOUT_SECONDS,
    max_retries: int = LLM_MAX_RETRIES,
) -> _T:
    """Invoke ``do_call`` with a wall-clock timeout and bounded retries.

    Retries on ``asyncio.TimeoutError``, ``LLMConnectionError`` and
    ``LLMRateLimitError`` with exponential backoff plus jitter. Auth
    errors and any unexpected exception propagate on first attempt —
    retrying a 401 just burns quota and racks up the retry counter on
    the provider side.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(do_call(), timeout=timeout)
        except asyncio.TimeoutError:
            last_exc = LLMConnectionError(f"{label}: request timed out after {timeout:.0f}s")
            logger.warning(
                "[llm] %s timed out (attempt %d/%d)",
                label,
                attempt + 1,
                max_retries + 1,
            )
        except (LLMConnectionError, LLMRateLimitError) as exc:
            last_exc = exc
            logger.warning(
                "[llm] %s transient error (attempt %d/%d): %s",
                label,
                attempt + 1,
                max_retries + 1,
                _sanitize(exc),
            )
        if attempt >= max_retries:
            break
        delay = LLM_RETRY_BASE_DELAY * (2**attempt) + random.uniform(0.0, 0.3)
        await asyncio.sleep(delay)
    if last_exc is None:
        raise RuntimeError("LLM retry exhausted without exception captured")
    raise last_exc


# ── Provider-neutral tool definitions ─────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    """A user-defined tool the model may call.

    ``input_schema`` is a JSON Schema object. When ``strict`` is on the
    schema must set ``additionalProperties: false`` and list every
    property in ``required`` — that is what lets both Anthropic and
    OpenAI guarantee the arguments validate exactly.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    strict: bool = True


@dataclass
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    input: dict[str, Any]


# ── Normalized LLM response ───────────────────────────────────────────────


@dataclass
class _ContentBlock:
    text: str


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0


@dataclass
class LLMResponse:
    """Provider-agnostic response.

    ``text`` is the concatenation of every text block (thinking blocks are
    skipped). ``tool_calls`` holds parsed tool invocations. ``raw_content``
    is the provider-native assistant content, suitable for appending to
    an append-only conversation history as ``{"role": "assistant",
    "content": raw_content}`` — for Anthropic that includes the thinking
    blocks, which must be replayed unchanged.
    """

    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str = "end_turn"
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: list[Any] = field(default_factory=list)
    refusal_category: str | None = None
    cache_read_input_tokens: int = 0
    model: str = ""

    # Anthropic-compatible accessors kept for legacy callers.
    @property
    def content(self) -> list[_ContentBlock]:
        return [_ContentBlock(text=self.text)]

    @property
    def usage(self) -> _Usage:
        return _Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens,
        )

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


@dataclass
class StreamEvent:
    """One event from :meth:`BaseLLMClient.stream_events`.

    - ``text``: a text delta (``text`` holds the fragment)
    - ``tool_input``: a fragment of a tool call's JSON input
    - ``final``: the stream is complete; ``response`` holds the
      normalized :class:`LLMResponse`
    """

    kind: Literal["text", "tool_input", "final"]
    text: str = ""
    response: LLMResponse | None = None


# ── Custom exceptions ─────────────────────────────────────────────────────


class LLMError(Exception):
    """Base class for LLM errors."""


class LLMConnectionError(LLMError):
    """Could not reach the LLM API endpoint."""


class LLMRateLimitError(LLMError):
    """Request was rate-limited by the provider."""


class LLMAuthError(LLMError):
    """Invalid or missing API credentials."""


class LLMRequestError(LLMError):
    """The provider rejected the request (HTTP 400). Not retried."""


# ── LLM configuration ─────────────────────────────────────────────────────

Effort = Literal["low", "medium", "high", "xhigh", "max"]
DecisionProtocol = Literal["auto", "tool_use", "json"]


@dataclass
class LLMConfig:
    """Holds everything needed to create a client for a specific provider.

    ``model`` is used by the Director (planning, synthesis). Worker agents
    use ``worker_model`` when set; otherwise they fall back to ``model``.
    ``effort`` / ``worker_effort`` control thinking depth per tier.
    ``temperature`` is only honoured by the OpenAI-compatible backends.
    """

    provider: Literal["anthropic", "openai", "vllm"]
    api_key: str
    model: str
    base_url: str = ""  # required for vLLM; ignored for others
    max_tokens: int = 16384
    temperature: float | None = None
    worker_model: str = ""
    effort: Effort | None = "high"
    worker_effort: Effort | None = "low"
    decision_protocol: DecisionProtocol = "auto"

    def resolved_worker_model(self) -> str:
        return self.worker_model or self.model

    def resolved_decision_protocol(self) -> Literal["tool_use", "json"]:
        """``auto`` → native tool use for the hosted providers, JSON text
        for vLLM (not every self-hosted server enables tool parsing)."""
        if self.decision_protocol != "auto":
            return self.decision_protocol
        return "json" if self.provider == "vllm" else "tool_use"


# ── Abstract client ───────────────────────────────────────────────────────


class BaseLLMClient(ABC):
    """Minimal async LLM interface used by all agents."""

    @abstractmethod
    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        cache_system_prompt: bool = False,
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
    ) -> LLMResponse:
        """Send a completion request and return a normalized response.

        ``messages`` may contain ``user``, ``assistant`` and (mid-
        conversation) ``system`` entries. ``user`` content may be a string
        or a list of blocks (``{"type": "text", ...}`` /
        ``{"type": "tool_result", "tool_use_id": ..., "content": ...,
        "is_error": bool}``). ``assistant`` content may be a string or the
        ``raw_content`` of a previous :class:`LLMResponse` from the same
        provider.

        ``cache_system_prompt`` requests provider-side caching of the
        system prompt. Anthropic implements this via ``cache_control``
        blocks; other providers ignore the flag. ``effort`` maps to the
        provider's thinking-depth control. ``tools`` enables native tool
        use; ``single_tool_call`` asks for at most one call per turn.
        """
        ...

    async def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        cache_system_prompt: bool = False,
        effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield text delta chunks as they arrive from the provider."""
        async for event in self.stream_events(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
            cache_system_prompt=cache_system_prompt,
            effort=effort,
        ):
            if event.kind == "text" and event.text:
                yield event.text

    async def stream_events(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        cache_system_prompt: bool = False,
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Yield :class:`StreamEvent` objects; the last one is ``final``.

        The default implementation falls back to a single ``create()``
        call, so subclasses only need to override when the underlying
        SDK supports real streaming.
        """
        response = await self.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
            cache_system_prompt=cache_system_prompt,
            effort=effort,
            tools=tools,
            single_tool_call=single_tool_call,
        )
        if response.text:
            yield StreamEvent("text", text=response.text)
        for call in response.tool_calls:
            yield StreamEvent("tool_input", text=json.dumps(call.input, ensure_ascii=False))
        yield StreamEvent("final", response=response)


# ── Shared helpers ────────────────────────────────────────────────────────


def text_blocks_to_str(content: Any) -> str:
    """Flatten a message ``content`` (str or block list) into plain text.

    Tool results are rendered as ``[tool_result] ...`` so the text stays
    informative when a provider can't take structured blocks.
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if btype == "text":
            parts.append(block["text"] if isinstance(block, dict) else getattr(block, "text", ""))
        elif btype == "tool_result":
            body = block.get("content", "") if isinstance(block, dict) else ""
            parts.append(f"[tool_result] {body}")
    return "\n".join(p for p in parts if p)


def _fold_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback for providers/models without mid-conversation ``system``
    messages: merge each one into the preceding ``user`` turn as an
    ``[OPERATOR NOTE]`` text block (the pre-``role: system`` pattern)."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "system":
            out.append(dict(msg))
            continue
        note = f"[OPERATOR NOTE] {text_blocks_to_str(msg.get('content', ''))}"
        if out and out[-1].get("role") == "user":
            prev = out[-1]
            content = prev.get("content", "")
            if isinstance(content, str):
                prev["content"] = f"{content}\n\n{note}"
            else:
                prev["content"] = list(content) + [{"type": "text", "text": note}]
        else:
            out.append({"role": "user", "content": note})
    return out


# ── Anthropic implementation ──────────────────────────────────────────────

# Models whose safety classifiers can decline a request and that accept
# the server-side ``fallbacks: "default"`` retry (Claude API only).
_FALLBACK_MODEL_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
)
_SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Error text the API returns when a model doesn't take ``role: system``
# inside ``messages`` (Claude Sonnet 5 and older generations).
_SYSTEM_ROLE_UNSUPPORTED_RE = re.compile(r"role\s*'?\"?system'?\"?\s+is not supported", re.I)


def supports_server_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_MODEL_PREFIXES)


def _anthropic_tool(spec: ToolSpec, *, streaming: bool) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
    }
    if spec.strict:
        tool["strict"] = True
    if streaming:
        # Large inputs (file bodies, code) stream as they are generated
        # instead of arriving in one burst once the server has buffered
        # and validated them. The client validates the parsed input.
        tool["eager_input_streaming"] = True
    return tool


def _anthropic_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Pass through, dropping our private ``_``-prefixed bookkeeping keys."""
    return {k: v for k, v in msg.items() if not k.startswith("_")}


def _normalize_anthropic(resp: Any) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in resp.content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            raw_input = block.input
            tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    input=dict(raw_input) if isinstance(raw_input, dict) else {},
                )
            )
    stop_reason = str(resp.stop_reason or "end_turn")
    category: str | None = None
    if stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        category = getattr(details, "category", None) if details is not None else None
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        text="".join(text_parts),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        raw_content=list(resp.content or []),
        refusal_category=category,
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        model=str(getattr(resp, "model", "") or ""),
    )


class AnthropicLLMClient(BaseLLMClient):
    def __init__(self, api_key: str) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key or None)

    # ── request assembly ──

    def _build_request(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        cache_system_prompt: bool,
        effort: str | None,
        tools: list[ToolSpec] | None,
        single_tool_call: bool,
        streaming: bool,
    ) -> tuple[Any, dict[str, Any]]:
        """Return ``(messages_namespace, kwargs)`` for the SDK call.

        The beta namespace is used only when a beta feature (server-side
        refusal fallback) applies to the model.
        """
        system_field: Any
        if cache_system_prompt and system:
            # One explicit breakpoint on the stable system prefix. The
            # conversation tail is covered by the top-level automatic
            # ``cache_control`` below.
            system_field = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_field = system

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_field,
            "messages": [_anthropic_message(m) for m in messages],
        }
        if cache_system_prompt:
            kwargs["cache_control"] = {"type": "ephemeral"}
        if effort:
            kwargs["output_config"] = {"effort": effort}
        if tools:
            kwargs["tools"] = [_anthropic_tool(t, streaming=streaming) for t in tools]
            # Forced tool choice (``any`` / ``tool``) is rejected on the
            # 5.5 generation; ``auto`` plus a prompt instruction is the
            # supported way to steer. ``disable_parallel_tool_use`` keeps
            # the one-action-per-turn contract.
            kwargs["tool_choice"] = {
                "type": "auto",
                "disable_parallel_tool_use": bool(single_tool_call),
            }

        betas: list[str] = []
        if supports_server_fallback(model):
            betas.append(_SERVER_FALLBACK_BETA)
            kwargs["fallbacks"] = "default"
        if betas:
            kwargs["betas"] = betas
            return self._client.beta.messages, kwargs
        return self._client.messages, kwargs

    @staticmethod
    def _translate_error(exc: Exception) -> Exception:
        import anthropic

        if isinstance(exc, anthropic.APIConnectionError):
            return LLMConnectionError(_sanitize(exc))
        if isinstance(exc, anthropic.RateLimitError):
            return LLMRateLimitError(_sanitize(exc))
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMAuthError(_sanitize(exc))
        if isinstance(exc, anthropic.BadRequestError):
            return LLMRequestError(_sanitize(exc))
        if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
            return LLMConnectionError(_sanitize(exc))
        return exc

    @staticmethod
    def _system_role_unsupported(exc: Exception) -> bool:
        return bool(_SYSTEM_ROLE_UNSUPPORTED_RE.search(str(exc)))

    # ── non-streaming ──

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,  # noqa: ARG002 — removed from the Claude API (2026-10-21)
        cache_system_prompt: bool = False,
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
    ) -> LLMResponse:
        import anthropic

        namespace, kwargs = self._build_request(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            cache_system_prompt=cache_system_prompt,
            effort=effort,
            tools=tools,
            single_tool_call=single_tool_call,
            streaming=False,
        )

        async def _do() -> Any:
            try:
                try:
                    return await namespace.create(**kwargs)
                except anthropic.BadRequestError as exc:
                    # Models without mid-conversation ``role: system``
                    # support: fold the notes into the user turn and retry.
                    has_system = any(m.get("role") == "system" for m in kwargs["messages"])
                    if has_system and self._system_role_unsupported(exc):
                        logger.info(
                            "Anthropic model %r rejected mid-conversation system "
                            "messages; folding them into the user turn.",
                            model,
                        )
                        kwargs["messages"] = _fold_system_messages(kwargs["messages"])
                        return await namespace.create(**kwargs)
                    raise
            except Exception as exc:  # normalize provider errors
                translated = self._translate_error(exc)
                if translated is exc:
                    raise
                raise translated from exc

        try:
            response = await _call_with_retry(_do, label=f"anthropic.create({model})")
            return _normalize_anthropic(response)
        except LLMError:
            raise
        except Exception as exc:
            logger.error(
                "Anthropic create() failed: model=%s error=%s",
                model,
                _sanitize(exc),
            )
            raise

    # ── streaming ──

    async def stream_events(  # type: ignore[override]
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,  # noqa: ARG002 — removed from the Claude API (2026-10-21)
        cache_system_prompt: bool = False,
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        import anthropic

        namespace, kwargs = self._build_request(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            cache_system_prompt=cache_system_prompt,
            effort=effort,
            tools=tools,
            single_tool_call=single_tool_call,
            streaming=True,
        )

        async def _run() -> AsyncIterator[StreamEvent]:
            async with namespace.stream(**kwargs) as stream_ctx:
                async for event in stream_ctx:
                    etype = getattr(event, "type", None)
                    if etype == "text":
                        yield StreamEvent("text", text=event.text)
                    elif etype == "input_json":
                        yield StreamEvent("tool_input", text=event.partial_json)
                final = await stream_ctx.get_final_message()
            yield StreamEvent("final", response=_normalize_anthropic(final))

        folded = False
        json_retries = 0
        while True:
            try:
                async for ev in _run():
                    yield ev
                return
            except anthropic.BadRequestError as exc:
                has_system = any(m.get("role") == "system" for m in kwargs["messages"])
                if has_system and not folded and self._system_role_unsupported(exc):
                    folded = True
                    kwargs["messages"] = _fold_system_messages(kwargs["messages"])
                    continue
                raise LLMRequestError(_sanitize(exc)) from exc
            except ValueError as exc:
                # Eager input streaming hands the client JSON the SDK could
                # not parse at all. There is no tool_use_id to answer, so
                # re-issue the turn (bounded), then surface as a parse error.
                json_retries += 1
                if json_retries > 1:
                    raise ValueError(f"tool input JSON unparseable: {exc}") from exc
                logger.warning("[llm] unparseable tool input from %s; retrying once", model)
                continue
            except anthropic.APIConnectionError as exc:
                raise LLMConnectionError(_sanitize(exc)) from exc
            except anthropic.RateLimitError as exc:
                raise LLMRateLimitError(_sanitize(exc)) from exc
            except anthropic.AuthenticationError as exc:
                raise LLMAuthError(_sanitize(exc)) from exc


# ── OpenAI / vLLM implementation ─────────────────────────────────────────


def _is_openai_reasoning_model(model: str) -> bool:
    """Return True for OpenAI reasoning-family models (o1/o3/...) that
    don't accept ``temperature`` and require ``max_completion_tokens``
    instead of ``max_tokens``.

    Extend the prefix tuple as new reasoning-family models ship.
    """
    lowered = model.lower()
    return lowered.startswith(("o1", "o3", "o4"))


def _openai_tool(spec: ToolSpec) -> dict[str, Any]:
    fn: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.input_schema,
    }
    if spec.strict:
        fn["strict"] = True
    return {"type": "function", "function": fn}


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the provider-neutral history into chat-completions shape.

    - mid-conversation ``system`` entries pass through (OpenAI accepts
      system messages anywhere);
    - ``user`` block lists split into ``tool`` messages (one per
      ``tool_result``) followed by a ``user`` message with the text;
    - ``assistant`` block lists become ``content`` + ``tool_calls``.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            out.append({"role": "system", "content": text_blocks_to_str(content)})
        elif role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            texts: list[str] = []
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    body = block.get("content", "")
                    if not isinstance(body, str):
                        body = json.dumps(body, ensure_ascii=False)
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": body,
                        }
                    )
                elif block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            texts = []
            tool_calls: list[dict[str, Any]] = []
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                            },
                        }
                    )
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


class OpenAILLMClient(BaseLLMClient):
    """Works for both the OpenAI API and vLLM's OpenAI-compatible endpoint."""

    def __init__(self, api_key: str, base_url: str | None = "", provider: str = "openai") -> None:
        from openai import AsyncOpenAI

        # Normalize empty base_url → None so the SDK's default-URL path kicks in.
        base_url = base_url or None

        if provider == "openai":
            if not api_key:
                raise ValueError("OpenAI requires an API key")
            effective_key = api_key
        elif provider == "vllm":
            if not api_key:
                logger.info(
                    "vllm client: empty api_key; using placeholder 'dummy' "
                    "(self-hosted vllm typically has no auth)"
                )
                effective_key = "dummy"
            else:
                effective_key = api_key
        else:
            # Unknown provider; keep old permissive behavior for safety.
            effective_key = api_key or "dummy"

        self._provider = provider
        kwargs: dict[str, Any] = {"api_key": effective_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    def _build_kwargs(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None,
        effort: str | None,
        tools: list[ToolSpec] | None,
        single_tool_call: bool,
    ) -> dict[str, Any]:
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(system, messages),
        }
        if _is_openai_reasoning_model(model):
            create_kwargs["max_completion_tokens"] = max_tokens
            if effort in ("low", "medium", "high"):
                create_kwargs["reasoning_effort"] = effort
        else:
            create_kwargs["max_tokens"] = max_tokens
            if temperature is not None:
                create_kwargs["temperature"] = temperature
        if tools:
            create_kwargs["tools"] = [_openai_tool(t) for t in tools]
            create_kwargs["tool_choice"] = "auto"
            if single_tool_call:
                create_kwargs["parallel_tool_calls"] = False
        return create_kwargs

    @staticmethod
    def _normalize(message: Any, usage: Any, finish_reason: str | None) -> LLMResponse:
        text = getattr(message, "content", None) or ""
        tool_calls: list[ToolCall] = []
        raw_content: list[Any] = []
        if text:
            raw_content.append({"type": "text", "text": text})
        for tc in getattr(message, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") or ""
            try:
                args = _parse_tool_arguments(getattr(fn, "arguments", "") or "")
            except json.JSONDecodeError:
                args = {}
            call = ToolCall(id=getattr(tc, "id", "") or "", name=name, input=args)
            tool_calls.append(call)
            raw_content.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
            )
        stop = finish_reason or "stop"
        if stop == "content_filter":
            stop = "refusal"
        elif stop == "length":
            stop = "max_tokens"
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0 if usage else 0,
            stop_reason=stop,
            tool_calls=tool_calls,
            raw_content=raw_content,
        )

    @staticmethod
    def _translate_error(exc: Exception) -> Exception:
        from openai import APIConnectionError, AuthenticationError, BadRequestError, RateLimitError

        if isinstance(exc, APIConnectionError):
            return LLMConnectionError(_sanitize(exc))
        if isinstance(exc, RateLimitError):
            return LLMRateLimitError(_sanitize(exc))
        if isinstance(exc, AuthenticationError):
            return LLMAuthError(_sanitize(exc))
        if isinstance(exc, BadRequestError):
            return LLMRequestError(_sanitize(exc))
        return exc

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        cache_system_prompt: bool = False,  # noqa: ARG002 — accepted for API parity; OpenAI/vLLM don't expose cache_control
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
    ) -> LLMResponse:
        create_kwargs = self._build_kwargs(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
            effort=effort,
            tools=tools,
            single_tool_call=single_tool_call,
        )

        async def _do() -> Any:
            try:
                return await self._client.chat.completions.create(
                    **create_kwargs,  # type: ignore[arg-type]
                )
            except Exception as exc:
                translated = self._translate_error(exc)
                if translated is exc:
                    raise
                raise translated from exc

        try:
            response = await _call_with_retry(_do, label=f"openai.create({self._provider}/{model})")
            choice = response.choices[0]
            return self._normalize(choice.message, response.usage, choice.finish_reason)
        except LLMError:
            raise
        except Exception as exc:
            # Log sanitized before re-raising so the Director/agent loop
            # surfaces the real reason a fallback was taken.
            logger.error(
                "OpenAI-compatible create() failed: provider=%s model=%s error=%s",
                self._provider,
                model,
                _sanitize(exc),
            )
            raise

    async def stream_events(  # type: ignore[override]
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        cache_system_prompt: bool = False,  # noqa: ARG002 — accepted for API parity; OpenAI/vLLM don't expose cache_control
        effort: str | None = None,
        tools: list[ToolSpec] | None = None,
        single_tool_call: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        create_kwargs = self._build_kwargs(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
            effort=effort,
            tools=tools,
            single_tool_call=single_tool_call,
        )
        create_kwargs["stream"] = True
        create_kwargs["stream_options"] = {"include_usage": True}

        text_parts: list[str] = []
        # index → {"id", "name", "arguments"}
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: Any = None

        try:
            stream_ctx = await self._client.chat.completions.create(**create_kwargs)
            async with stream_ctx:
                async for chunk in stream_ctx:
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    if delta is None:
                        continue
                    if delta.content:
                        text_parts.append(delta.content)
                        yield StreamEvent("text", text=delta.content)
                    for tc in delta.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        fn = tc.function
                        if fn is not None:
                            if fn.name:
                                slot["name"] = fn.name
                            if fn.arguments:
                                slot["arguments"] += fn.arguments
                                yield StreamEvent("tool_input", text=fn.arguments)
        except Exception as exc:
            translated = self._translate_error(exc)
            if translated is exc:
                raise
            raise translated from exc

        # Assemble a chat-completions-shaped message for the normalizer.
        class _Fn:
            def __init__(self, name: str, arguments: str) -> None:
                self.name = name
                self.arguments = arguments

        class _Call:
            def __init__(self, id_: str, name: str, arguments: str) -> None:
                self.id = id_
                self.function = _Fn(name, arguments)

        class _Msg:
            def __init__(self) -> None:
                self.content = "".join(text_parts)
                self.tool_calls = [
                    _Call(c["id"], c["name"], c["arguments"]) for _, c in sorted(calls.items())
                ]

        yield StreamEvent("final", response=self._normalize(_Msg(), usage, finish_reason))


# ── Factory ───────────────────────────────────────────────────────────────


def create_llm_client(config: LLMConfig) -> BaseLLMClient:
    """Instantiate the right client for the given provider config."""
    if config.provider == "anthropic":
        return AnthropicLLMClient(api_key=config.api_key)
    if config.provider in ("openai", "vllm"):
        base_url = config.base_url if config.provider == "vllm" else ""
        return OpenAILLMClient(
            api_key=config.api_key,
            base_url=base_url,
            provider=config.provider,
        )
    raise ValueError(f"Unknown LLM provider: {config.provider!r}")


def llm_config_from_settings() -> LLMConfig:
    """Build a LLMConfig from the global server settings (for CLI / admin use)."""
    from autonoma.config import settings

    return LLMConfig(
        provider=settings.provider,
        api_key=(
            settings.anthropic_api_key
            if settings.provider == "anthropic"
            else settings.openai_api_key
            if settings.provider == "openai"
            else settings.vllm_api_key
        ),
        model=settings.model,
        base_url=settings.vllm_base_url if settings.provider == "vllm" else "",
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        worker_model=settings.resolved_worker_model(),
        effort=settings.director_effort or None,
        worker_effort=settings.worker_effort or None,
        decision_protocol=settings.decision_protocol,
    )
