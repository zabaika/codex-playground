#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_ROOT="$HOME/Library/Application Support/telegram_connector_service"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BRIDGE_PLIST_PATH="$LAUNCH_AGENTS_DIR/com.zabaika.telegram-connector-bridge.plist"
DIGEST_PLIST_PATH="$LAUNCH_AGENTS_DIR/com.zabaika.telegram-connector-digest.plist"
PYTHON_BIN="${PYTHON_BIN:-}"
PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"
STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"
STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"
STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"
DIGEST_STARTUP_LOG="$PROJECT_LOG_DIR/digest.startup.log"
DIGEST_STDOUT_LOG="$PROJECT_LOG_DIR/digest.stdout.log"
DIGEST_STDERR_LOG="$PROJECT_LOG_DIR/digest.stderr.log"
DIGEST_LAST_ATTEMPT_LOG="$PROJECT_LOG_DIR/digest.last_attempt.json"

resolve_python_bin() {
  local explicit="${PYTHON_BIN:-}"
  local candidates=()
  if [[ -n "$explicit" ]]; then
    candidates+=("$explicit")
  fi
  candidates+=(
    "/usr/local/opt/python@3.13/bin/python3.13"
    "/usr/local/bin/python3"
    "/opt/homebrew/bin/python3"
    "/usr/bin/python3"
  )

  local py
  for py in "${candidates[@]}"; do
    [[ -x "$py" ]] || continue
    if "$py" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec("telethon") is not None else 1)
PY
    then
      printf '%s\n' "$py"
      return 0
    fi
  done

  for py in "${candidates[@]}"; do
    [[ -x "$py" ]] || continue
    printf '%s\n' "$py"
    return 0
  done

  echo "No usable python interpreter found." >&2
  exit 1
}

PYTHON_BIN="$(resolve_python_bin)"

read -r DIGEST_HOUR DIGEST_MINUTE < <(
  "$PYTHON_BIN" - <<PY
import tomllib
from pathlib import Path

config_path = Path(r"$SOURCE_ROOT/config/runtime.local.toml")
with config_path.open("rb") as fh:
    config = tomllib.load(fh)

raw_time = str(((config.get("digest") or {}).get("time") or "08:00")).strip()
hour_text, minute_text = raw_time.split(":", 1)
print(int(hour_text), int(minute_text))
PY
)

mkdir -p "$SERVICE_ROOT/config" "$SERVICE_ROOT/data" "$SERVICE_ROOT/scripts" "$LAUNCH_AGENTS_DIR" "$PROJECT_LOG_DIR"

rm -f "$PROJECT_LOG_DIR"/bridge.startup.log "$PROJECT_LOG_DIR"/bridge.stdout.log "$PROJECT_LOG_DIR"/bridge.stderr.log
rm -f "$PROJECT_LOG_DIR"/digest.startup.log "$PROJECT_LOG_DIR"/digest.stdout.log "$PROJECT_LOG_DIR"/digest.stderr.log
rm -f "$PROJECT_LOG_DIR"/digest.last_attempt.json

cp "$SOURCE_ROOT/telegram_connector.py" "$SERVICE_ROOT/telegram_connector.py"
cp "$SOURCE_ROOT/telegram_history_client.py" "$SERVICE_ROOT/telegram_history_client.py"
cp "$SOURCE_ROOT/telegram_digest.py" "$SERVICE_ROOT/telegram_digest.py"
rm -rf "$SERVICE_ROOT/telegram_shared"
cp -R "$SOURCE_ROOT/../telegram_shared" "$SERVICE_ROOT/telegram_shared"
cp "$SOURCE_ROOT/config/runtime.local.toml" "$SERVICE_ROOT/config/runtime.local.toml"
cp "$SOURCE_ROOT/config/runtime.example.toml" "$SERVICE_ROOT/config/runtime.example.toml"
cp "$SOURCE_ROOT/config/digest_prompts.toml" "$SERVICE_ROOT/config/digest_prompts.toml"

if [[ -d "$SOURCE_ROOT/data/sessions" && ! -d "$SERVICE_ROOT/data/sessions" ]]; then
  cp -R "$SOURCE_ROOT/data/sessions" "$SERVICE_ROOT/data/sessions"
fi

