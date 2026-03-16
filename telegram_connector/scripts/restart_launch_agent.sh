#!/usr/bin/env bash
set -euo pipefail

PLIST_PATH="$HOME/Library/LaunchAgents/com.zabaika.telegram-connector-bridge.plist"

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Restarted launch agent: com.zabaika.telegram-connector-bridge"
