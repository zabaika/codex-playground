#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/lib.sh"

candidate_args=()
if [[ "${1:-}" == "--candidate-id" ]]; then
  candidate_args=(--candidate-id "${2:-}")
fi

echo "== Daily actions =="
run_vacancy_cli daily-actions "${candidate_args[@]}"

echo
echo "== Pipeline report =="
run_vacancy_cli pipeline-report "${candidate_args[@]}"
