#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import tomllib
from urllib import error, parse, request


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
BASE_DIR = PROJECT_ROOT
CONFIG_DIR = BASE_DIR / "config"
RUNTIME_LOCAL_FILE = CONFIG_DIR / "runtime.local.toml"
DATA_DIR = BASE_DIR / "data"
OFFSET_FILE = DATA_DIR / "offset.local.json"
INBOX_FILE = DATA_DIR / "inbox.jsonl"
HISTORY_CLIENT_FILE = APP_DIR / "telegram_history_client.py"
EXPORT_DIR = DATA_DIR / "exports"
OP_REFERENCE_PREFIX = "op://"
_SECRET_CACHE: dict[str, str] = {}


def load_runtime_config() -> dict[str, Any]:
    if not RUNTIME_LOCAL_FILE.exists():
        return {}
    with RUNTIME_LOCAL_FILE.open("rb") as fh:
        data = tomllib.load(fh)
    return data if isinstance(data, dict) else {}


def get_config_value(config: dict[str, Any], section: str, key: str) -> str:
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return ""
    value = section_data.get(key, "")
    return str(value).strip()


def resolve_onepassword_secret(reference: str, label: str) -> str:
    cached = _SECRET_CACHE.get(reference)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            f"1Password CLI 'op' is required to resolve {label}. Install 1Password CLI and sign in first."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"Timed out while resolving {label} from 1Password.") from exc
    if completed.returncode != 0:
        raise SystemExit(
            f"Failed to resolve {label} from 1Password. Make sure 'op' is signed in and the secret reference is valid."
        )
    value = completed.stdout.strip()
    _SECRET_CACHE[reference] = value
    return value


def resolve_secret_value(raw_value: str, label: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith(OP_REFERENCE_PREFIX):
        return resolve_onepassword_secret(value, label)
    return value


def require_token() -> str:
    config = load_runtime_config()
    token = resolve_secret_value(
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or get_config_value(config, "secrets", "bot_token"),
        "Telegram bot token",
    )
    if not token:
        raise SystemExit(
            "Missing Telegram bot token. Put it into telegram_connector/config/runtime.local.toml under [secrets].bot_token."
        )
    return token


def parse_allowed_chat_ids(config: dict[str, Any]) -> set[str]:
    raw = get_config_value(config, "bridge", "allowed_chat_ids")
    if not raw:
        default_chat = get_config_value(config, "telegram", "default_chat_id")
        return {default_chat} if default_chat else set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def resolve_history_client_path(config: dict[str, Any]) -> Path:
    raw = get_config_value(config, "bridge", "history_client_path")
    return Path(raw) if raw else HISTORY_CLIENT_FILE


def api_call(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with request.urlopen(req, timeout=65) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"Telegram API HTTP {exc.code} while calling {method}.") from exc
    except error.URLError as exc:
        raise SystemExit(f"Telegram API request failed while calling {method}.") from exc

    if not response.get("ok"):
        description = response.get("description") or "request failed"
        raise SystemExit(f"Telegram API error while calling {method}: {description}")
    return response["result"]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_offset() -> int | None:
    if not OFFSET_FILE.exists():
        return None
    try:
        data = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data.get("offset")


def save_offset(offset: int) -> None:
    ensure_data_dir()
    OFFSET_FILE.write_text(json.dumps({"offset": offset}, ensure_ascii=True, indent=2), encoding="utf-8")


def redact_update_for_storage(update: dict[str, Any]) -> dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    text = message.get("text") or message.get("caption") or ""
    redacted: dict[str, Any] = {
        "update_id": update.get("update_id"),
        "kind": "edited_message" if update.get("edited_message") else "message",
        "chat_id": chat.get("id"),
        "chat_type": chat.get("type"),
        "from_id": user.get("id"),
        "from_username": user.get("username"),
        "date": message.get("date"),
        "has_text": bool(text),
        "text_length": len(text),
    }
    if text.startswith("/"):
        redacted["command"] = text.split(maxsplit=1)[0]
    return redacted


def append_inbox(update: dict[str, Any]) -> None:
    ensure_data_dir()
    with INBOX_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(redact_update_for_storage(update), ensure_ascii=False) + "\n")


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


