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
SERVICE_ROOT="$HOME/Library/Application Support/telegram_connector_service"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
if [[ -n "$STAGING_ROOT" ]]; then
  SERVICE_ROOT="$STAGING_ROOT/telegram_connector_service"
  LAUNCH_AGENTS_DIR="$STAGING_ROOT/LaunchAgents"
fi
printf -v SERVICE_ROOT_SHELL '%q' "$SERVICE_ROOT"
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
BRIDGE_LAUNCHER="$SERVICE_ROOT/scripts/telegram-connector-bridge-launcher"
DIGEST_LAUNCHER="$SERVICE_ROOT/scripts/telegram-connector-digest-launcher"
LAUNCHCTL_DOMAIN="gui/$(id -u)"

source "$SOURCE_ROOT/../common/python_runtime.sh"
BOOTSTRAP_PYTHON="$(python_runtime_bootstrap)"
read -r DIGEST_HOUR DIGEST_MINUTE < <(
  "$BOOTSTRAP_PYTHON" - "$SOURCE_ROOT/config/runtime.local.toml" <<'PY'
import sys
import tomllib
from pathlib import Path

with Path(sys.argv[1]).open("rb") as fh:
    config = tomllib.load(fh)
raw_time = str(((config.get("digest") or {}).get("time") or "08:00")).strip()
hour_text, minute_text = raw_time.split(":", 1)
hour, minute = int(hour_text), int(minute_text)
if not (0 <= hour <= 23 and 0 <= minute <= 59):
    raise SystemExit("Invalid digest schedule")
print(hour, minute)
PY
)

mkdir -p "$SOURCE_ROOT/../scratch/python-runtime"
RUNTIME_STAGE="$(mktemp -d "$SOURCE_ROOT/../scratch/python-runtime/connector-install.XXXXXX")"
MANAGED_VENV=0
RUNTIME_PUBLISHED=0
STARTUP_CHANGED=0
startup_snapshot() {
  "$BOOTSTRAP_PYTHON" - "$1" "$RUNTIME_STAGE" "$SERVICE_ROOT" \
    "$BRIDGE_PLIST_PATH" "$DIGEST_PLIST_PATH" <<'PY'
import os
import shutil
import sys
from pathlib import Path

mode, stage, service, *files = sys.argv[1:]
stage, service = Path(stage), Path(service)
saved = stage / 'previous-startup'
external = stage / 'previous-startup-files'

def copy_entry(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)

def remove_entry(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)

if mode == 'save':
    saved.mkdir()
    external.mkdir()
    if service.exists():
        for path in service.iterdir():
            if path.name not in ('data', '.venv'):
                copy_entry(path, saved / path.name)
    for index, name in enumerate(files):
        path = Path(name)
        if path.exists() or path.is_symlink():
            copy_entry(path, external / str(index))
elif mode == 'restore':
    if service.exists():
        for path in service.iterdir():
            if path.name not in ('data', '.venv'):
                remove_entry(path)
    for path in saved.iterdir():
        copy_entry(path, service / path.name)
    for index, name in enumerate(files):
        path, previous = Path(name), external / str(index)
        remove_entry(path)
        if previous.exists() or previous.is_symlink():
            copy_entry(previous, path)
else:
    raise SystemExit('Invalid startup snapshot phase')
PY
}
finish_runtime_preparation() {
  local status=$?
  if [[ "$status" != 0 && "$STARTUP_CHANGED" == 1 && "$RUNTIME_PUBLISHED" == 0 ]]; then
    startup_snapshot restore
  fi
  if [[ "$status" != 0 && "$MANAGED_VENV" == 1 && "$RUNTIME_PUBLISHED" == 0 ]]; then
    rm -rf "$SERVICE_ROOT/.venv"
    if [[ -d "$RUNTIME_STAGE/previous-venv" ]]; then
      mv "$RUNTIME_STAGE/previous-venv" "$SERVICE_ROOT/.venv"
    fi
  fi
  rm -rf "$RUNTIME_STAGE"
  return "$status"
}
trap finish_runtime_preparation EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cp "$SOURCE_ROOT/requirements.lock" "$RUNTIME_STAGE/requirements.lock"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -L "$SERVICE_ROOT/.venv" || ( -e "$SERVICE_ROOT/.venv" && ! -d "$SERVICE_ROOT/.venv" ) ]]; then
    echo "Venv target is not an owned directory" >&2
    exit 1
  fi
  if [[ -d "$SERVICE_ROOT/.venv" ]]; then
    cp -R "$SERVICE_ROOT/.venv" "$RUNTIME_STAGE/previous-venv"
  fi
  MANAGED_VENV=1
