#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/lib.sh"

host="${JSS_API_HOST:-127.0.0.1}"
port="${JSS_API_PORT:-8765}"

require_workspace
require_file "${CONFIG_PATH}" "runtime config"

python3 -m job_search.interfaces.api.server \
  --config-path "${CONFIG_PATH}" \
  --workspace-path "${WORKSPACE_PATH}" \
  --host "${host}" \
  --port "${port}"