def extract_date(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    unix_ts = message.get("date")
    if not unix_ts:
        return "-"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def send_text_message(token: str, chat_id: str | int, text: str) -> None:
    api_call(
        token,
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
        },
    )


def send_text_chunks(token: str, chat_id: str | int, text: str, chunk_size: int = 3500) -> None:
    remaining = text.strip() or "<empty response>"
    while remaining:
        chunk = remaining[:chunk_size]
        split_at = chunk.rfind("\n")
        if split_at > 100:
            chunk = chunk[:split_at]
        send_text_message(token, chat_id, chunk)
        remaining = remaining[len(chunk):].lstrip()


def send_document(token: str, chat_id: str | int, file_path: Path, caption: str = "") -> None:
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    body_parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        f"{chat_id}\r\n".encode(),
    ]
    if caption:
        body_parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="caption"\r\n\r\n',
                f"{caption}\r\n".encode(),
            ]
        )
    body_parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"\r\n'.encode(),
            b"Content-Type: text/csv\r\n\r\n",
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    req = request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=b"".join(body_parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=65) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"Telegram API HTTP {exc.code} while calling sendDocument.") from exc
    except error.URLError as exc:
        raise SystemExit("Telegram API request failed while calling sendDocument.") from exc
    if not response.get("ok"):
        description = response.get("description") or "request failed"
        raise SystemExit(f"Telegram API error while calling sendDocument: {description}")


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"/Users/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"/home/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"/tmp/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"bot\d{6,}:[A-Za-z0-9_-]+", "<bot_token>", redacted)
    return redacted


def build_safe_command_response(command_text: str, completed: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any] | None]:
    status = "ok" if completed.returncode == 0 else f"failed ({completed.returncode})"
    lines = [f"Command: {command_text}", f"Status: {status}"]
    output = (completed.stdout or "").strip()
    error_output = redact_sensitive_text((completed.stderr or "").strip())
    json_output = None
    if output:
        try:
            json_output = json.loads(output)
        except json.JSONDecodeError:
            json_output = None
    if completed.returncode == 0 and isinstance(json_output, dict):
        safe_keys = [
            "channel",
            "channel_id",
            "mode",
            "auth_mode",
            "processed_messages",
            "skipped_existing",
            "refreshed_existing_media",
            "download_media",
            "ocr_processed",
            "processed_assets",
            "status",
            "row_count",
            "limit",
            "since",
            "until",
            "output_file",
        ]
        for key in safe_keys:
            if key in json_output and json_output[key] not in {None, ""}:
                lines.append(f"{key}: {json_output[key]}")
    elif error_output:
        lines.append(f"error: {error_output}")
    elif output:
        lines.append(redact_sensitive_text(output))
    return "\n".join(lines), json_output if isinstance(json_output, dict) else None


def command_help_text() -> str:
    return (
        "Available commands:\n"
        "/help\n"
        "/doctor\n"
        "/state\n"
        "/backfill @channel [limit] [media] [bot|user|auto]\n"
        "/tail @channel [limit] [media|ocr] [bot|user|auto]\n"
        "/update @channel [limit] [media|ocr] [bot|user|auto]\n"
        "/ocrhistory @channel [limit] [bot|user|auto]\n"
        "/exportcsv @channel [limit] [bot|user|auto]\n"
        "/exportcsv @channel since=YYYY-MM-DD until=YYYY-MM-DD [bot|user|auto]\n"
        "/ocr [limit]\n"
        "\nExamples:\n"
        "/backfill @vcnews 200\n"
        "/tail @vcnews 100 ocr\n"
        "/update @vcnews 100\n"
        "/tail @vcnews 100 media\n"
        "/ocrhistory @vcnews 50\n"
        "/exportcsv @vcnews 100\n"
        "/exportcsv @vcnews since=2026-03-15\n"
        "/backfill https://t.me/+invitehash 100 user\n"
        "/ocr 50\n"
        "\nFlags meaning:\n"
        "media = download media only\n"
        "ocr = download image media and run OCR"
    )


