"""Check the maintained MCP configuration and optional installed-copy parity."""

import argparse
import json
from pathlib import Path
import re
import sys


def configuration_errors(configuration):
    server = configuration.get("mcpServers", {}).get("linkedin", {})
    arguments = server.get("args", [])
    if not isinstance(arguments, list) or not all(
        isinstance(value, str) for value in arguments
    ):
        return ["LinkedIn server arguments must be a list of strings."]
    errors = []
    if server.get("command") != "uvx":
        errors.append("The reviewed launcher must remain uvx.")
    if not arguments or not re.fullmatch(
        r"mcp-server-linkedin==[0-9]+\.[0-9]+\.[0-9]+", arguments[0]
    ):
        errors.append("Pin one exact server release; latest/ranges are forbidden.")
    if "--no-auto-import" not in arguments:
        errors.append("The --no-auto-import flag is required.")
    if any(
        value == "--auto-import"
        or value.split("=", 1)[0] == "--import-from-browser"
        for value in arguments
    ):
        errors.append("Browser-session import must not be enabled.")
    return errors


def inventory(root):
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks require a separate review.")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path)
    options = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    try:
        configuration = json.loads((source / ".mcp.json").read_text())
        errors = configuration_errors(configuration)
        if options.installed_root is not None:
            if not options.installed_root.is_dir():
                errors.append("Installed plugin directory does not exist.")
            elif inventory(source) != inventory(options.installed_root):
                errors.append("Source/install file inventory or bytes differ.")
    except (OSError, ValueError, AttributeError, TypeError):
        errors = ["Cannot validate plugin files or configuration."]
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact server pin, disabled auto-import" + (
        ", complete source/install parity" if options.installed_root else ""
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
