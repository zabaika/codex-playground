#!/usr/bin/env bash
set -euo pipefail

STAGING_ROOT=""
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--staging-root" || "$2" != /* || "$2" == "/" ]]; then
    echo "Usage: $0 [--staging-root ABSOLUTE_DIRECTORY]" >&2
    exit 2
  fi
  STAGING_ROOT="$2"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_ROOT="$HOME/Library/Application Support/telegram_agent_bot_service"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
if [[ -n "$STAGING_ROOT" ]]; then
  SERVICE_ROOT="$STAGING_ROOT/telegram_agent_bot_service"
  LAUNCH_AGENTS_DIR="$STAGING_ROOT/LaunchAgents"
fi
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.zabaika.telegram-agent-bot-bridge.plist"
PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"
STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"
STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"
STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"
BRIDGE_LAUNCHER="$SERVICE_ROOT/scripts/telegram-agent-bot-bridge-launcher"
LAUNCHCTL_DOMAIN="gui/$(id -u)"
source "$SOURCE_ROOT/../common/python_runtime.sh"
PYTHON_BIN="$(python_runtime_select \
  --config "$SOURCE_ROOT/config/runtime.local.toml" \
  --record "$SOURCE_ROOT/data/runtime/python.json" --path-only)"
(
  cd "$SOURCE_ROOT"
  "$PYTHON_BIN" -c 'import telegram_agent_bridge, telegram_agent_worker'
)

mkdir -p "$SERVICE_ROOT/config" "$SERVICE_ROOT/data/launchd" "$SERVICE_ROOT/scripts" "$LAUNCH_AGENTS_DIR" "$PROJECT_LOG_DIR"

cp "$SOURCE_ROOT/telegram_agent_bridge.py" "$SERVICE_ROOT/telegram_agent_bridge.py"
cp "$SOURCE_ROOT/telegram_agent_worker.py" "$SERVICE_ROOT/telegram_agent_worker.py"
rm -rf "$SERVICE_ROOT/telegram_shared"
cp -R "$SOURCE_ROOT/../telegram_shared" "$SERVICE_ROOT/telegram_shared"
rm -rf "$SERVICE_ROOT/common"
cp -R "$SOURCE_ROOT/../common" "$SERVICE_ROOT/common"
cp "$SOURCE_ROOT/config/runtime.local.toml" "$SERVICE_ROOT/config/runtime.local.toml"
cp "$SOURCE_ROOT/config/runtime.example.toml" "$SERVICE_ROOT/config/runtime.example.toml"

{
printf '#!/bin/bash\nPYTHON_BIN=%q\n' "$PYTHON_BIN"
cat <<'EOF'
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "${TELEGRAM_AGENT_BOT_PROJECT_ROOT:?TELEGRAM_AGENT_BOT_PROJECT_ROOT is required}"
STARTUP_LOG="$TELEGRAM_AGENT_BOT_PROJECT_ROOT/data/launchd/bridge.startup.log"

cd "$ROOT"
printf '[%s] starting telegram agent bridge from %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ROOT" >> "$STARTUP_LOG"
exec "$PYTHON_BIN" "$ROOT/telegram_agent_bridge.py" listen --run-commands
EOF
} > "$SERVICE_ROOT/scripts/run_telegram_agent_bridge.sh"

chmod +x "$SERVICE_ROOT/scripts/run_telegram_agent_bridge.sh"

cat > "$BRIDGE_LAUNCHER" <<EOF
#!/bin/sh
exec /bin/bash "$SERVICE_ROOT/scripts/run_telegram_agent_bridge.sh"
EOF

chmod +x "$BRIDGE_LAUNCHER"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.zabaika.telegram-agent-bot-bridge</string>

    <key>ProgramArguments</key>
    <array>
      <string>$BRIDGE_LAUNCHER</string>
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

(
  cd "$SERVICE_ROOT"
  "$PYTHON_BIN" -c 'import telegram_agent_bridge, telegram_agent_worker'
)

if [[ -z "$STAGING_ROOT" ]]; then
  launchctl bootout "$LAUNCHCTL_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
  launchctl bootstrap "$LAUNCHCTL_DOMAIN" "$PLIST_PATH"
  launchctl kickstart -k "$LAUNCHCTL_DOMAIN/com.zabaika.telegram-agent-bot-bridge"
else
  echo "Staged runtime only; launchctl was not invoked."
fi

echo "Installed launch agent: $PLIST_PATH"
echo "Service root: $SERVICE_ROOT"
echo "Startup log: $STARTUP_LOG"
echo "Stdout log: $STDOUT_LOG"
echo "Stderr log: $STDERR_LOG"
