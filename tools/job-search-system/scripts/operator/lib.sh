#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export JSS_ROOT="$(cd "${script_dir}/../.." && pwd)"
export PLAYGROUND_ROOT="$(cd "${JSS_ROOT}/../.." && pwd)"
export CONFIG_PATH="${CONFIG_PATH:-${JSS_ROOT}/config/runtime.local.toml}"
export WORKSPACE_PATH="${WORKSPACE_PATH:-}"
export PYTHONPATH="${JSS_ROOT}/src:${PLAYGROUND_ROOT}:${PYTHONPATH:-}"

require_workspace() {
  if [[ -z "${WORKSPACE_PATH}" ]]; then
    echo "Нужно указать WORKSPACE_PATH=/path/to/workspace.local.toml" >&2
    exit 2
  fi
}

require_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "${path}" ]]; then
    echo "Не найден ${label}: ${path}" >&2
    exit 2
  fi
}

run_system_cli() {
  require_workspace
  require_file "${CONFIG_PATH}" "runtime config"
  python3 -m job_search.interfaces.cli.system_cli \
    --config-path "${CONFIG_PATH}" \
    --workspace-path "${WORKSPACE_PATH}" \
    "$@"
}

run_candidate_cli() {
  require_workspace
  require_file "${CONFIG_PATH}" "runtime config"
  python3 -m job_search.interfaces.cli.candidate_cli \
    --config-path "${CONFIG_PATH}" \
    --workspace-path "${WORKSPACE_PATH}" \
    "$@"
}

run_vacancy_cli() {
  require_workspace
  require_file "${CONFIG_PATH}" "runtime config"
  python3 -m job_search.interfaces.cli.vacancy_cli \
    --config-path "${CONFIG_PATH}" \
    --workspace-path "${WORKSPACE_PATH}" \
    "$@"
}
