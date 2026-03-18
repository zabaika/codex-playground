#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/usr/local/bin/python3}"
CRON_TAG="telegram_connector_daily_digest"
LOG_DIR="$SOURCE_ROOT/data/launchd"

mkdir -p "$LOG_DIR"

CRON_LINE="$(
  TELEGRAM_CONNECTOR_PROJECT_ROOT="$SOURCE_ROOT" \
  "$PYTHON_BIN" "$SOURCE_ROOT/telegram_digest.py" cron-line
)"

EXISTING_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(printf '%s\n' "$EXISTING_CRONTAB" | grep -v "$CRON_TAG" || true)"

{
  if [[ -n "$FILTERED_CRONTAB" ]]; then
    printf '%s\n' "$FILTERED_CRONTAB"
  fi
  printf '%s\n' "$CRON_LINE"
} | crontab -

echo "Installed digest cron entry:"
echo "$CRON_LINE"
