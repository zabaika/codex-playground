#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest_root="${CODEX_HOME:-$HOME/.codex}/skills"
dest_dir="${dest_root}/agents-best-practices"

if [[ "${script_dir}" == "${dest_dir}" ]]; then
  echo "Run this installer from the repository source, not the installed copy." >&2
  exit 1
fi

mkdir -p "${dest_root}"
rm -rf "${dest_dir}"
cp -R "${script_dir}" "${dest_dir}"

echo "Installed agents-best-practices to ${dest_dir}"
