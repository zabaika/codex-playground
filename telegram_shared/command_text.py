"""Shared command-text normalization helpers for Telegram bridges."""

from __future__ import annotations

import shlex


def normalize_bridge_command_text(
    text: str,
    *,
    supported_commands: set[str],
    default_command: str = "",
) -> str:
    raw = text.strip()
    if not raw:
        return ""
    parts = shlex.split(raw)
    if not parts:
        return ""
    command = parts[0].strip()
    if command.startswith("/"):
        bare = command[1:]
        if "@" in bare:
            bare = bare.split("@", 1)[0]
        parts[0] = f"/{bare.lower()}"
        return " ".join(parts)
    normalized = command.lower()
    if normalized in supported_commands:
        parts[0] = f"/{normalized}"
        return " ".join(parts)
    if default_command:
        return f"/{default_command} {raw}".strip()
    return raw
