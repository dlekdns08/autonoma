#!/usr/bin/env bash
# Native-mac launcher for the Autonoma API.
#
# Invoked by launchd via ~/Library/LaunchAgents/com.autonoma.api.plist
# (see scripts/install_native_api.sh).  Running natively — instead of
# inside a Docker container — is what unlocks MPS (Metal) acceleration
# for OmniVoice on Apple Silicon.  Docker Desktop on macOS does not
# pass MPS through to containers, so the in-container torch install is
# pinned to CPU wheels.  Host-side torch resolves to the MPS-capable
# arm64 build via pyproject.toml's uv source markers.
#
# Run directly for a one-off invocation:
#   scripts/run_api_native.sh
#
# Or let launchd own the lifecycle after ``scripts/install_native_api.sh``.

set -euo pipefail

# Resolve the repo root regardless of cwd.  launchd invokes this script
# with the plist's WorkingDirectory already set, but we want the same
# behaviour for ad-hoc shell runs too.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Load .env so the same deploy.yml-generated secrets flow into the
# native process.  ``set -a`` auto-exports every var assigned while
# active — matches how docker-compose reads env_file.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# Default TTS flags when .env is absent (dev) so the warmup hook in
# autonoma.api.lifespan actually loads OmniVoice on startup.
export AUTONOMA_TTS_ENABLED="${AUTONOMA_TTS_ENABLED:-true}"
export AUTONOMA_TTS_PROVIDER="${AUTONOMA_TTS_PROVIDER:-omnivoice}"

# Persistent state lives OUTSIDE the runner's workspace because
# ``actions/checkout@v4`` runs ``git clean -ffdx`` by default, which
# nukes gitignored dirs (``data/``, ``output/``, ``.hf-cache/``) on
# every deploy.  Parking these under ``$HOME/.autonoma/`` keeps user
# accounts, voice profiles, VRM bindings, and the 3GB OmniVoice
# weights alive across redeploys.  All three can be overridden via
# .env when tests or CI need ephemeral paths.
STATE_ROOT="${AUTONOMA_STATE_DIR:-$HOME/.autonoma}"
export AUTONOMA_DATA_DIR="${AUTONOMA_DATA_DIR:-$STATE_ROOT/data}"
export AUTONOMA_OUTPUT_DIR="${AUTONOMA_OUTPUT_DIR:-$STATE_ROOT/output}"
export HF_HOME="${AUTONOMA_HF_HOME:-$STATE_ROOT/hf-cache}"
mkdir -p "$AUTONOMA_DATA_DIR" "$AUTONOMA_OUTPUT_DIR" "$HF_HOME"

# Pick up Homebrew uv when launchd starts the process with a minimal
# PATH (launchd does not source the user's shell profile).
if ! command -v uv >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
    if [ -x "$candidate/uv" ]; then
      export PATH="$candidate:$PATH"
      break
    fi
  done
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "[run_api_native] uv not found on PATH" >&2
  exit 127
fi

# Sync deps (idempotent, fast when the lock is unchanged).  ``--extra
# tts`` pulls MPS-capable torch + OmniVoice on arm64 Macs.  We do NOT
# pass ``--frozen`` so a fresh checkout on a new machine can resolve
# platform-specific wheels that weren't in the original lock entry.
uv sync --no-dev --extra tts

# VibeVoice ships from a git repo that we deliberately keep out of
# pyproject's optional-dependencies (uv's lock resolver can't keep
# the source stable across our requires-python matrix). The sync
# above therefore strips it on every startup. Restore here only when
# the venv is missing it — first boot pays the git-clone cost (a few
# seconds), every subsequent boot is a no-op import check.
#
# IMPORTANT version-skew note: vibevoice 1.0.0 expects
# ``transformers ~=4.51``, while Cohere ASR (the default
# ``AUTONOMA_VOICE_ASR_PROVIDER=cohere``) needs transformers 5.x. The
# two can't coexist in one venv. When the operator opted into
# vibevoice we pin transformers below the 5.0 boundary AFTER ``uv
# sync`` runs, then re-install vibevoice so its transitive imports
# resolve. Operators who want both backends should run two API
# processes with separate venvs — that's outside this script's scope.
if [ "${AUTONOMA_TTS_PROVIDER:-}" = "vibevoice" ]; then
  # Force transformers back into the 4.51 line every time. This
  # overwrites the version ``uv sync`` just installed (which is a
  # transformers-5 build pulled by Cohere ASR's lockfile entry).
  # Only re-install when needed — checking the installed version
  # is a one-line probe and saves a network round-trip.
  current_tx=$(.venv/bin/python -c "import transformers; print(transformers.__version__)" 2>/dev/null || echo "")
  case "$current_tx" in
    4.51.*)
      ;;
    *)
      echo "[run_api_native] pinning transformers~=4.51 for vibevoice (was: $current_tx)…"
      uv pip install --python .venv/bin/python --quiet 'transformers~=4.51.0' || \
        echo "[run_api_native] WARN: transformers downgrade failed" >&2
      ;;
  esac
  if ! .venv/bin/python -c "import vibevoice" 2>/dev/null; then
    echo "[run_api_native] vibevoice missing — installing from git…"
    uv pip install --python .venv/bin/python --quiet \
      'vibevoice[streamingtts] @ git+https://github.com/microsoft/VibeVoice.git@main' || \
      echo "[run_api_native] WARN: vibevoice install failed; TTS will fall back" >&2
  fi
fi

HOST="${AUTONOMA_API_HOST:-127.0.0.1}"
PORT="${AUTONOMA_API_PORT:-3479}"

echo "[run_api_native] starting uvicorn on ${HOST}:${PORT} (HF_HOME=$HF_HOME)"
exec uv run --no-dev --extra tts uvicorn autonoma.api:app \
  --host "$HOST" \
  --port "$PORT"
