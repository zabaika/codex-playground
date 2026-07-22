#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"

rm -f "$PROJECT_LOG_DIR"/bridge.startup.log "$PROJECT_LOG_DIR"/bridge.stdout.log "$PROJECT_LOG_DIR"/bridge.stderr.log

echo "Cleared launch logs: $PROJECT_LOG_DIR"