fi

resolve_python_bin() {
  local tested="$SOURCE_ROOT/../.venv-test-gap-detection/.python-runtime.json"
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    local selected
    selected="$(python_runtime_select --config "$SOURCE_ROOT/config/runtime.local.toml" \
      --requirements "$RUNTIME_STAGE/requirements.lock" \
      --scratch "$SOURCE_ROOT/../scratch/python-runtime" \
      --path-only)" || return
    "$selected" -I "$SOURCE_ROOT/../common/python_environment.py" --check \
      --requirements "$RUNTIME_STAGE/requirements.lock" --require-hashes \
      --constraints "$SOURCE_ROOT/requirements.txt" \
      --find-links "${PIP_FIND_LINKS:-$SOURCE_ROOT/third_party}" \
      --scratch "$SOURCE_ROOT/../scratch/python-runtime" \
      --reference-record "$tested" --record "$RUNTIME_STAGE/python.json" || return
    printf '%s\n' "$selected"
    return
  fi
  local options=(--config "$SOURCE_ROOT/config/runtime.local.toml"
    --venv "$SERVICE_ROOT/.venv" --requirements "$RUNTIME_STAGE/requirements.lock"
    --require-hashes --reference-record "$tested"
    --constraints "$SOURCE_ROOT/requirements.txt"
    --find-links "${PIP_FIND_LINKS:-$SOURCE_ROOT/third_party}"
    --scratch "$SOURCE_ROOT/../scratch/python-runtime"
    --record "$RUNTIME_STAGE/python.json"
    --import telethon --import urllib3 --path-only)
  if [[ -n "${BASE_PYTHON_BIN:-}" ]]; then
    options+=(--python "$BASE_PYTHON_BIN")
  fi
  python_runtime_prepare "${options[@]}"
}

PYTHON_BIN="$(resolve_python_bin)"

if ! "$PYTHON_BIN" -c 'import telethon, urllib3' >/dev/null 2>&1; then
  echo "Missing Telethon or urllib3; install telegram_connector/requirements.txt with $PYTHON_BIN." >&2
  exit 1
fi

startup_snapshot save
STARTUP_CHANGED=1
mkdir -p "$SERVICE_ROOT/config" "$SERVICE_ROOT/data" "$SERVICE_ROOT/scripts" "$LAUNCH_AGENTS_DIR" "$PROJECT_LOG_DIR"

