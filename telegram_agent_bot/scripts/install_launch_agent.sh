#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_ROOT="$HOME/Library/Application Support/telegram_agent_bot_service"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.zabaika.telegram-agent-bot-bridge.plist"
PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"
STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"
STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"
STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"

mkdir -p "$SERVICE_ROOT/config" "$SERVICE_ROOT/data/launchd" "$SERVICE_ROOT/scripts" "$LAUNCH_AGENTS_DIR" "$PROJECT_LOG_DIR"

rm -f "$PROJECT_LOG_DIR"/bridge.startup.log "$PROJECT_LOG_DIR"/bridge.stdout.log "$PROJECT_LOG_DIR"/bridge.stderr.log
rm -f "$SERVICE_ROOT"/data/launchd/bridge.stdout.log "$SERVICE_ROOT"/data/launchd/bridge.stderr.log

cp "$SOURCE_ROOT/telegram_agent_bridge.py" "$SERVICE_ROOT/telegram_agent_bridge.py"
cp "$SOURCE_ROOT/telegram_agent_worker.py" "$SERVICE_ROOT/telegram_agent_worker.py"
rm -rf "$SERVICE_ROOT/telegram_shared"
cp -R "$SOURCE_ROOT/../telegram_shared" "$SERVICE_ROOT/telegram_shared"
cp "$SOURCE_ROOT/config/runtime.local.toml" "$SERVICE_ROOT/config/runtime.local.toml"
cp "$SOURCE_ROOT/config/runtime.example.toml" "$SERVICE_ROOT/config/runtime.example.toml"

cat > "$SERVICE_ROOT/scripts/run_telegram_agent_bridge.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/Library/Application Support/telegram_agent_bot_service"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "${TELEGRAM_AGENT_BOT_PROJECT_ROOT:?TELEGRAM_AGENT_BOT_PROJECT_ROOT is required}"
STARTUP_LOG="$TELEGRAM_AGENT_BOT_PROJECT_ROOT/data/launchd/bridge.startup.log"

cd "$ROOT"
printf '[%s] starting telegram agent bridge from %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ROOT" >> "$STARTUP_LOG"
exec /usr/local/bin/python3 "$ROOT/telegram_agent_bridge.py" listen --run-commands
EOF

chmod +x "$SERVICE_ROOT/scripts/run_telegram_agent_bridge.sh"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.zabaika.telegram-agent-bot-bridge</string>

    <key>ProgramArguments</key>
    <array>
      <string>$SERVICE_ROOT/scripts/run_telegram_agent_bridge.sh</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
      <key>TELEGRAM_AGENT_BOT_PROJECT_ROOT</key>
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