if [[ -f "$SOURCE_ROOT/data/telegram_history.sqlite3" && ! -f "$SERVICE_ROOT/data/telegram_history.sqlite3" ]]; then
  cp "$SOURCE_ROOT/data/telegram_history.sqlite3" "$SERVICE_ROOT/data/telegram_history.sqlite3"
fi

if [[ -d "$SOURCE_ROOT/data/media" && ! -d "$SERVICE_ROOT/data/media" ]]; then
  cp -R "$SOURCE_ROOT/data/media" "$SERVICE_ROOT/data/media"
fi

cat > "$SERVICE_ROOT/scripts/run_telegram_bridge.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

ROOT="\$HOME/Library/Application Support/telegram_connector_service"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "\${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"
STARTUP_LOG="\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/bridge.startup.log"

cd "\$ROOT"
printf '[%s] starting telegram bridge from %s\n' "\$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "\$ROOT" >> "\$STARTUP_LOG"
exec "$PYTHON_BIN" "\$ROOT/telegram_connector.py" listen --run-commands
EOF

chmod +x "$SERVICE_ROOT/scripts/run_telegram_bridge.sh"

cat > "$SERVICE_ROOT/scripts/run_telegram_digest.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

ROOT="\$HOME/Library/Application Support/telegram_connector_service"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "\${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"
STARTUP_LOG="\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/digest.startup.log"

cd "\$ROOT"
printf '[%s] starting telegram digest from %s\n' "\$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "\$ROOT" >> "\$STARTUP_LOG"
exec /usr/bin/caffeinate -i "$PYTHON_BIN" "\$ROOT/telegram_digest.py" run
EOF

chmod +x "$SERVICE_ROOT/scripts/run_telegram_digest.sh"

cat > "$BRIDGE_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.zabaika.telegram-connector-bridge</string>

    <key>ProgramArguments</key>
    <array>
      <string>$SERVICE_ROOT/scripts/run_telegram_bridge.sh</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
      <key>TELEGRAM_CONNECTOR_PROJECT_ROOT</key>
      <string>$SOURCE_ROOT</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>WorkingDirectory</key>
    <string>$SERVICE_ROOT</string>

    <key>StandardOutPath</key>
    <string>$STDOUT_LOG</string>

    <key>StandardErrorPath</key>
    <string>$STDERR_LOG</string>

    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
EOF

cat > "$DIGEST_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.zabaika.telegram-connector-digest</string>

    <key>ProgramArguments</key>
    <array>
      <string>$SERVICE_ROOT/scripts/run_telegram_digest.sh</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
      <key>TELEGRAM_CONNECTOR_PROJECT_ROOT</key>
      <string>$SOURCE_ROOT</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
      <key>Hour</key>
      <integer>$DIGEST_HOUR</integer>
      <key>Minute</key>
      <integer>$DIGEST_MINUTE</integer>
    </dict>

    <key>WorkingDirectory</key>
    <string>$SERVICE_ROOT</string>

    <key>StandardOutPath</key>
    <string>$DIGEST_STDOUT_LOG</string>

    <key>StandardErrorPath</key>
    <string>$DIGEST_STDERR_LOG</string>

    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
EOF

launchctl unload "$BRIDGE_PLIST_PATH" >/dev/null 2>&1 || true
launchctl unload "$DIGEST_PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$BRIDGE_PLIST_PATH"
launchctl load "$DIGEST_PLIST_PATH"

echo "Installed launch agent: $BRIDGE_PLIST_PATH"
echo "Installed launch agent: $DIGEST_PLIST_PATH"
echo "Service root: $SERVICE_ROOT"
echo "Startup log: $STARTUP_LOG"
echo "Stdout log: $STDOUT_LOG"
echo "Stderr log: $STDERR_LOG"
echo "Digest startup log: $DIGEST_STARTUP_LOG"
echo "Digest stdout log: $DIGEST_STDOUT_LOG"
echo "Digest stderr log: $DIGEST_STDERR_LOG"
echo "Digest last-attempt audit log: $DIGEST_LAST_ATTEMPT_LOG"
echo "Digest schedule: $(printf '%02d:%02d' "$DIGEST_HOUR" "$DIGEST_MINUTE")"
echo "Python interpreter: $PYTHON_BIN"
