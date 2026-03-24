#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest_root="${CODEX_HOME:-$HOME/.codex}/skills"
dest_dir="${dest_root}/youtube-transcribe-skill"

if [[ -e "${dest_dir}" ]]; then
  echo "Refusing to overwrite existing skill: ${dest_dir}" >&2
  exit 1
fi

mkdir -p "${dest_root}"
cp -R "${script_dir}" "${dest_dir}"

echo "Installed youtube-transcribe-skill to ${dest_dir}"
echo "Restart Codex to pick up new skills."

