#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_ROOT="$HOME/Library/Application Support/infoext_monitor_service"
LABEL="com.infoext.monitor"
PLIST_SOURCE="$PROJECT_ROOT/launchd/$LABEL.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -x "$SERVICE_ROOT/.venv/bin/python" || ! -x "$SERVICE_ROOT/launchd/infoext-monitor-launcher" ]]; then
  echo "InfoExt runtime is not installed. Run bash scripts/install.sh first." >&2
  exit 1
fi
if [[ ! -f "$PROJECT_ROOT/config/runtime.local.toml" ]]; then
  echo "Missing config/runtime.local.toml. Create it before reloading LaunchAgent." >&2
  exit 1
fi
chmod 600 "$PROJECT_ROOT/config/runtime.local.toml"
mkdir -p "$PROJECT_ROOT/logs" "$HOME/Library/LaunchAgents"

PROJECT_ROOT="$PROJECT_ROOT" SERVICE_ROOT="$SERVICE_ROOT" PLIST_SOURCE="$PLIST_SOURCE" PLIST_TARGET="$PLIST_TARGET" \
  "$SERVICE_ROOT/.venv/bin/python" -c '
from pathlib import Path
import os
import sys
from xml.sax.saxutils import escape
source = Path(os.environ["PLIST_SOURCE"])
target = Path(os.environ["PLIST_TARGET"])
root = os.environ["PROJECT_ROOT"]
sys.path.insert(0, root)
from config import load_settings

settings = load_settings(Path(root))
calendar_intervals = "\n".join(
    "    <dict>\n"
    f"      <key>Weekday</key><integer>{weekday}</integer>\n"
    f"      <key>Hour</key><integer>{value.hour}</integer>\n"
    f"      <key>Minute</key><integer>{value.minute}</integer>\n"
    "    </dict>"
    for weekday in settings.launchd.weekdays
    for value in settings.launchd.calendar_times
)
payload = source.read_text(encoding="utf-8").replace("__PROJECT_ROOT__", escape(root))
payload = payload.replace("__SERVICE_ROOT__", escape(os.environ["SERVICE_ROOT"]))
target.write_text(payload.replace("__START_CALENDAR_INTERVALS__", calendar_intervals), encoding="utf-8")
'
plutil -lint "$PLIST_TARGET" >/dev/null

USER_ID="$(id -u)"
launchctl bootout "gui/$USER_ID" "$PLIST_TARGET" 2>/dev/null || true
launchctl bootstrap "gui/$USER_ID" "$PLIST_TARGET"
launchctl print "gui/$USER_ID/$LABEL" >/dev/null

echo "InfoExt LaunchAgent reloaded from local configuration. No check was triggered."