def build_history_command(text: str) -> list[str] | None:
    parts = shlex.split(text)
    if not parts:
        return None

    command = parts[0].lower()
    base = [sys.executable, str(HISTORY_CLIENT_FILE)]

    if command == "/help":
        return []
    if command == "/doctor":
        return base + ["doctor"]
    if command == "/state":
        return base + ["inspect-state"]
    if command == "/ocr":
        limit = "100"
        if len(parts) >= 2:
            limit = parts[1]
        return base + ["ocr-pending", "--limit", limit]
    if command == "/exportcsv":
        if len(parts) < 2:
            raise ValueError("Channel is required. Example: /exportcsv @vcnews 100")
        argv = base + ["export-csv", "--channel", parts[1]]
        auth_mode = "user"
        has_filter = False
        for part in parts[2:]:
            lowered = part.lower()
            if part.isdigit():
                argv += ["--limit", part]
                has_filter = True
            elif lowered.startswith("since="):
                argv += ["--since", part.split("=", 1)[1]]
                has_filter = True
            elif lowered.startswith("until="):
                argv += ["--until", part.split("=", 1)[1]]
                has_filter = True
            elif lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
        if not has_filter:
            argv += ["--limit", "100"]
        return argv + ["--auth-mode", auth_mode]
    if command == "/ocrhistory":
        if len(parts) < 2:
            raise ValueError("Channel is required. Example: /ocrhistory @vcnews 50 user")
        argv = base + ["tail", "--channel", parts[1]]
        limit = "100"
        auth_mode = "user"
        if len(parts) >= 3 and parts[2].isdigit():
            limit = parts[2]
        argv += ["--limit", limit, "--download-media", "--ocr"]
        for part in parts[2:]:
            lowered = part.lower()
            if lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
        return argv + ["--auth-mode", auth_mode]
    if command in {"/backfill", "/tail", "/update"}:
        if len(parts) < 2:
            raise ValueError("Channel is required. Example: /tail @vcnews 100")
        if command == "/backfill":
            subcommand = "backfill"
        elif command == "/update":
            subcommand = "update"
        else:
            subcommand = "tail"
        argv = base + [subcommand, "--channel", parts[1]]
        limit = "1000" if subcommand == "backfill" else "100"
        auth_mode = "user"
        if len(parts) >= 3 and parts[2].isdigit():
            limit = parts[2]
        argv += ["--limit", limit]
        for part in parts[2:]:
            lowered = part.lower()
            if lowered == "media":
                argv.append("--download-media")
            if lowered == "ocr":
                if "--download-media" not in argv:
                    argv.append("--download-media")
                argv.append("--ocr")
            if lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
        argv += ["--auth-mode", auth_mode]
        return argv
    raise ValueError(f"Unsupported command: {parts[0]}")


