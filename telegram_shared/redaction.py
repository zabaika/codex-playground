"""Shared redaction helpers for bridge-facing text."""

from __future__ import annotations

import re


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"/Users/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"/home/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"/tmp/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"op://[^\s\"']+", "<secret_ref>", redacted)
    redacted = re.sub(r"bot\d{6,}:[A-Za-z0-9_-]+", "<bot_token>", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<api_key>", redacted)
    redacted = re.sub(r"(?i)authorization:\s*bearer\s+\S+", "Authorization: Bearer <redacted>", redacted)
    redacted = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._-]{12,}\b", "Bearer <redacted>", redacted)
    redacted = re.sub(r"(?i)\b(openai[_ -]?api[_ -]?key|bot[_ -]?token)\b\s*[:=]\s*\S+", r"\1=<redacted>", redacted)
    return redacted
