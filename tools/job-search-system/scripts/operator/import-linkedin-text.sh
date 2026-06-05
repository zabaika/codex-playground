#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/lib.sh"

content_path=""
candidate_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --content-path)
      content_path="${2:-}"
      shift 2
      ;;
    --candidate-id)
      candidate_args=(--candidate-id "${2:-}")
      shift 2
      ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${content_path}" ]]; then
  echo "Нужно указать --content-path /path/to/linkedin-vacancies.txt" >&2
  exit 2
fi

require_file "${content_path}" "LinkedIn text/export input"
run_vacancy_cli import-linkedin-text "${candidate_args[@]}" --content-path "${content_path}"
