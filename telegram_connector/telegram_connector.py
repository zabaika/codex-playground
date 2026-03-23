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
DIGEST_FILE = APP_DIR / "telegram_digest.py"
EXPORT_DIR = DATA_DIR / "exports"
OP_REFERENCE_PREFIX = "op://"
_SECRET_CACHE: dict[str, str] = {}
SUPPORTED_BRIDGE_COMMANDS = {"help", "ocr", "exportcsv", "ocrhistory", "backfill", "tail", "update", "digest"}
HISTORY_CLIENT_SECRET_ENV_MAP = {
    "TELEGRAM_API_ID": ("telethon", "api_id", "Telegram API ID"),
    "TELEGRAM_API_HASH": ("secrets", "api_hash", "Telegram API hash"),
    "TELEGRAM_PHONE": ("telethon", "phone", "Telegram phone"),
    "TELEGRAM_BOT_TOKEN": ("secrets", "bot_token", "Telegram bot token"),
    "TELEGRAM_USER_PASSWORD": ("secrets", "user_password", "Telegram user password"),
    "OPENAI_API_KEY": ("secrets", "openai_api_key", "OpenAI API key"),
}
SAFE_SUBPROCESS_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
}


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


def resolve_bridge_secrets(config: dict[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for env_name, (section, key, label) in HISTORY_CLIENT_SECRET_ENV_MAP.items():
        raw_value = os.environ.get(env_name, "").strip() or get_config_value(config, section, key)
        value = resolve_secret_value(raw_value, label)
        if value:
            resolved[env_name] = value
    return resolved


def require_bot_token_from_secrets(secret_env: dict[str, str]) -> str:
    token = secret_env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Missing Telegram bot token. Put it into telegram_connector/config/runtime.local.toml under [secrets].bot_token."
        )
    return token


def build_history_client_subprocess_env(secret_env: dict[str, str]) -> dict[str, str]:
    child_env = {key: value for key, value in os.environ.items() if key in SAFE_SUBPROCESS_ENV_KEYS}
    project_root = os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "").strip()
    if project_root:
        child_env["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = project_root
    child_env.update({key: value for key, value in secret_env.items() if value})
    return child_env


def parse_allowed_chat_ids(config: dict[str, Any]) -> set[str]:
    raw = get_config_value(config, "bridge", "allowed_chat_ids")
    if not raw:
        default_chat = get_config_value(config, "telegram", "default_chat_id")
        return {default_chat} if default_chat else set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def resolve_sync_mode_limit(config: dict[str, Any], mode: str) -> str:
    raw = get_config_value(config, "sync", f"{mode}_limit")
    if not raw:
        raise SystemExit(f"Missing sync.{mode}_limit in runtime config.")
    return raw


def resolve_text_chunk_size(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "text_chunk_size")
    try:
        value = int(raw) if raw else 3900
    except ValueError:
        value = 3900
    return max(500, min(4096, value))


def is_channel_token(value: str) -> bool:
    token = value.strip()
    if not token:
        return False
    parts = [item.strip() for item in token.split(",") if item.strip()]
    if not parts:
        return False
    for part in parts:
        if part.startswith("@") or part.startswith("https://t.me/") or part.startswith("t.me/"):
            continue
        if part.lstrip("-").isdigit() and not part.isdigit():
            continue
        return False
    return True


def normalize_bridge_command_text(text: str) -> str:
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
    if normalized in SUPPORTED_BRIDGE_COMMANDS:
        parts[0] = f"/{normalized}"
        return " ".join(parts)
    return raw


def sanitize_text_for_storage(text: str) -> str:
    sanitized_chars: list[str] = []
    for char in text:
        if char in {"\n", "\r", "\t"} or ord(char) < 32:
            sanitized_chars.append(" ")
        else:
            sanitized_chars.append(char)
    return re.sub(r"\s+", " ", "".join(sanitized_chars)).strip()


def is_channel_fragment(token: str) -> bool:
    raw = token.strip()
    if not raw:
        return False
    if raw == ",":
        return True
    trimmed = raw.strip(",").strip()
    if not trimmed:
        return True
    if trimmed.startswith("@") or trimmed.startswith("https://t.me/") or trimmed.startswith("t.me/"):
        return True
    return trimmed.lstrip("-").isdigit() and not trimmed.isdigit()


def consume_channel_argument(parts: list[str]) -> tuple[str | None, list[str]]:
    if not parts:
        return None, []

    consumed: list[str] = []
    remaining = list(parts)
    while remaining:
        token = remaining[0]
        if is_channel_fragment(token):
            consumed.append(token)
            remaining.pop(0)
            continue
        break

    if not consumed:
        return None, parts
    normalized = re.sub(r"\s*,\s*", ", ", " ".join(consumed)).strip()
    return normalized, remaining


def resolve_history_client_path(config: dict[str, Any]) -> Path:
    raw = get_config_value(config, "bridge", "history_client_path")
    return Path(raw) if raw else HISTORY_CLIENT_FILE


def append_option(argv: list[str], name: str, value: str) -> None:
    if value.startswith("-"):
        argv.append(f"{name}={value}")
    else:
        argv.extend([name, value])


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
    normalized = normalize_bridge_command_text(text)
    if normalized.startswith("/"):
        redacted["command"] = normalized.split(maxsplit=1)[0]
        redacted["command_text"] = sanitize_text_for_storage(normalized)
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


def send_text_chunks(token: str, chat_id: str | int, text: str, chunk_size: int | None = None) -> None:
    active_chunk_size = resolve_text_chunk_size() if chunk_size is None else chunk_size
    remaining = text.strip() or "<empty response>"
    if len(remaining) <= min(4096, active_chunk_size):
        send_text_message(token, chat_id, remaining)
        return
    while remaining:
        chunk = remaining[:active_chunk_size]
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
            "sync_mode",
            "limit_profile",
            "processed_messages",
            "skipped_existing",
            "refreshed_existing_media",
            "download_media",
            "ocr_processed",
            "processed_assets",
            "status",
            "channels",
            "row_count",
            "limit",
            "sync_limit",
            "batch_size",
            "since",
            "until",
            "output_file",
            "current_read_max_id",
            "marked_read",
            "marked_read_from",
            "marked_read_until",
        ]
        for key in safe_keys:
            if key in json_output and json_output[key] not in {None, ""}:
                lines.append(f"{key}: {json_output[key]}")
        if "error" in json_output and json_output["error"] not in {None, ""}:
            lines.append(f"error: {redact_sensitive_text(str(json_output['error']))}")
        if "errors" in json_output and json_output["errors"]:
            for item in json_output["errors"]:
                lines.append(f"error: {redact_sensitive_text(str(item))}")
    elif error_output:
        lines.append(f"error: {error_output}")
    elif output:
        lines.append(redact_sensitive_text(output))
    return "\n".join(lines), json_output if isinstance(json_output, dict) else None


