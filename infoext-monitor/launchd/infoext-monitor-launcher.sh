#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RUNTIME_ROOT"
exec "$RUNTIME_ROOT/.venv/bin/python" "$RUNTIME_ROOT/launchd/run_monitor.py"
