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

# Keep the HF model cache on-disk across restarts so warmup is fast
# after the first download.  Override with AUTONOMA_HF_HOME in .env
# to share a cache across multiple checkouts.
export HF_HOME="${AUTONOMA_HF_HOME:-$REPO_ROOT/.hf-cache}"
mkdir -p "$HF_HOME"

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

HOST="${AUTONOMA_API_HOST:-127.0.0.1}"
PORT="${AUTONOMA_API_PORT:-3479}"

echo "[run_api_native] starting uvicorn on ${HOST}:${PORT} (HF_HOME=$HF_HOME)"
exec uv run --no-dev --extra tts uvicorn autonoma.api:app \
  --host "$HOST" \
  --port "$PORT"
