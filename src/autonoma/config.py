"""Configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ``extra="ignore"`` — pydantic-settings 2.x defaults to ``forbid``,
    # which is too strict for our deploy. The shared ``.env`` file
    # carries variables consumed by other libraries (HF_TOKEN for
    # huggingface_hub, ANTHROPIC_API_KEY for anthropic SDK, etc.) that
    # are intentionally NOT mirrored as Settings fields — those SDKs
    # read straight from the OS environment. Without ``ignore`` every
    # such variable raises ``ValidationError: extra_forbidden`` and
    # crashes the launchd worker on startup.
    model_config = {
        "env_prefix": "AUTONOMA_",
        "env_file": ".env",
        "extra": "ignore",
    }

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
    # Which backend to use: "omnivoice" | "vibevoice" | "none"
    # - ``omnivoice``: zero-shot cloning via k2-fsa/OmniVoice. Requires
    #   the ``omnivoice`` + ``torch`` packages installed. Device auto-
    #   detected: cuda > mps (Apple Silicon) > cpu.
    # - ``vibevoice``: Microsoft VibeVoice-1.5B via transformers
    #   (``trust_remote_code=True``). Multi-speaker dialogue model
    #   with the same zero-shot reference-audio cloning interface as
    #   OmniVoice — drop-in replacement for the /podcast feature.
    # - ``none``: stub that emits audio-start/end events without bytes —
    #   keeps the UI flowing while the model isn't available.
    tts_provider: Literal["omnivoice", "vibevoice", "none"] = "none"

    # ── VibeVoice-specific knobs ──
    # Override the model id when you want a different size or fork.
    # Empty → falls back to ``microsoft/VibeVoice-Realtime-0.5B``
    # declared in ``autonoma.tts_vibevoice``. Note: the offline 1.5B
    # variant is NOT a drop-in replacement — see the long comment in
    # tts_vibevoice for why; pick a Realtime/streaming fork instead.
    vibevoice_model_id: str = ""
    # Output sample rate VibeVoice produces. The 1.5B model emits
    # 24 kHz mono; if you swap to a variant with a different rate,
    # set this so the WAV header matches.
    vibevoice_sample_rate: int = 24_000
    # Hard cap on generation length. VibeVoice's max-new-tokens window
    # roughly maps to total audio duration; ~4096 covers a ~30 s line
    # which is more than any single podcast turn we ever script.
    vibevoice_max_new_tokens: int = 4096
    # Budgets: soft caps per round / per session. When exceeded the
    # worker drops the line and emits ``agent.speech_audio_dropped``.
    #
    # Historical note: ``per_round`` was 800 and only reset by the
    # swarm's round tick (``SwarmRoom._tick``). Single-agent / direct
    # typing paths NEVER reset it, turning per_round into a
    # session-wide 800-char ceiling that silently dropped every
    # utterance after the first ~2 paragraphs. This was the main
    # driver of the "전체다 Speech로 안 되는" class of bug after the
    # OmniVoice perf tuning encouraged longer utterances. Bumped to
    # match MAX_TEXT_CHARS (2000) × a few utterances per round.
    tts_char_budget_per_round: int = 8000
    tts_char_budget_per_session: int = 200000
    # Rate limit: max TTS requests launched per minute, across the room.
    tts_rate_limit_per_minute: int = 40
    # When True, only the room owner hears TTS (server-side gate; viewer
    # audio is stripped). Used in Phase 4 once rooms exist — harmless now.
    tts_require_owner: bool = False
    # Language hint forwarded to the TTS client. OmniVoice is language-
    # agnostic (driven by the reference audio's language), so this is
    # advisory only — it still flows through for future backends.
    tts_default_language: str = "ko"

    # ── OmniVoice perf tuning ──
    # OmniVoice is a diffusion-style iterative decoder (NOT autoregressive),
    # so decode time scales LINEARLY with ``num_step``. Upstream default
    # is 32; 16 is indistinguishable for voice-cloning conditioning and
    # halves latency. Values below ~10 start to show artifacts.
    tts_num_step: int = 16
    # Classifier-free guidance. Upstream default 2.0 (CFG active) doubles
    # the forward pass because the model runs cond + uncond in a 2B
    # batch. 0.0 disables CFG entirely — combined with
    # ``tts_skip_uncond_forward`` below, the uncond half of the batch
    # is also skipped on the forward pass for a real ~2× speedup
    # (measured: 2.92s → 1.68s for a 6.5 s utterance on MPS).
    # Trade-off: g=0 is slightly less expressive than g=2 for voice
    # cloning. On ref-audio-driven profiles the difference is subtle,
    # but if voice characterisation degrades set this to 1.0 (skip
    # mixing math but still run CFG forward) or 2.0 (full CFG).
    tts_guidance_scale: float = 0.0
    # When True AND guidance_scale == 0, patch _generate_iterative to run
    # the forward on the cond half only (B rows instead of 2B). Falsy
    # guidance_scale is required because any non-zero scale needs uncond
    # logits to compute the CFG mix.
    tts_skip_uncond_forward: bool = True
    # OmniVoice has its own internal text chunking (splits long texts and
    # generates chunk-by-chunk, crossfades). Upstream thresholds 30 s /
    # 15 s are tuned for narration; for interactive dialogue we want
    # chunking to kick in MUCH earlier so the first sentence-ish starts
    # streaming to the browser while the rest is still synthesising.
    tts_chunk_threshold_s: float = 5.0
    tts_chunk_duration_s: float = 6.0
    # ``postprocess_output`` in OmniVoice does silence removal + fade
    # in/out + edge padding. For dialogue lines we don't need the extra
    # polish and it costs tens of ms per utterance.
    tts_postprocess: bool = False
    # Opt-in torch.compile on the speech LM. Can give 15-30 % more on
    # MPS but compile-time is multi-second and some graph captures still
    # fail on newer macOS. Off by default until empirically validated on
    # target hardware.
    tts_compile: bool = False

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

    # ── Live / broadcast (feature #1, #2) ──
    # Secret shared with a streaming automation platform (Aitum, Streamer.bot,
    # or a custom Twitch/YouTube bridge). Webhooks arriving without this
    # secret in ``X-Autonoma-Signature`` are rejected.
    live_webhook_secret: str = ""
    # Auto-clip settings: when the listed events fire, a ``live.clip``
    # event is emitted with the clip metadata so the browser /obs page
    # grabs the last N seconds of MediaRecorder buffer.
    live_autoclip_enabled: bool = True
    live_autoclip_seconds: int = 20

    # ── Vision agent (feature #3) ──
    vision_agent_enabled: bool = False
    # Minimum seconds between auto-comments so the agent doesn't spam.
    vision_agent_cooldown_s: int = 60

    # ── Persistence (feature #5) ──
    # When False, agent identities, diaries, and relationships are
    # session-scoped as they always were. When True they persist across
    # sessions keyed by (owner_user_id, role, name).
    persistent_agent_identities: bool = True

    # ── Standup podcast (feature #10) ──
    standup_enabled: bool = False
    standup_output_dir: Path = Path("./output/standups")

    # ── External bridges (feature #8) ──
    slack_signing_secret: str = ""     # Slack Events API verification
    discord_webhook_secret: str = ""   # shared secret for inbound Discord

    # ── Memory RAG (feature #5) ──
    # When True and ``sentence-transformers`` is installed, AgentMemory
    # exposes ``recall_semantic`` for embedding-based retrieval; the
    # diary recall in ``agents/base.py`` opts in via this flag. When
    # False or the package is missing we silently fall back to keyword
    # substring search (the historical behaviour).
    memory_rag_enabled: bool = True
    # Override the multilingual default if a deployment cares about a
    # specific language or wants a smaller/larger model. Empty → uses
    # the default declared in ``autonoma.memory.embeddings``.
    memory_rag_model: str = ""

    # ── Live chat moderation ──
    # Comma-separated list of substrings (case-insensitive). Any
    # incoming chat message whose lowercased text contains one of these
    # is dropped silently with a 200 OK so spammers don't learn the
    # filter. Empty disables the filter (default — keeps current
    # behaviour).
    live_chat_word_filter: str = ""
    # Comma-separated list of usernames (case-insensitive) whose
    # messages are dropped. Quick mute without an external bot.
    live_chat_user_mutes: str = ""

    # ── Voice ASR (Phase 2-#4) ──
    # Server-side speech-to-text for voice commands. The default uses
    # CohereLabs/cohere-transcribe-03-2026 via HuggingFace transformers
    # — the model is gated, so the operator must accept its license and
    # set HF_TOKEN before launch. Set provider="none" to disable voice
    # commands entirely (e.g. on machines without GPU).
    voice_asr_provider: Literal["cohere", "none"] = "cohere"
    voice_asr_model: str = "CohereLabs/cohere-transcribe-03-2026"
    # Language hint forwarded to the processor. The Cohere model is
    # multilingual but performs best when the source language is given.
    voice_asr_default_language: str = "ko"

    def model_post_init(self, __context: object) -> None:
        """Accept bare ANTHROPIC_API_KEY / OPENAI_API_KEY without the AUTONOMA_ prefix."""
        import os
        if not self.anthropic_api_key:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.openai_api_key:
            self.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        # Resolve workspace paths to absolute so they don't drift with CWD
        # changes (uvicorn --reload, scheduled tasks, tests using chdir).
        self.output_dir = self.output_dir.expanduser().resolve()
        self.data_dir = self.data_dir.expanduser().resolve()
        self.trace_dir = self.trace_dir.expanduser().resolve()
        self.standup_output_dir = self.standup_output_dir.expanduser().resolve()


settings = Settings()

# Apply concurrency cap to the sandbox module at import time.
from autonoma.sandbox import configure_concurrency as _configure_sandbox_concurrency  # noqa: E402

_configure_sandbox_concurrency(settings.sandbox_max_concurrent)
