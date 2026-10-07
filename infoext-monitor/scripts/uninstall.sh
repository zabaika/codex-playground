#!/usr/bin/env bash
set -euo pipefail

LABEL="com.infoext.monitor"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
USER_ID="$(id -u)"

if [[ -f "$PLIST_TARGET" ]]; then
  launchctl bootout "gui/$USER_ID" "$PLIST_TARGET" 2>/dev/null || true
  rm "$PLIST_TARGET"
fi

echo "InfoExt LaunchAgent removed. data/, logs/, debug/, config/runtime.local.toml and telegram_connector were kept."
