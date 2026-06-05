#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/lib.sh"

content_path=""
source_kind="${SOURCE_KIND:-generic_text}"
source_origin="${SOURCE_ORIGIN:-manual_text}"
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
    --source-kind)
      source_kind="${2:-}"
      shift 2
      ;;
    --source-origin)
      source_origin="${2:-}"
      shift 2
      ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${content_path}" ]]; then
  echo "Нужно указать --content-path /path/to/vacancy-text.txt" >&2
  exit 2
fi

require_file "${content_path}" "generic vacancy text input"
run_vacancy_cli import-text "${candidate_args[@]}" --source-kind "${source_kind}" --source-origin "${source_origin}" --content-path "${content_path}"
