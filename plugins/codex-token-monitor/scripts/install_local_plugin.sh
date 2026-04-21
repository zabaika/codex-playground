#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_root="$(cd "$script_dir/.." && pwd)"
plugin_name="$(basename "$plugin_root")"
plugins_root="${HOME}/.codex/plugins"
marketplace_dir="${HOME}/.agents/plugins"
marketplace_path="${marketplace_dir}/marketplace.json"
target_path="${plugins_root}/${plugin_name}"

mkdir -p "$plugins_root" "$marketplace_dir"
rm -rf "$target_path"
cp -R "$plugin_root" "$target_path"

python3 - "$marketplace_path" "$plugin_name" <<'PY'
import json
import pathlib
import sys

marketplace_path = pathlib.Path(sys.argv[1])
plugin_name = sys.argv[2]
entry = {
    "name": plugin_name,
    "source": {
        "source": "local",
        "path": f"./.codex/plugins/{plugin_name}",
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Productivity",
}

if marketplace_path.exists():
    data = json.loads(marketplace_path.read_text(encoding="utf-8"))
else:
    data = {
        "name": "local-plugins",
        "interface": {
            "displayName": "Local Plugins",
        },
        "plugins": [],
    }

plugins = [plugin for plugin in data.get("plugins", []) if plugin.get("name") != plugin_name]
plugins.append(entry)
data["plugins"] = plugins
marketplace_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

printf 'Installed %s at %s\n' "$plugin_name" "$target_path"
printf 'Updated marketplace at %s\n' "$marketplace_path"
printf 'Restart Codex to pick up plugin changes.\n'
