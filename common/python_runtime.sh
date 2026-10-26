#!/usr/bin/env bash
# Source this file; application interpreter selection belongs to the Python owner.
PYTHON_RUNTIME_COMMON_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHON_RUNTIME_COMMON_ROOT

python_runtime_bootstrap() {
  local candidate
  # Bootstrap only reads configuration. PYTHON_BIN is validated by the resolver
  # under its outer TTL, rather than being executed here without supervision.
  for candidate in /opt/homebrew/bin/python3 python3; do
    if "$candidate" -I -c 'import tomllib' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  echo "A local Python with stdlib tomllib is required to read runtime policy." >&2
  return 1
}

python_runtime_select() {
  local bootstrap
  bootstrap="$(python_runtime_bootstrap)" || return 1
  "$bootstrap" -I "$PYTHON_RUNTIME_COMMON_ROOT/python_runtime.py" "$@"
}

python_runtime_prepare() {
  local bootstrap
  bootstrap="$(python_runtime_bootstrap)" || return 1
  "$bootstrap" -I "$PYTHON_RUNTIME_COMMON_ROOT/ttl_runner.py" -- \
    "$bootstrap" -I "$PYTHON_RUNTIME_COMMON_ROOT/python_environment.py" "$@"
}
