#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_ROOT="$HOME/Library/Application Support/telegram_connector_service"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.zabaika.telegram-connector-bridge.plist"
PYTHON_BIN="/usr/local/bin/python3"
PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"
STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"
STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"
STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"

mkdir -p "$SERVICE_ROOT/config" "$SERVICE_ROOT/data/launchd" "$SERVICE_ROOT/scripts" "$LAUNCH_AGENTS_DIR" "$PROJECT_LOG_DIR"

rm -f "$PROJECT_LOG_DIR"/bridge.startup.log "$PROJECT_LOG_DIR"/bridge.stdout.log "$PROJECT_LOG_DIR"/bridge.stderr.log
rm -f "$SERVICE_ROOT"/data/launchd/bridge.stdout.log "$SERVICE_ROOT"/data/launchd/bridge.stderr.log

cp "$SOURCE_ROOT/telegram_connector.py" "$SERVICE_ROOT/telegram_connector.py"
cp "$SOURCE_ROOT/telegram_history_client.py" "$SERVICE_ROOT/telegram_history_client.py"
cp "$SOURCE_ROOT/telegram_digest.py" "$SERVICE_ROOT/telegram_digest.py"
cp "$SOURCE_ROOT/config/runtime.local.toml" "$SERVICE_ROOT/config/runtime.local.toml"
cp "$SOURCE_ROOT/config/runtime.example.toml" "$SERVICE_ROOT/config/runtime.example.toml"

if [[ -d "$SOURCE_ROOT/data/sessions" && ! -d "$SERVICE_ROOT/data/sessions" ]]; then
  cp -R "$SOURCE_ROOT/data/sessions" "$SERVICE_ROOT/data/sessions"
fi

if [[ -f "$SOURCE_ROOT/data/telegram_history.sqlite3" && ! -f "$SERVICE_ROOT/data/telegram_history.sqlite3" ]]; then
  cp "$SOURCE_ROOT/data/telegram_history.sqlite3" "$SERVICE_ROOT/data/telegram_history.sqlite3"
fi

if [[ -d "$SOURCE_ROOT/data/media" && ! -d "$SERVICE_ROOT/data/media" ]]; then
  cp -R "$SOURCE_ROOT/data/media" "$SERVICE_ROOT/data/media"
fi

cat > "$SERVICE_ROOT/scripts/run_telegram_bridge.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/Library/Application Support/telegram_connector_service"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"
STARTUP_LOG="$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/bridge.startup.log"

cd "$ROOT"
printf '[%s] starting telegram bridge from %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ROOT" >> "$STARTUP_LOG"
exec /usr/local/bin/python3 "$ROOT/telegram_connector.py" listen --run-commands
EOF

chmod +x "$SERVICE_ROOT/scripts/run_telegram_bridge.sh"

cat > "$PLIST_PATH" <<EOF
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

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Installed launch agent: $PLIST_PATH"
echo "Service root: $SERVICE_ROOT"
echo "Startup log: $STARTUP_LOG"
echo "Stdout log: $STDOUT_LOG"
echo "Stderr log: $STDERR_LOG"
