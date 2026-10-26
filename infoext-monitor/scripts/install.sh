#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_ROOT="$HOME/Library/Application Support/infoext_monitor_service"
PREPARE_ONLY=false
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 1 || "$1" != "--prepare-only" ]]; then
    echo "Usage: $0 [--prepare-only]" >&2
    exit 2
  fi
  PREPARE_ONLY=true
fi
source "$PROJECT_ROOT/../common/python_runtime.sh"
bootstrap="$(python_runtime_bootstrap)"
if [[ ! -f "$PROJECT_ROOT/config/runtime.local.toml" ]]; then
  echo "Create config/runtime.local.toml from config/runtime.example.toml and fill in InfoExt data before installing LaunchAgent." >&2
  exit 1
fi
chmod 600 "$PROJECT_ROOT/config/runtime.local.toml"
"$bootstrap" -c '
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from config import load_settings
load_settings(root)
' "$PROJECT_ROOT"
python_runtime_prepare --config "$PROJECT_ROOT/config/runtime.local.toml" \
  --venv "$PROJECT_ROOT/.venv" --venv "$SERVICE_ROOT/.venv" \
  --requirements "$PROJECT_ROOT/requirements.txt" \
  --scratch "$PROJECT_ROOT/../scratch/python-runtime" \
  --import playwright --import PIL --import greenlet >/dev/null
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

# Keep executable code outside Documents, matching telegram_connector deployment.
mkdir -p "$SERVICE_ROOT/launchd" "$SERVICE_ROOT/bin" "$SERVICE_ROOT/common"
cp "$PROJECT_ROOT/"{main,config,infoext,captcha_solver,state,notifier}.py "$SERVICE_ROOT/"
cp "$PROJECT_ROOT/requirements.txt" "$SERVICE_ROOT/requirements.txt"
cp "$PROJECT_ROOT/launchd/"{run_monitor.py,infoext-monitor-launcher,infoext-monitor-launcher.sh} "$SERVICE_ROOT/launchd/"
cp "$PROJECT_ROOT/bin/infoext-vision-ocr" "$SERVICE_ROOT/bin/infoext-vision-ocr"
rsync -a --delete --exclude '__pycache__' --exclude 'tests' "$PROJECT_ROOT/../common/" "$SERVICE_ROOT/common/"
# Prepare installed environment: both venvs passed the shared preparation above.
"$SERVICE_ROOT/.venv/bin/python" -m playwright install chromium
chmod +x "$SERVICE_ROOT/launchd/"{infoext-monitor-launcher,infoext-monitor-launcher.sh}

if [[ "$PREPARE_ONLY" == false ]]; then
  bash "$PROJECT_ROOT/scripts/reload_launch_agent.sh"
fi

"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/main.py" --help >/dev/null
if [[ "$PREPARE_ONLY" == true ]]; then
  echo "InfoExt runtime prepared. LaunchAgent registration was not changed."
else
  echo "InfoExt monitor installed. The next check will start at the next configured calendar time."
fi
