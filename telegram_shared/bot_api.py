"""Shared Telegram Bot API helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from .errors import TelegramApiError


# Telegram Bot API text messages are capped at 4096 characters.
TELEGRAM_TEXT_MESSAGE_MAX_CHARS = 4096
# Avoid splitting at the very beginning of a chunk; short prefixes read worse than a hard cut.
MIN_NEWLINE_SPLIT_INDEX = 100


def api_call(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: int = 65,
    urlopen_func: Callable[..., Any] = request.urlopen,
) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urlopen_func(req, timeout=timeout_seconds) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except TimeoutError as exc:
        raise TelegramApiError(f"Telegram API request timed out while calling {method}.") from exc
    except error.HTTPError as exc:
        raise TelegramApiError(f"Telegram API HTTP {exc.code} while calling {method}.") from exc
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        details = f": {reason}" if reason else ""
        raise TelegramApiError(f"Telegram API request failed while calling {method}{details}.") from exc
    if not response.get("ok"):
        description = response.get("description") or "request failed"
        raise TelegramApiError(f"Telegram API error while calling {method}: {description}")
    return response["result"]


def fetch_updates(
    token: str,
    offset: int | None,
    timeout: int,
    *,
    api_call_func: Callable[..., Any],
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "edited_message"]}
    if offset is not None:
        payload["offset"] = offset
    # The HTTP envelope must outlive Telegram long polling, otherwise a valid
    # long poll can be cut off locally before Telegram returns.
    result = api_call_func(token, "getUpdates", payload, timeout_seconds=timeout + 5)
    if not isinstance(result, list):
        raise TelegramApiError(f"Unexpected getUpdates payload: {result}")
    return result


def load_offset(offset_file: Path) -> int | None:
    if not offset_file.exists():
        return None
    try:
        data = json.loads(offset_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data.get("offset")


def save_offset(offset_file: Path, offset: int) -> None:
    offset_file.parent.mkdir(parents=True, exist_ok=True)
    offset_file.write_text(json.dumps({"offset": offset}, ensure_ascii=True, indent=2), encoding="utf-8")


def append_jsonl_record(jsonl_file: Path, payload: dict[str, Any]) -> None:
    jsonl_file.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_text(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    return (
        message.get("text")
        or message.get("caption")
        or f"<non-text message keys={','.join(sorted(message.keys()))}>"
    )


def extract_chat_id(update: dict[str, Any]) -> int | None:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    return chat.get("id")


def extract_username(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    user = message.get("from") or {}
    return user.get("username") or user.get("first_name") or "unknown"


def extract_user_id(update: dict[str, Any]) -> int | None:
    message = update.get("message") or update.get("edited_message") or {}
    user = message.get("from") or {}
    return user.get("id")


def split_text_chunks(text: str, chunk_size: int) -> list[str]:
    remaining = text.strip() or "<empty response>"
    active_limit = min(TELEGRAM_TEXT_MESSAGE_MAX_CHARS, chunk_size)
    if len(remaining) <= active_limit:
        return [remaining]
    chunks: list[str] = []
    while remaining:
        chunk = remaining[:active_limit]
        split_at = chunk.rfind("\n")
        if split_at > MIN_NEWLINE_SPLIT_INDEX:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk):].lstrip()
    return chunks
