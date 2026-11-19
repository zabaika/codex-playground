#!/usr/bin/env bash
# Prepare the existing shared environments through the single runtime owner.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
source "$script_dir/python_runtime.sh"
case "${1:-}" in
  tests)
    shift
    requirements="$repo_root/telegram_connector/requirements.lock"
    verify=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --requirements) requirements="${2:?Missing candidate lock}"; shift 2 ;;
        --verify) verify=1; shift ;;
        *) echo "Unknown tests option: $1" >&2; exit 2 ;;
      esac
    done
    requirements="$(cd "$(dirname "$requirements")" && pwd)/$(basename "$requirements")"
    export PYTHON_RUNTIME_TEST_LOCK="$requirements"
    checks=(--import pytest --import telethon --import urllib3)
    if [[ "$verify" == 1 ]]; then
      for project in common telegram_shared telegram_connector telegram_agent_bot; do
        checks+=(--test-suite "$repo_root/$project/tests")
      done
    fi
    python_runtime_prepare --venv "$repo_root/.venv-test-gap-detection" \
      --requirements "$requirements" --require-hashes \
      --constraints "$repo_root/telegram_connector/requirements.txt" \
      --find-links "${PIP_FIND_LINKS:-$repo_root/telegram_connector/third_party}" \
      --scratch "$repo_root/scratch/python-runtime" \
      "${checks[@]}"
    ;;
  validation)
    python_runtime_prepare --venv "$repo_root/skills/.venv-validation" \
      --requirements "$repo_root/skills/requirements-validation.txt" \
      --scratch "$repo_root/scratch/python-runtime" --import yaml
    ;;
  *) echo 'Usage: bash common/setup_python.sh tests|validation' >&2; exit 2 ;;
esac
