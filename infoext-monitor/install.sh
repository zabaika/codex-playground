#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LABEL="com.infoext.monitor"
PLIST_SOURCE="$PROJECT_ROOT/launchd/$LABEL.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

"$PYTHON_BIN" -m venv "$PROJECT_ROOT/.venv"
if [[ ! -f "$PROJECT_ROOT/config/runtime.local.toml" ]]; then
  echo "Create config/runtime.local.toml from config/runtime.example.toml and fill in InfoExt data before installing LaunchAgent." >&2
  exit 1
fi
chmod 600 "$PROJECT_ROOT/config/runtime.local.toml"
"$PROJECT_ROOT/.venv/bin/python" -c '
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from config import load_settings
load_settings(root)
' "$PROJECT_ROOT"
"$PROJECT_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_ROOT/.venv/bin/python" -m pip install -r "$PROJECT_ROOT/requirements.txt"
"$PROJECT_ROOT/.venv/bin/python" -m playwright install chromium

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "Apple Vision OCR requires Xcode Command Line Tools. Install them with: xcode-select --install" >&2
  exit 1
fi
mkdir -p "$PROJECT_ROOT/bin"
xcrun --sdk macosx swiftc "$PROJECT_ROOT/vision_ocr.swift" -o "$PROJECT_ROOT/bin/infoext-vision-ocr"

mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs" "$PROJECT_ROOT/debug" "$HOME/Library/LaunchAgents"
chmod 700 "$PROJECT_ROOT/data" "$PROJECT_ROOT/debug"
chmod +x "$PROJECT_ROOT/launchd/infoext-monitor-launcher.sh"
chmod +x "$PROJECT_ROOT/launchd/infoext-monitor-launcher"

PROJECT_ROOT="$PROJECT_ROOT" PLIST_SOURCE="$PLIST_SOURCE" PLIST_TARGET="$PLIST_TARGET" \
  "$PROJECT_ROOT/.venv/bin/python" -c '
from pathlib import Path
import os
import sys
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
payload = source.read_text(encoding="utf-8").replace("__PROJECT_ROOT__", root)
target.write_text(payload.replace("__START_CALENDAR_INTERVALS__", calendar_intervals), encoding="utf-8")
'
plutil -lint "$PLIST_TARGET"

USER_ID="$(id -u)"
launchctl bootout "gui/$USER_ID" "$PLIST_TARGET" 2>/dev/null || true
launchctl bootstrap "gui/$USER_ID" "$PLIST_TARGET"
launchctl print "gui/$USER_ID/$LABEL" >/dev/null

"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/main.py" --help >/dev/null
echo "InfoExt monitor installed. The next check will start at the next configured calendar time."