def handle_history_command(token: str, config: dict[str, Any], update: dict[str, Any]) -> None:
    chat_id = extract_chat_id(update)
    if chat_id is None:
        return

    allowed_chat_ids = parse_allowed_chat_ids(config)
    if allowed_chat_ids and str(chat_id) not in allowed_chat_ids:
        send_text_message(token, chat_id, f"Chat {chat_id} is not allowed to run bridge commands.")
        return

    text = extract_text(update).strip()
    if not text.startswith("/"):
        return

    if text == "/help":
        send_text_message(token, chat_id, command_help_text())
        return

    history_client_path = resolve_history_client_path(config)
    if not history_client_path.exists():
        send_text_message(token, chat_id, f"History client not found: {history_client_path}")
        return

    try:
        argv = build_history_command(text)
    except ValueError as exc:
        send_text_message(token, chat_id, f"{exc}\n\n{command_help_text()}")
        return

    if not argv:
        send_text_message(token, chat_id, command_help_text())
        return

    try:
        completed = subprocess.run(
            argv,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        send_text_message(token, chat_id, "Command timed out after 3600 seconds.")
        return

    safe_response, json_output = build_safe_command_response(" ".join(argv[2:]), completed)
    send_text_chunks(token, chat_id, safe_response)
    if completed.returncode == 0 and isinstance(json_output, dict) and json_output.get("output_file"):
        output_path = EXPORT_DIR / str(json_output["output_file"])
        if output_path.exists():
            send_document(token, chat_id, output_path, caption=f"CSV export: {output_path.name}")


def cmd_get_me(args: argparse.Namespace) -> int:
    token = require_token()
    result = api_call(token, "getMe")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    token = require_token()
    config = load_runtime_config()
    chat_id = (
        args.chat_id
        or get_config_value(config, "telegram", "default_chat_id")
        or os.environ.get("TELEGRAM_DEFAULT_CHAT_ID", "").strip()
    )
    if not chat_id:
        raise SystemExit(
            "Missing chat id. Pass --chat-id or set [telegram].default_chat_id in telegram_connector/config/runtime.local.toml."
        )
    result = api_call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": args.text,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def fetch_updates(token: str, offset: int | None, timeout: int) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "edited_message"]}
    if offset is not None:
        payload["offset"] = offset
    result = api_call(token, "getUpdates", payload)
    if not isinstance(result, list):
        raise SystemExit(f"Unexpected getUpdates payload: {result}")
    return result


def print_update(update: dict[str, Any]) -> None:
    chat_id = extract_chat_id(update)
    username = extract_username(update)
    text = extract_text(update)
    date = extract_date(update)
    command = text.split(maxsplit=1)[0] if text.startswith("/") else "<message>"
    print(f"[{date}] chat_id={chat_id} from={username} event={command} text_length={len(text)}")


def cmd_listen(args: argparse.Namespace) -> int:
    token = require_token()
    config = load_runtime_config()
    offset = None if args.from_scratch else load_offset()
    print("Listening for Telegram updates.")

    while True:
        updates = fetch_updates(token, offset, args.timeout)
        if not updates:
            if args.once:
                return 0
            continue

        for update in updates:
            append_inbox(update)
            print_update(update)
            update_id = update["update_id"]
            offset = update_id + 1
            save_offset(offset)

            if args.echo:
                chat_id = extract_chat_id(update)
                if chat_id is not None:
                    send_text_message(token, chat_id, f"Echo: {extract_text(update)}")

            if args.run_commands:
                handle_history_command(token, config, update)

        if args.once:
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal Telegram bot bridge using Bot API long polling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_me = subparsers.add_parser("get-me", help="Check bot connectivity and print bot metadata.")
    get_me.set_defaults(func=cmd_get_me)

    send = subparsers.add_parser("send", help="Send a text message to a chat.")
    send.add_argument("--chat-id", help="Telegram chat id. If omitted, TELEGRAM_DEFAULT_CHAT_ID is used.")
    send.add_argument("text", help="Message text to send.")
    send.set_defaults(func=cmd_send)

    listen = subparsers.add_parser("listen", help="Receive incoming updates via long polling.")
    listen.add_argument("--timeout", type=int, default=30, help="Long polling timeout in seconds.")
    listen.add_argument("--once", action="store_true", help="Poll once and exit.")
    listen.add_argument("--echo", action="store_true", help="Echo received text back into the same chat.")
    listen.add_argument(
        "--run-commands",
        action="store_true",
        help="Allow incoming bot commands to control telegram_history_client.py for approved chat ids.",
    )
    listen.add_argument(
        "--from-scratch",
        action="store_true",
        help="Ignore saved offset and read updates from the current backlog.",
    )
    listen.set_defaults(func=cmd_listen)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
