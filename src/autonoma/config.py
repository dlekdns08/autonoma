"""Configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "AUTONOMA_", "env_file": ".env"}

    anthropic_api_key: str = ""

    def model_post_init(self, __context: object) -> None:
        """Also accept ANTHROPIC_API_KEY without the AUTONOMA_ prefix."""
        import os
        if not self.anthropic_api_key:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.1
    output_dir: Path = Path("./output")
    max_agents: int = 8
    tick_rate: float = 0.15  # TUI animation tick in seconds

    # ── Sandbox (agent code execution) ──
    # Sized for an 8-core / 12 GB host: at most 2 concurrent agent programs,
    # each capped at ~8s wall clock and ~384 MB memory.
    sandbox_max_concurrent: int = 2
    sandbox_wall_time_sec: float = 8.0
    sandbox_cpu_time_sec: int = 5
    sandbox_memory_mb: int = 384
    sandbox_max_output_bytes: int = 64 * 1024


settings = Settings()

# Apply concurrency cap to the sandbox module at import time.
from autonoma.sandbox import configure_concurrency as _configure_sandbox_concurrency  # noqa: E402

_configure_sandbox_concurrency(settings.sandbox_max_concurrent)