def build_safe_command_response_any(command_text: str, completed: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any] | list[dict[str, Any]] | None]:
    output = (completed.stdout or "").strip()
    json_output: dict[str, Any] | list[dict[str, Any]] | None = None
    if output:
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                return build_safe_command_response(command_text, completed)
            if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
                lines = [f"Command: {command_text}", f"Status: {'ok' if completed.returncode == 0 else f'failed ({completed.returncode})'}"]
                safe_keys = ["channel", "channel_id", "mode", "auth_mode", "status", "processed_messages", "skipped_existing", "row_count", "output_file", "limit", "since", "until"]
                safe_keys += ["current_read_max_id", "marked_read", "marked_read_from", "marked_read_until"]
                for item in parsed:
                    summary = []
                    for key in safe_keys:
                        if key in item and item[key] not in {None, ""}:
                            summary.append(f"{key}={item[key]}")
                    if item.get("error"):
                        summary.append(f"error={redact_sensitive_text(str(item['error']))}")
                    lines.append(", ".join(summary))
                return "\n".join(lines), parsed
        except json.JSONDecodeError:
            pass
    return build_safe_command_response(command_text, completed)


def command_help_text() -> str:
    return (
        "Bot commands:\n"
        "/help\n"
        "/backfill [channel] [limit] [since=YYYY-MM-DD] [until=YYYY-MM-DD] [media] [bot|user|auto]\n"
        "  historical load into SQLite\n"
        "/tail [channel] [limit] [since=YYYY-MM-DD] [until=YYYY-MM-DD] [media|ocr|read] [bot|user|auto]\n"
        "  latest window sync\n"
        "/update [channel] [limit] [since=YYYY-MM-DD] [until=YYYY-MM-DD] [media|ocr|read] [bot|user|auto]\n"
        "  only messages newer than saved history\n"
        "/ocrhistory [channel] [limit] [since=YYYY-MM-DD] [until=YYYY-MM-DD] [bot|user|auto]\n"
        "  tail + media download + OCR\n"
        "/digest [channel] [since=YYYY-MM-DD] [until=YYYY-MM-DD] [today|yesterday|week|month|-Nd] [bot|user|auto]\n"
        "  config-driven morning AI digest and Telegram delivery\n"
        "/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]\n"
        "  export saved history to CSV\n"
        "/ocr [limit] [channel] [since=YYYY-MM-DD] [until=YYYY-MM-DD]\n"
        "  OCR only for already-downloaded pending images\n"
        "\nNotes:\n"
        "- channel may be omitted to use default channels from config\n"
        "- channel may be a comma-separated list\n"
        "- auth defaults to user\n"
        "- since = start of day UTC, until = end of day UTC\n"
        "- since/until aliases: today, yesterday, week, month, -Nd\n"
        "- media downloads files only\n"
        "- ocr downloads image media and runs OCR\n"
        "- read is optional and only works in user auth mode\n"
        "\nExamples:\n"
        "/backfill 200\n"
        "/tail @vcnews 100 ocr\n"
        "/update @vcnews 100 read\n"
        "/digest\n"
        "/digest -3d\n"
        "/digest since=week\n"
        "/digest @vcnews since=2026-03-15 until=2026-03-16\n"
        "/exportcsv @vcnews since=2026-03-15\n"
        "/ocr @vcnews since=2026-03-15 until=2026-03-16"
    )


