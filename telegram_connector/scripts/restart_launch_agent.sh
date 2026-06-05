#!/usr/bin/env bash
set -euo pipefail

PLIST_PATH="$HOME/Library/LaunchAgents/com.zabaika.telegram-connector-bridge.plist"
LAUNCHCTL_DOMAIN="gui/$(id -u)"

launchctl bootout "$LAUNCHCTL_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "$LAUNCHCTL_DOMAIN" "$PLIST_PATH"
launchctl kickstart -k "$LAUNCHCTL_DOMAIN/com.zabaika.telegram-connector-bridge"

echo "Restarted launch agent: com.zabaika.telegram-connector-bridge"
