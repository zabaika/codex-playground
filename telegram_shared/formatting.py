"""Shared Telegram formatting helpers."""

from __future__ import annotations

import html
import re


def format_telegram_html(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip() or "<empty response>"
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    lines = [format_telegram_line(line) for line in normalized.split("\n")]
    return "\n".join(lines)


def format_telegram_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if stripped.startswith(("- ", "* ")):
        return f"• {format_inline_telegram_html(stripped[2:].strip())}"
    if re.fullmatch(r"/[a-z0-9_@ -]+", stripped, flags=re.IGNORECASE):
        return f"<code>{html.escape(stripped)}</code>"
    if stripped.endswith(":") and len(stripped) <= 80:
        return f"<b>{html.escape(stripped[:-1])}:</b>"
    return format_inline_telegram_html(stripped)


def format_inline_telegram_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    return escaped
