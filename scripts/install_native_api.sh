#!/usr/bin/env bash
# One-shot installer for the native-mac Autonoma API launchd agent.
#
# Writes ~/Library/LaunchAgents/com.autonoma.api.plist with the repo's
# absolute path resolved in, then bootstraps it into the user's GUI
# domain.  Re-running is safe — it unloads the old copy first.
#
# The deploy.yml workflow expects this to have been run *once* on the
# target Mac before the first native deploy.  Subsequent deploys only
# ``launchctl kickstart`` the already-loaded service.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LABEL="com.autonoma.api"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

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

  <!-- Seed a minimal PATH so the script can find /bin/bash even before
       it layers on Homebrew paths itself. -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST

echo "[install_native_api] wrote $PLIST_PATH"

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
TARGET="${DOMAIN}/${LABEL}"

# ``bootout`` fails when the service isn't loaded; tolerate that so the
# script is re-runnable on a clean machine.
if launchctl print "$TARGET" >/dev/null 2>&1; then
  echo "[install_native_api] unloading existing $TARGET"
  launchctl bootout "$TARGET" || true
fi

echo "[install_native_api] bootstrapping $TARGET"
launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
launchctl enable "$TARGET"
launchctl kickstart -k "$TARGET"

echo "[install_native_api] done.  Tail logs with:"
echo "  tail -f $LOG_DIR/api.stderr.log"
