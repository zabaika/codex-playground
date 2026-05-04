#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest_root="${CODEX_HOME:-$HOME/.codex}/skills"
dest_dir="${dest_root}/llm-council"
repo_runtime_local="${script_dir}/config/runtime.local.toml"
dest_runtime_local="${dest_dir}/config/runtime.local.toml"

mkdir -p "${dest_root}"
rm -rf "${dest_dir}"
cp -R "${script_dir}" "${dest_dir}"
rm -f "${dest_runtime_local}"
ln -s "${repo_runtime_local}" "${dest_runtime_local}"

echo "Installed llm-council to ${dest_dir}"
echo "Linked ${dest_runtime_local} -> ${repo_runtime_local}"
echo "Restart Codex to pick up skill changes if the current session does not see them yet."
