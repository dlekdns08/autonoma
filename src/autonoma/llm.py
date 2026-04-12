"""LLM provider abstraction — supports Anthropic Claude, OpenAI, and vLLM.

All agents interact with a `BaseLLMClient` rather than the Anthropic SDK
directly, so the rest of the codebase stays provider-agnostic.

Usage:
    config = LLMConfig(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-4-20250514")
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
        try:
            import anthropic
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=messages,
            )
            return LLMResponse(
                text=response.content[0].text,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                stop_reason=str(response.stop_reason or "end_turn"),
            )
        except Exception as exc:
            import anthropic
            if isinstance(exc, anthropic.APIConnectionError):
                raise LLMConnectionError(str(exc)) from exc
            if isinstance(exc, anthropic.RateLimitError):
                raise LLMRateLimitError(str(exc)) from exc
            if isinstance(exc, anthropic.AuthenticationError):
                raise LLMAuthError(str(exc)) from exc
            raise


# ── OpenAI / vLLM implementation ─────────────────────────────────────────


class OpenAILLMClient(BaseLLMClient):
    """Works for both the OpenAI API and vLLM's OpenAI-compatible endpoint."""

    def __init__(self, api_key: str, base_url: str = "") -> None:
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {"api_key": api_key or "dummy"}
        if base_url:
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
            response = await self._client.chat.completions.create(
                model=model,
                messages=full_messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
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
            try:
                from openai import APIConnectionError, RateLimitError, AuthenticationError
                if isinstance(exc, APIConnectionError):
                    raise LLMConnectionError(str(exc)) from exc
                if isinstance(exc, RateLimitError):
                    raise LLMRateLimitError(str(exc)) from exc
                if isinstance(exc, AuthenticationError):
                    raise LLMAuthError(str(exc)) from exc
            except ImportError:
                pass
            raise


# ── Factory ───────────────────────────────────────────────────────────────


def create_llm_client(config: LLMConfig) -> BaseLLMClient:
    """Instantiate the right client for the given provider config."""
    if config.provider == "anthropic":
        return AnthropicLLMClient(api_key=config.api_key)
    if config.provider in ("openai", "vllm"):
        base_url = config.base_url if config.provider == "vllm" else ""
        return OpenAILLMClient(api_key=config.api_key, base_url=base_url)
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
