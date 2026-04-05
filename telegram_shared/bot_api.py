"""Shared Telegram Bot API helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib import error, request


def api_call(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with request.urlopen(req, timeout=65) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except TimeoutError as exc:
        raise SystemExit(f"Telegram API request timed out while calling {method}.") from exc
    except error.HTTPError as exc:
        raise SystemExit(f"Telegram API HTTP {exc.code} while calling {method}.") from exc
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        details = f": {reason}" if reason else ""
        raise SystemExit(f"Telegram API request failed while calling {method}{details}.") from exc
    if not response.get("ok"):
        description = response.get("description") or "request failed"
        raise SystemExit(f"Telegram API error while calling {method}: {description}")
    return response["result"]


def fetch_updates(
    token: str,
    offset: int | None,
    timeout: int,
    *,
    api_call_func: Callable[[str, str, dict[str, Any] | None], Any],
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "edited_message"]}
    if offset is not None:
        payload["offset"] = offset
    result = api_call_func(token, "getUpdates", payload)
    if not isinstance(result, list):
        raise SystemExit(f"Unexpected getUpdates payload: {result}")
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
    active_limit = min(4096, chunk_size)
    if len(remaining) <= active_limit:
        return [remaining]
    chunks: list[str] = []
    while remaining:
        chunk = remaining[:chunk_size]
        split_at = chunk.rfind("\n")
        if split_at > 100:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk):].lstrip()
    return chunks
