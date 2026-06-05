#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/lib.sh"

timeout_seconds="${JSS_DAILY_ROUTINE_TIMEOUT_SECONDS:-300}"
audit_file="${JSS_DAILY_ROUTINE_AUDIT_FILE:-${JSS_ROOT}/data/runtime/daily-routine-audit.json}"
candidate_args=()
caffeinate_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-id)
      candidate_args=(--candidate-id "${2:-}")
      shift 2
      ;;
    --timeout-seconds)
      timeout_seconds="${2:-}"
      shift 2
      ;;
    --audit-file)
      audit_file="${2:-}"
      shift 2
      ;;
    --use-caffeinate)
      caffeinate_args=(--use-caffeinate)
      shift
      ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      exit 2
      ;;
  esac
done

require_workspace
require_file "${CONFIG_PATH}" "runtime config"
mkdir -p "$(dirname "${audit_file}")"

python3 "${PLAYGROUND_ROOT}/common/ttl_runner.py" \
  --timeout-seconds "${timeout_seconds}" \
  --audit-file "${audit_file}" \
  --timeout-reason "job_search_daily_routine_ttl_expired" \
  "${caffeinate_args[@]}" \
  -- "${script_dir}/daily-routine.sh" "${candidate_args[@]}"
