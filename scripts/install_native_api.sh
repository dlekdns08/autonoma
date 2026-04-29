
#!/usr/bin/env bash
# One-shot installer for the native-mac Autonoma API launchd agent.
#
# Writes ~/Library/LaunchAgents/com.autonoma.api.plist with the repo's
# absolute path resolved in, then registers it with launchd via the
# legacy ``load`` / ``unload`` API.
#
# Why ``load`` / ``unload`` and not the modern ``bootstrap`` /
# ``bootout``:  when invoked from a GitHub self-hosted runner (itself
# a LaunchAgent subprocess), ``bootstrap gui/<uid>`` often fails with
# ``Input/output error`` (errno 5) because the runner's launchd
# session is not a full Aqua/GUI context.  The legacy commands act on
# the caller's own launchd domain and work from any user-context
# subprocess — runner, ssh shell, Terminal.app — without surprises.
#
# The plist is always rewritten from scratch so repeated deploys
# cannot drift: path changes (workspace move) take effect on the next
# run.  Safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LABEL="com.autonoma.api"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
# Logs live outside the repo so ``actions/checkout`` (which does
# ``git clean -ffdx`` by default) cannot wipe history across deploys.
# Matches the $HOME/.autonoma state root used by run_api_native.sh.
LOG_DIR="${AUTONOMA_STATE_DIR:-$HOME/.autonoma}/logs"

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

# ── Stop any currently-loaded instance ───────────────────────────────
# ``unload`` is a no-op if the service isn't registered; ``|| true``
# tolerates the "Could not find specified service" line on a fresh
# machine.  Using both ``-w`` (clear any ``Disabled`` override) keeps
# the service eligible for auto-load at next login.
if [ -f "$PLIST_PATH" ]; then
  echo "[install_native_api] unloading existing ${LABEL}"
  launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
fi

# Belt-and-suspenders: even if the file was missing, the label might
# still be registered (e.g. loaded from a now-deleted plist).  ``remove``
# drops the registration by name without needing the file.
launchctl remove "$LABEL" 2>/dev/null || true

# Give launchd a beat to actually tear down the old process so the
# subsequent ``load`` doesn't race and see a stale registration.
sleep 1

# ── Write a fresh plist with current paths ────────────────────────────
# The plist is overwritten unconditionally so a repo move (e.g. the
# GH runner's _work path vs. a dev checkout) can't leave stale paths.
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <!-- Run the launcher under a login shell so /etc/paths and the
       user's profile populate PATH (Homebrew uv, etc.). -->
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>exec "${REPO_ROOT}/scripts/run_api_native.sh"</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${REPO_ROOT}</string>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <!-- Throttle crash-loop restarts so a broken deploy doesn't burn
       CPU.  launchd default is 10s which is fine but explicit is
       easier to reason about. -->
  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>StandardOutPath</key>
  <string>${LOG_DIR}/api.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/api.stderr.log</string>

  <!-- Seed a usable PATH so the script can find /bin/bash and
       Homebrew binaries even when launchd starts us outside a full
       GUI session (e.g. CI runner subprocess). -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST

echo "[install_native_api] wrote $PLIST_PATH"

# ── Load the fresh plist ─────────────────────────────────────────────
# ``load -w`` registers the service and clears any disabled flag so
# launchd will also auto-start it on next login.  This call works
# from a subagent's shell where ``bootstrap gui/<uid>`` would EIO.
echo "[install_native_api] loading ${LABEL}"
launchctl load -w "$PLIST_PATH"

# ── Sanity check that launchd actually registered + started us ───────
# ``launchctl list <label>`` is the legacy counterpart to ``print`` and,
# like ``load``, works in any user context.  Exit cleanly once we see
# a PID (== running), else surface the launchd status for debugging.
for i in 1 2 3 4 5 6 7 8 9 10; do
  if info="$(launchctl list "$LABEL" 2>/dev/null)"; then
    pid="$(printf '%s' "$info" | awk -F'=' '/"PID"/{gsub(/[^0-9]/,"",$2); print $2}')"
    if [ -n "${pid:-}" ] && [ "$pid" != "0" ]; then
      echo "[install_native_api] ${LABEL} running (pid=${pid})"
      echo "[install_native_api] tail logs with: tail -f ${LOG_DIR}/api.stderr.log"
      exit 0
    fi
  fi
  sleep 1
done

echo "[install_native_api] WARNING: ${LABEL} registered but no PID yet."
echo "[install_native_api] last launchd status:"
launchctl list "$LABEL" 2>&1 || true
echo "[install_native_api] tail of stderr log:"
tail -50 "${LOG_DIR}/api.stderr.log" 2>&1 || true
# Don't exit non-zero — the service might still come up once
# ThrottleInterval elapses.  CI health check will catch a real failure.
exit 0