cp "$SOURCE_ROOT/telegram_bridge.py" "$SERVICE_ROOT/telegram_bridge.py"
cp "$SOURCE_ROOT/telegram_history_client.py" "$SERVICE_ROOT/telegram_history_client.py"
cp "$SOURCE_ROOT/history_settings.py" "$SERVICE_ROOT/history_settings.py"
cp "$SOURCE_ROOT/history_messages.py" "$SERVICE_ROOT/history_messages.py"
cp "$SOURCE_ROOT/history_store.py" "$SERVICE_ROOT/history_store.py"
cp "$SOURCE_ROOT/history_session.py" "$SERVICE_ROOT/history_session.py"
cp "$SOURCE_ROOT/analysis_history.py" "$SERVICE_ROOT/analysis_history.py"
cp "$SOURCE_ROOT/digest_settings.py" "$SERVICE_ROOT/digest_settings.py"
cp "$SOURCE_ROOT/digest_rendering.py" "$SERVICE_ROOT/digest_rendering.py"
cp "$SOURCE_ROOT/digest_pipeline.py" "$SERVICE_ROOT/digest_pipeline.py"
cp "$SOURCE_ROOT/digest_telemetry.py" "$SERVICE_ROOT/digest_telemetry.py"
cp "$SOURCE_ROOT/telegram_digest.py" "$SERVICE_ROOT/telegram_digest.py"
cp "$SOURCE_ROOT/telegram_analysis.py" "$SERVICE_ROOT/telegram_analysis.py"
cp "$SOURCE_ROOT/analysis_store.py" "$SERVICE_ROOT/analysis_store.py"
cp "$SOURCE_ROOT/analysis_evidence.py" "$SERVICE_ROOT/analysis_evidence.py"
cp "$SOURCE_ROOT/analysis_pipeline.py" "$SERVICE_ROOT/analysis_pipeline.py"
cp "$SOURCE_ROOT/analysis_rendering.py" "$SERVICE_ROOT/analysis_rendering.py"
cp "$SOURCE_ROOT/analysis_settings.py" "$SERVICE_ROOT/analysis_settings.py"
cp "$SOURCE_ROOT/analysis_telemetry.py" "$SERVICE_ROOT/analysis_telemetry.py"
cp "$SOURCE_ROOT/scripts/report_analysis.py" "$SERVICE_ROOT/scripts/report_analysis.py"
cp "$SOURCE_ROOT/scripts/report_digest.py" "$SERVICE_ROOT/scripts/report_digest.py"
rm -rf "$SERVICE_ROOT/common"
cp -R "$SOURCE_ROOT/../common" "$SERVICE_ROOT/common"
rm -rf "$SERVICE_ROOT/telegram_shared"
cp -R "$SOURCE_ROOT/../telegram_shared" "$SERVICE_ROOT/telegram_shared"
cp "$SOURCE_ROOT/config/runtime.local.toml" "$SERVICE_ROOT/config/runtime.local.toml"
cp "$SOURCE_ROOT/config/runtime.example.toml" "$SERVICE_ROOT/config/runtime.example.toml"
cp "$SOURCE_ROOT/config/digest_prompts.toml" "$SERVICE_ROOT/config/digest_prompts.toml"
cp "$SOURCE_ROOT/config/analysis_prompts.toml" "$SERVICE_ROOT/config/analysis_prompts.toml"

"$PYTHON_BIN" - "$SOURCE_ROOT" "$SERVICE_ROOT" <<'PY'
import shutil
import sys
import tomllib
from pathlib import Path

source_root, service_root = map(Path, sys.argv[1:])
with (source_root / "config/runtime.local.toml").open("rb") as fh:
    config = tomllib.load(fh)