def build_history_command(text: str) -> list[str] | None:
    parts = shlex.split(text)
    if not parts:
        return None

    config = load_runtime_config()
    command = parts[0].lower()
    base = [sys.executable, str(HISTORY_CLIENT_FILE)]
    digest_base = [sys.executable, str(DIGEST_FILE)]

    if command == "/help":
        return []
    if command == "/digest":
        argv = digest_base + ["run"]
        auth_mode = "user"
        single_window_token = None
        extra_parts = parts[1:]
        channel_arg, extra_parts = consume_channel_argument(extra_parts)
        if channel_arg:
            argv += ["--channel", channel_arg]
        for part in extra_parts:
            lowered = part.lower()
            if lowered.startswith("since="):
                append_option(argv, "--since", part.split("=", 1)[1])
            elif lowered.startswith("until="):
                append_option(argv, "--until", part.split("=", 1)[1])
            elif lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
            elif re.fullmatch(r"(today|yesterday|week|month|-?\d+d|\d{4}-\d{2}-\d{2})", lowered):
                if single_window_token is not None:
                    raise ValueError(f"Unsupported digest argument: {part}")
                single_window_token = part
            else:
                raise ValueError(f"Unsupported digest argument: {part}")
        if single_window_token is not None:
            append_option(argv, "--since", single_window_token)
            append_option(argv, "--until", single_window_token)
        return argv + ["--auth-mode", auth_mode]
    if command == "/ocr":
        limit = "100"
        argv = base + ["ocr-pending"]
        extra_parts = parts[1:]
        channel_arg, extra_parts = consume_channel_argument(extra_parts)
        if channel_arg:
            argv += ["--channel", channel_arg]
        if extra_parts and extra_parts[0].isdigit():
            limit = extra_parts[0]
        argv += ["--limit", limit]
        for part in extra_parts:
            lowered = part.lower()
            if lowered.startswith("since="):
                append_option(argv, "--since", part.split("=", 1)[1])
            elif lowered.startswith("until="):
                append_option(argv, "--until", part.split("=", 1)[1])
        return argv
    if command == "/exportcsv":
        argv = base + ["export-csv"]
        auth_mode = "user"
        has_filter = False
        extra_parts = parts[1:]
        channel_arg, extra_parts = consume_channel_argument(extra_parts)
        if channel_arg:
            argv += ["--channel", channel_arg]
        for part in extra_parts:
            lowered = part.lower()
            if part.isdigit():
                argv += ["--limit", part]
                has_filter = True
            elif lowered.startswith("since="):
                append_option(argv, "--since", part.split("=", 1)[1])
                has_filter = True
            elif lowered.startswith("until="):
                append_option(argv, "--until", part.split("=", 1)[1])
                has_filter = True
            elif lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
        if not has_filter:
            argv += ["--limit", "100"]
        return argv + ["--auth-mode", auth_mode]
    if command == "/ocrhistory":
        argv = base + ["sync", "--mode", "tail"]
        limit = resolve_sync_mode_limit(config, "tail")
        auth_mode = "user"
        extra_parts = parts[1:]
        channel_arg, extra_parts = consume_channel_argument(extra_parts)
        if channel_arg:
            argv += ["--channel", channel_arg]
        if extra_parts and extra_parts[0].isdigit():
            limit = extra_parts[0]
        argv += ["--limit", limit, "--download-media", "--ocr"]
        for part in extra_parts:
            lowered = part.lower()
            if lowered.startswith("since="):
                append_option(argv, "--since", part.split("=", 1)[1])
            elif lowered.startswith("until="):
                append_option(argv, "--until", part.split("=", 1)[1])
            if lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
        return argv + ["--auth-mode", auth_mode]
    if command in {"/backfill", "/tail", "/update"}:
        if command == "/backfill":
            subcommand = "backfill"
        elif command == "/update":
            subcommand = "update"
        else:
            subcommand = "tail"
        argv = base + ["sync", "--mode", subcommand]
        limit = resolve_sync_mode_limit(config, subcommand)
        auth_mode = "user"
        extra_parts = parts[1:]
        channel_arg, extra_parts = consume_channel_argument(extra_parts)
        if channel_arg:
            argv += ["--channel", channel_arg]
        if extra_parts and extra_parts[0].isdigit():
            limit = extra_parts[0]
        argv += ["--limit", limit]
        for part in extra_parts:
            lowered = part.lower()
            if lowered.startswith("since="):
                append_option(argv, "--since", part.split("=", 1)[1])
            if lowered.startswith("until="):
                append_option(argv, "--until", part.split("=", 1)[1])
            if lowered == "media":
                argv.append("--download-media")
            if lowered == "ocr":
                if "--download-media" not in argv:
                    argv.append("--download-media")
                argv.append("--ocr")
            if lowered == "read":
                argv.append("--mark-read")
            if lowered in {"auto", "bot", "user"}:
                auth_mode = lowered
        argv += ["--auth-mode", auth_mode]
        return argv
    raise ValueError(f"Unsupported command: {parts[0]}")


