"""Configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "AUTONOMA_", "env_file": ".env"}

    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.1
    output_dir: Path = Path("./output")
    max_agents: int = 8
    tick_rate: float = 0.15  # TUI animation tick in seconds


settings = Settings()
