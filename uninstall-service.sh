#!/usr/bin/env bash
#
# uninstall-service.sh — stop and remove the QLab -> Wyze bridge system service.
#
#     sudo ./uninstall-service.sh
#
set -euo pipefail

LABEL="com.qlab-wyze-bridge"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo:  sudo ./uninstall-service.sh" >&2
  exit 1
fi

launchctl bootout "system/$LABEL" 2>/dev/null \
  || launchctl unload -w "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

echo "✓ Service stopped and removed."
echo "  (The code, virtualenv, and .env are left in place; delete the folder to fully remove.)"