def handle_history_command(token: str, config: dict[str, Any], update: dict[str, Any], secret_env: dict[str, str] | None = None) -> None:
    chat_id = extract_chat_id(update)
    if chat_id is None:
        return

    allowed_chat_ids = parse_allowed_chat_ids(config)
    if allowed_chat_ids and str(chat_id) not in allowed_chat_ids:
        send_text_message(token, chat_id, f"Chat {chat_id} is not allowed to run bridge commands.")
        return

    text = normalize_bridge_command_text(extract_text(update))
    if not text.startswith("/"):
        return

    if text == "/help":
        send_text_message(token, chat_id, command_help_text())
        return

    try:
        argv = build_history_command(text)
    except ValueError as exc:
        send_text_message(token, chat_id, f"{exc}\n\n{command_help_text()}")
        return

    if not argv:
        send_text_message(token, chat_id, command_help_text())
        return

    script_path = Path(argv[1])
    if not script_path.exists():
        send_text_message(token, chat_id, f"Bridge command target not found: {script_path.name}")
        return

    try:
        completed = subprocess.run(
            argv,
            cwd=str(BASE_DIR),
            capture_output=True,
            env=build_history_client_subprocess_env(secret_env or {}),
            text=True,
            timeout=3600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        send_text_message(token, chat_id, "Command timed out after 3600 seconds.")
        return

    safe_response, json_output = build_safe_command_response_any(" ".join(argv[2:]), completed)
    is_digest_command = text.split(maxsplit=1)[0] == "/digest"
    if not (is_digest_command and completed.returncode == 0):
        send_text_chunks(token, chat_id, safe_response)
    if completed.returncode == 0:
        outputs: list[str] = []
        if isinstance(json_output, dict) and json_output.get("output_file"):
            outputs.append(str(json_output["output_file"]))
        elif isinstance(json_output, list):
            outputs.extend(str(item["output_file"]) for item in json_output if isinstance(item, dict) and item.get("output_file"))
        for output_file in outputs:
            output_path = EXPORT_DIR / output_file
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
    normalized = normalize_bridge_command_text(text)
    command = normalized.split(maxsplit=1)[0] if normalized.startswith("/") else "<message>"
    print(f"[{date}] chat_id={chat_id} from={username} event={command} text_length={len(text)}")


def cmd_listen(args: argparse.Namespace) -> int:
    config = load_runtime_config()
    secret_env = resolve_bridge_secrets(config)
    token = require_bot_token_from_secrets(secret_env)
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
                handle_history_command(token, config, update, secret_env=secret_env)

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
