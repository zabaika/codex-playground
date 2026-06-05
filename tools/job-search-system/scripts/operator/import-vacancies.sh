#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/lib.sh"

source_kind="${SOURCE_KIND:-manual}"
items_path=""
candidate_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --items-path)
      items_path="${2:-}"
      shift 2
      ;;
    --candidate-id)
      candidate_args=(--candidate-id "${2:-}")
      shift 2
      ;;
    --source-kind)
      source_kind="${2:-}"
      shift 2
      ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${items_path}" ]]; then
  echo "Нужно указать --items-path /path/to/vacancy-batch.json" >&2
  exit 2
fi

require_file "${items_path}" "vacancy batch"
run_vacancy_cli import-json "${candidate_args[@]}" --source-kind "${source_kind}" --items-path "${items_path}"
