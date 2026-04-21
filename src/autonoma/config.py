"""Configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "AUTONOMA_", "env_file": ".env"}

    # ── Deployment environment ──
    # ``development`` expands the default CORS allow-list to include
    # localhost:3000/3478 so the local dev server and docker-compose web
    # container both work out of the box. ``production`` ships NO
    # defaults — the operator must supply ``cors_allow_origins`` as a
    # comma-separated list.
    environment: Literal["development", "production"] = "production"

    # Comma-separated list of origins to allow. Always merged with the
    # environment default above; wildcards are not supported to keep
    # ``allow_credentials=True`` safe.
    cors_allow_origins: str = ""

    # ── Admin account ──
    # Set this to enable an admin login that can use server-side API keys.
    # If empty, everyone must supply their own key.
    admin_password: str = ""

    # ── Cookie session auth ──
    # Secret used to sign the ``autonoma_session`` cookie. If unset, a
    # random dev-only value is generated at startup (with a loud log)
    # so cookies don't silently become forgeable across restarts in
    # production.
    session_secret: str | None = None

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

    # ── Persistent character database ──
    # Where the SQLite file lives. Default is a `data/` dir next to the
    # working directory so it survives across runs. In Docker this is
    # mounted to a named volume (`autonoma_db:/app/data`).
    data_dir: Path = Path("./data")
    db_filename: str = "autonoma.db"
    # Enable the persistent character registry. When False the swarm
    # behaves exactly as before (fresh characters every run, no memory
    # across sessions). Useful for tests and CI.
    persistent_characters: bool = True

    # ── TTS (agent voices) ──
    # Master kill-switch. When False no TTS module runs; agents are silent.
    tts_enabled: bool = False
    # Which backend to use: "azure" | "openai" | "none"
    # ``none`` is a stub that just emits audio-start/end events with no
    # audio bytes — useful for UI development without provider credentials.
    tts_provider: Literal["azure", "openai", "none"] = "none"
    # Azure Neural TTS credentials (Region + subscription key). ``region``
    # is e.g. "eastus", "koreacentral".
    tts_azure_key: str = ""
    tts_azure_region: str = ""
    # OpenAI TTS: reuses openai_api_key if empty.
    tts_openai_voice_default: str = "alloy"
    # Budgets: soft caps per round / per session. When exceeded the
    # worker drops the line and emits ``agent.speech_audio_dropped``.
    tts_char_budget_per_round: int = 800
    tts_char_budget_per_session: int = 20000
    # Rate limit: max TTS requests launched per minute, across the room.
    tts_rate_limit_per_minute: int = 40
    # When True, only the room owner hears TTS (server-side gate; viewer
    # audio is stripped). Used in Phase 4 once rooms exist — harmless now.
    tts_require_owner: bool = False
    # Language preference for voice selection pools. "ko" picks Korean
    # voices by default; callers can override per-character.
    tts_default_language: str = "ko"

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
