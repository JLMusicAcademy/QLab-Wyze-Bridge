#!/usr/bin/env bash
#
# install-service.sh — install the QLab -> Wyze bridge as a macOS LaunchDaemon
# (a system service) so it starts at boot, restarts itself if it crashes, and
# runs independently of which user is logged in. Run this as an admin:
#
#     sudo ./install-service.sh
#
# The service runs as the admin who invoked sudo (so it can read the .env
# credentials, which stay locked to that account). Unprivileged users — your
# sound/light techs — never need to touch it; QLab just sends OSC to
# 127.0.0.1:9000 and the always-running service handles it.
#
# Override the service account with:  sudo SERVICE_USER=someuser ./install-service.sh
#
set -euo pipefail

LABEL="com.qlab-wyze-bridge"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
LOGFILE="/Library/Logs/qlab-wyze-bridge.log"

if [ "$(id -u)" -ne 0 ]; then
  echo "This installs a system service and must be run with sudo:" >&2
  echo "    sudo ./install-service.sh" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$REPO/.venv/bin/python"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-root}}"

if [ ! -x "$VENV_PY" ]; then
  echo "Error: virtualenv not found at $VENV_PY" >&2
  echo "Run ./install.sh first (as the admin user, without sudo)." >&2
  exit 1
fi
if [ ! -f "$REPO/.env" ]; then
  echo "Error: $REPO/.env not found. Run ./install.sh first and add your credentials." >&2
  exit 1
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Error: service user '$SERVICE_USER' does not exist." >&2
  exit 1
fi

echo "==> Installing system service '$LABEL'"
echo "    Repo:         $REPO"
echo "    Python:       $VENV_PY"
echo "    Runs as user: $SERVICE_USER"
echo "    Log file:     $LOGFILE"

# Lock the credentials to the service account so other users can't read them.
chown "$SERVICE_USER" "$REPO/.env"
chmod 600 "$REPO/.env"

# Pre-create a world-readable log (techs can tail it; only the service writes).
touch "$LOGFILE"
chown "$SERVICE_USER" "$LOGFILE"
chmod 644 "$LOGFILE"

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>UserName</key>
    <string>$SERVICE_USER</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PY</string>
        <string>-m</string>
        <string>qlab_wyze_bridge</string>
        <string>-c</string>
        <string>$REPO/config.yaml</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$LOGFILE</string>
    <key>StandardErrorPath</key>
    <string>$LOGFILE</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF
chown root:wheel "$PLIST"
chmod 644 "$PLIST"

# (Re)load the daemon. bootout first in case it's already loaded.
launchctl bootout "system/$LABEL" 2>/dev/null || true
launchctl enable "system/$LABEL" 2>/dev/null || true
launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
launchctl kickstart -k "system/$LABEL" 2>/dev/null || true

echo
echo "✓ Service installed and started. It will start automatically at every boot."
echo
echo "Check it:   sudo launchctl print system/$LABEL | grep -E 'state|pid'"
echo "Logs:       tail -f $LOGFILE"
echo "Restart:    sudo launchctl kickstart -k system/$LABEL"
echo "Remove:     sudo ./uninstall-service.sh"