for section, default in (("digest_prompts", "digest_prompts.toml"), ("analysis_prompts", "analysis_prompts.toml")):
    path = Path(str(config.get(section, {}).get("file", default))).expanduser()
    if path.is_absolute():
        if not path.is_file():
            raise SystemExit(f"Missing configured prompt bundle: {section}")
        continue  # An absolute operator path remains an external reference.
    source = (source_root / "config" / path).resolve()
    target = (service_root / "config" / path).resolve()
    if not target.is_relative_to(service_root.resolve()):
        raise SystemExit(f"Configured relative bundle escapes service root: {section}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target:
        shutil.copy2(source, target)
PY

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
#!/bin/bash
set -euo pipefail

ROOT=$SERVICE_ROOT_SHELL
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "\${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"
STARTUP_LOG="\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/bridge.startup.log"

cd "\$ROOT"
printf '[%s] starting telegram bridge from %s\n' "\$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "\$ROOT" >> "\$STARTUP_LOG"
"$PYTHON_BIN" -I "\$ROOT/common/python_environment.py" --check \
  --requirements "\$ROOT/requirements.lock" \
  --record "\$ROOT/.python-runtime.json"
exec "$PYTHON_BIN" "\$ROOT/telegram_bridge.py" listen --run-commands
EOF

chmod +x "$SERVICE_ROOT/scripts/run_telegram_bridge.sh"

cat > "$BRIDGE_LAUNCHER" <<EOF
#!/bin/sh
exec /bin/bash "$SERVICE_ROOT/scripts/run_telegram_bridge.sh"
EOF

chmod +x "$BRIDGE_LAUNCHER"

cat > "$SERVICE_ROOT/scripts/run_telegram_digest.sh" <<EOF
#!/bin/bash
set -euo pipefail

ROOT=$SERVICE_ROOT_SHELL
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
: "\${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"
STARTUP_LOG="\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/digest.startup.log"
AUDIT_LOG="\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/digest.last_attempt.json"

cd "\$ROOT"
printf '[%s] starting telegram digest from %s\n' "\$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "\$ROOT" >> "\$STARTUP_LOG"
exec "$PYTHON_BIN" - "\$ROOT" "\$AUDIT_LOG" "\$STARTUP_LOG" <<'PY'
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

service_root = Path(sys.argv[1])
if str(service_root) not in sys.path:
    sys.path.insert(0, str(service_root))

from digest_settings import build_digest_supervisor_command, resolve_digest_process_limits
from digest_telemetry import build_digest_run_id, prepare_supervised_audit, verify_supervised_runtime

with (service_root / "config" / "runtime.local.toml").open("rb") as fh:
    runtime = tomllib.load(fh)
timeout, grace = resolve_digest_process_limits(runtime)
with Path(sys.argv[3]).open("a") as log:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] arming digest ttl={timeout}s grace={grace}s", file=log)
audit_file = Path(sys.argv[2])
run_id = build_digest_run_id()
prepare_supervised_audit(audit_file, run_id)
verify_supervised_runtime(audit_file, requirements=service_root / 'requirements.lock',
    record_path=service_root / '.python-runtime.json',
    required=True)
command = build_digest_supervisor_command(
    [sys.executable, str(service_root / "telegram_digest.py"), "run"],
    runtime, audit_file, run_id=run_id)
os.execv(command[0], command)
PY
EOF

chmod +x "$SERVICE_ROOT/scripts/run_telegram_digest.sh"

cat > "$DIGEST_LAUNCHER" <<EOF
#!/bin/sh
exec /bin/bash "$SERVICE_ROOT/scripts/run_telegram_digest.sh"
EOF

chmod +x "$DIGEST_LAUNCHER"

cat > "$BRIDGE_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.zabaika.telegram-connector-bridge</string>

    <key>ProgramArguments</key>
    <array>
      <string>$BRIDGE_LAUNCHER</string>
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
      <string>$DIGEST_LAUNCHER</string>
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

"$BOOTSTRAP_PYTHON" - "$SOURCE_ROOT/.." "$RUNTIME_STAGE" "$SERVICE_ROOT" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from common.json_io import write_text_atomic

stage, service = map(Path, sys.argv[2:])
updates = [(service / 'requirements.lock', stage / 'requirements.lock'),
           (service / '.python-runtime.json', stage / 'python.json')]
for path, pending in updates:
    write_text_atomic(path, pending.read_bytes().decode('utf-8'))
PY
RUNTIME_PUBLISHED=1

if [[ -z "$STAGING_ROOT" ]]; then
  launchctl bootout "$LAUNCHCTL_DOMAIN" "$BRIDGE_PLIST_PATH" >/dev/null 2>&1 || true
  launchctl bootout "$LAUNCHCTL_DOMAIN" "$DIGEST_PLIST_PATH" >/dev/null 2>&1 || true
  launchctl bootstrap "$LAUNCHCTL_DOMAIN" "$BRIDGE_PLIST_PATH"
  launchctl bootstrap "$LAUNCHCTL_DOMAIN" "$DIGEST_PLIST_PATH"
  launchctl kickstart -k "$LAUNCHCTL_DOMAIN/com.zabaika.telegram-connector-bridge"
else
  echo "Staged runtime only; launchctl was not invoked."
fi

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
