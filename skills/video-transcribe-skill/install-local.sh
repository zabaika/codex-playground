#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/common/python_runtime.sh" ]]; then
  common_root="$script_dir/common"
else
  common_root="$(cd "$script_dir/../../common" && pwd)"
fi
source "$common_root/python_runtime.sh"
bootstrap="$(python_runtime_bootstrap)"
exec "$bootstrap" -I "$common_root/ttl_runner.py" -- \
  "$bootstrap" -I "$script_dir/scripts/install_runtime.py"
