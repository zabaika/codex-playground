#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest_root="${CODEX_HOME:-$HOME/.codex}/skills"
dest_dir="${dest_root}/youtube-to-obsidian-kb"

mkdir -p "${dest_root}"
rm -rf "${dest_dir}"
cp -R "${script_dir}" "${dest_dir}"

echo "Installed youtube-to-obsidian-kb to ${dest_dir}"
echo "Restart Codex to pick up skill changes if the current session does not see them yet."
