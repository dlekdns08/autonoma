"""LLM provider abstraction — supports Anthropic Claude, OpenAI, and vLLM.

All agents interact with a `BaseLLMClient` rather than the Anthropic SDK
directly, so the rest of the codebase stays provider-agnostic.

Usage:
    config = LLMConfig(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-4-6")
    client = create_llm_client(config)
    response = await client.create(system="...", messages=[...], model=..., max_tokens=..., temperature=...)
    text = response.content[0].text   # same shape as the old Anthropic response
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ── Normalized LLM response ───────────────────────────────────────────────


@dataclass
class _ContentBlock:
    text: str


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class LLMResponse:
    """Provider-agnostic response — mirrors the Anthropic SDK shape so
    existing callers (``response.content[0].text``, ``response.usage``) work
    without modification."""

    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str = "end_turn"

    # Anthropic-compatible accessors
    @property
    def content(self) -> list[_ContentBlock]:
        return [_ContentBlock(text=self.text)]

    @property
    def usage(self) -> _Usage:
        return _Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


# ── Custom exceptions ─────────────────────────────────────────────────────


class LLMError(Exception):
    """Base class for LLM errors."""


class LLMConnectionError(LLMError):
    """Could not reach the LLM API endpoint."""


class LLMRateLimitError(LLMError):
    """Request was rate-limited by the provider."""


class LLMAuthError(LLMError):
    """Invalid or missing API credentials."""


# ── LLM configuration ─────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """Holds everything needed to create a client for a specific provider."""

    provider: Literal["anthropic", "openai", "vllm"]
    api_key: str
    model: str
    base_url: str = ""       # required for vLLM; ignored for others
    max_tokens: int = 8192
    temperature: float = 0.1


# ── Abstract client ───────────────────────────────────────────────────────


class BaseLLMClient(ABC):
    """Minimal async LLM interface used by all agents."""

    @abstractmethod
    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str,
        messages: list[dict[str, Any]],
    ) -> LLMResponse:
        """Send a completion request and return a normalized response."""
        ...


# ── Anthropic implementation ──────────────────────────────────────────────


# Per-process memo: models that Anthropic has told us don't accept
# `temperature`. The set is seeded lazily the first time we hit the
# "temperature is deprecated for this model" 400 for a given model name.
_ANTHROPIC_MODELS_NO_TEMPERATURE: set[str] = set()


def _is_temperature_deprecation_error(exc: Exception) -> bool:
    """Heuristic match for Anthropic's `temperature is deprecated` 400.

    We can't rely on a structured error code, so match the human message
    text. This is narrow on purpose — we only want to retry on THIS failure
    mode, never on generic 400s.
    """
    msg = str(exc).lower()
    return "temperature" in msg and "deprecated" in msg


class AnthropicLLMClient(BaseLLMClient):
    def __init__(self, api_key: str) -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None)

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str,
        messages: list[dict[str, Any]],
    ) -> LLMResponse:
        import anthropic

        # Fast path: if we've previously learned this model rejects
        # temperature, skip it up front so we don't burn a 400 every call.
        send_temperature = model not in _ANTHROPIC_MODELS_NO_TEMPERATURE

        async def _call(include_temperature: bool):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            }
            if include_temperature:
                kwargs["temperature"] = temperature
            return await self._client.messages.create(**kwargs)

        try:
            try:
                response = await _call(include_temperature=send_temperature)
            except anthropic.BadRequestError as exc:
                # Some Claude models (e.g. opus-4-7) have dropped support for
                # `temperature`. Detect the specific deprecation error, memo
                # the model, and retry once without the parameter.
                if send_temperature and _is_temperature_deprecation_error(exc):
                    logger.warning(
                        "Anthropic model %r rejected `temperature` "
                        "(deprecated). Retrying without it and memoizing.",
                        model,
                    )
                    _ANTHROPIC_MODELS_NO_TEMPERATURE.add(model)
                    response = await _call(include_temperature=False)
                else:
                    raise

            return LLMResponse(
                text=response.content[0].text,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                stop_reason=str(response.stop_reason or "end_turn"),
            )
        except Exception as exc:
            logger.error(
                "Anthropic create() failed: model=%s error=%s", model, exc,
            )
            if isinstance(exc, anthropic.APIConnectionError):
                raise LLMConnectionError(str(exc)) from exc
            if isinstance(exc, anthropic.RateLimitError):
                raise LLMRateLimitError(str(exc)) from exc
            if isinstance(exc, anthropic.AuthenticationError):
                raise LLMAuthError(str(exc)) from exc
            raise


# ── OpenAI / vLLM implementation ─────────────────────────────────────────


def _is_openai_reasoning_model(model: str) -> bool:
    """Return True for OpenAI reasoning-family models (o1/o3/...) that
    don't accept ``temperature`` and require ``max_completion_tokens``
    instead of ``max_tokens``.

    Extend the prefix tuple as new reasoning-family models ship.
    """
    lowered = model.lower()
    return lowered.startswith(("o1", "o3"))


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

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str,
        messages: list[dict[str, Any]],
    ) -> LLMResponse:
        try:
            full_messages = [{"role": "system", "content": system}] + list(messages)

            # Reasoning models (o1/o3...) don't accept `temperature` and use
            # `max_completion_tokens` instead of `max_tokens`.
            create_kwargs: dict[str, Any] = {
                "model": model,
                "messages": full_messages,
            }
            if _is_openai_reasoning_model(model):
                create_kwargs["max_completion_tokens"] = max_tokens
            else:
                create_kwargs["max_tokens"] = max_tokens
                create_kwargs["temperature"] = temperature

            response = await self._client.chat.completions.create(
                **create_kwargs,  # type: ignore[arg-type]
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return LLMResponse(
                text=text,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                stop_reason=response.choices[0].finish_reason or "stop",
            )
        except Exception as exc:
            # Log BEFORE re-raising so the Director/agent loop can surface
            # the real reason a fallback was taken (empty runs were previously
            # mysterious because the root cause was swallowed).
            logger.error(
                "OpenAI-compatible create() failed: provider=%s model=%s error=%s",
                self._provider,
                model,
                exc,
            )
            try:
                from openai import APIConnectionError, RateLimitError, AuthenticationError
            except ImportError:
                raise
            if isinstance(exc, APIConnectionError):
                raise LLMConnectionError(str(exc)) from exc
            if isinstance(exc, RateLimitError):
                raise LLMRateLimitError(str(exc)) from exc
            if isinstance(exc, AuthenticationError):
                raise LLMAuthError(str(exc)) from exc
            raise


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
            settings.anthropic_api_key if settings.provider == "anthropic"
            else settings.openai_api_key if settings.provider == "openai"
            else settings.vllm_api_key
        ),
        model=settings.model,
        base_url=settings.vllm_base_url if settings.provider == "vllm" else "",
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
    )
