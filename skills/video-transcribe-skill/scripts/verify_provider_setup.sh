#!/usr/bin/env bash
set -euo pipefail

url="${1:-https://www.youtube.com/watch?v=dQw4w9WgXcQ}"
config_path="${2:-$HOME/.codex/skills/video-transcribe-skill/config/runtime.local.toml}"

python3 "$HOME/.codex/skills/video-transcribe-skill/scripts/run_video_transcribe.py" \
  --config "$config_path" \
  --url "$url"
