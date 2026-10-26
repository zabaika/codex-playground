#!/usr/bin/env bash
# Prepare the existing shared environments through the single runtime owner.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
source "$script_dir/python_runtime.sh"
case "${1:-}" in
  tests)
    python_runtime_prepare --venv "$repo_root/.venv-test-gap-detection" \
      --requirements "$repo_root/telegram_connector/requirements.txt" \
      --scratch "$repo_root/scratch/python-runtime" \
      --import pytest --import telethon --import urllib3
    ;;
  validation)
    python_runtime_prepare --venv "$repo_root/skills/.venv-validation" \
      --requirements "$repo_root/skills/requirements-validation.txt" \
      --scratch "$repo_root/scratch/python-runtime" --import yaml
    ;;
  *) echo 'Usage: bash common/setup_python.sh tests|validation' >&2; exit 2 ;;
esac
