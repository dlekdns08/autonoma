"""Configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "AUTONOMA_", "env_file": ".env"}

    # ── Admin account ──
    # Set this to enable an admin login that can use server-side API keys.
    # If empty, everyone must supply their own key.
    admin_password: str = ""

    # ── LLM provider keys (server-side; only accessible to admin) ──
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ── vLLM (OpenAI-compatible) ──
    vllm_base_url: str = ""   # e.g. http://localhost:8080/v1
    vllm_api_key: str = ""    # optional bearer token

    # ── Default provider/model used when admin authenticates ──
    provider: Literal["anthropic", "openai", "vllm"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 8192
    temperature: float = 0.1

    # ── Workspace ──
    output_dir: Path = Path("./output")

    # ── Swarm ──
    max_agents: int = 8
    tick_rate: float = 0.15  # TUI animation tick in seconds

    # ── Sandbox (agent code execution) ──
    sandbox_max_concurrent: int = 2
    sandbox_wall_time_sec: float = 8.0
    sandbox_cpu_time_sec: int = 5
    sandbox_memory_mb: int = 384
    sandbox_max_output_bytes: int = 64 * 1024

    # ── Observability ──
    trace_enabled: bool = True
    trace_dir: Path = Path("./traces")

    def model_post_init(self, __context: object) -> None:
        """Accept bare ANTHROPIC_API_KEY / OPENAI_API_KEY without the AUTONOMA_ prefix."""
        import os
        if not self.anthropic_api_key:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.openai_api_key:
            self.openai_api_key = os.environ.get("OPENAI_API_KEY", "")


settings = Settings()

# Apply concurrency cap to the sandbox module at import time.
from autonoma.sandbox import configure_concurrency as _configure_sandbox_concurrency  # noqa: E402

_configure_sandbox_concurrency(settings.sandbox_max_concurrent)
