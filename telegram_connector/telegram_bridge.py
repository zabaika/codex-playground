#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import queue
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from threading import Event, Thread
import uuid
from dataclasses import asdict, dataclass
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_shared import secrets as shared_secrets
from telegram_shared.ai_usage_stats import fetch_ai_usage_summary as shared_fetch_ai_usage_summary
from telegram_shared.ai_usage_stats import format_ai_usage_summary as shared_format_ai_usage_summary
from telegram_shared.bot_api import api_call as shared_api_call
from telegram_shared.bot_api import append_jsonl_record as shared_append_jsonl_record
from telegram_shared.bot_api import call_bot_api_with_retry as shared_call_bot_api_with_retry
from telegram_shared.bot_api import extract_chat_id as shared_extract_chat_id
from telegram_shared.bot_api import extract_text as shared_extract_text
from telegram_shared.bot_api import extract_user_id as shared_extract_user_id
from telegram_shared.bot_api import extract_username as shared_extract_username
from telegram_shared.bot_api import fetch_updates as shared_fetch_updates
from telegram_shared.bot_api import load_offset as shared_load_offset
from telegram_shared.bot_api import save_offset as shared_save_offset
from telegram_shared.bot_api import split_text_chunks as shared_split_text_chunks
from telegram_shared.bridge_env import build_child_env as shared_build_child_env
from telegram_shared.bridge_env import is_user_allowed as shared_is_user_allowed
from telegram_shared.bridge_env import parse_allowed_chat_ids as shared_parse_allowed_chat_ids
from telegram_shared.bridge_env import parse_allowed_user_ids as shared_parse_allowed_user_ids
from telegram_shared.bridge_env import parse_allowed_usernames as shared_parse_allowed_usernames
from telegram_shared.bridge_env import run_worker_subprocess as shared_run_worker_subprocess
from telegram_shared.command_text import normalize_bridge_command_text as shared_normalize_bridge_command_text
from telegram_shared.config import get_config_value as shared_get_config_value
from telegram_shared.config import load_runtime_config as shared_load_runtime_config
from telegram_shared.config import parse_int_range as shared_parse_int_range
from telegram_shared.config import resolve_agent_stats_row_limit as shared_resolve_agent_stats_row_limit
from telegram_shared.config import resolve_bridge_text_chunk_size as shared_resolve_bridge_text_chunk_size
from telegram_shared.config import resolve_bridge_worker_process_timeout_seconds as shared_resolve_bridge_worker_process_timeout_seconds
from telegram_shared.errors import SecretResolutionError
from telegram_shared.errors import TelegramApiError
from telegram_shared.formatting import format_telegram_html as shared_format_telegram_html
from telegram_shared.paths import resolve_app_paths as shared_resolve_app_paths
from telegram_shared.redaction import redact_sensitive_text as shared_redact_sensitive_text


APP_PATHS = shared_resolve_app_paths(
    __file__,
    project_root_env_var="TELEGRAM_CONNECTOR_PROJECT_ROOT",
    runtime_base="app_dir",
)
APP_DIR = APP_PATHS.app_dir
PROJECT_ROOT = APP_PATHS.project_root
BASE_DIR = APP_PATHS.base_dir
CONFIG_DIR = APP_PATHS.config_dir
RUNTIME_LOCAL_FILE = APP_PATHS.runtime_local_file
DATA_DIR = APP_PATHS.data_dir
OFFSET_FILE = DATA_DIR / "offset.local.json"
INBOX_FILE = DATA_DIR / "inbox.jsonl"
HISTORY_CLIENT_FILE = APP_DIR / "telegram_history_client.py"
DIGEST_FILE = APP_DIR / "telegram_digest.py"
EXPORT_DIR = DATA_DIR / "exports"
OP_REFERENCE_PREFIX = shared_secrets.OP_REFERENCE_PREFIX
_SECRET_CACHE = shared_secrets._SECRET_CACHE
SUPPORTED_BRIDGE_COMMANDS = {"help", "agent-stats", "top-models", "ocr", "exportcsv", "ocrhistory", "backfill", "tail", "update", "digest", "infoext"}
ANALYSIS_COMMANDS = {"topics", "search", "qa"}
ANALYSIS_CONTROLS = {"status", "cancel", "resume"}
INFOEXT_LAUNCH_AGENT = Path.home() / "Library/LaunchAgents/com.infoext.monitor.plist"
AUTH_MODES = {"auto", "bot", "user"}
DIGEST_WINDOW_TOKEN_RE = re.compile(r"(today|yesterday|week|month|-?\d+d|\d{4}-\d{2}-\d{2})")
DEFAULT_EXPORT_CSV_LIMIT = 100
MIN_EXPORT_CSV_LIMIT = 1
MAX_EXPORT_CSV_LIMIT = 100000
DEFAULT_OCR_PENDING_LIMIT = 100
MIN_OCR_PENDING_LIMIT = 1
MAX_OCR_PENDING_LIMIT = 100000
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
TOP_MODELS_DEFAULT_URL = "https://shir-man.com/api/free-llm/top-models"
TOP_MODELS_DEFAULT_TIMEOUT_SECONDS = 15
TOP_MODELS_MIN_TIMEOUT_SECONDS = 3
TOP_MODELS_MAX_TIMEOUT_SECONDS = 60
TOP_MODELS_DEFAULT_LIMIT = 5
TOP_MODELS_MIN_LIMIT = 1
TOP_MODELS_MAX_LIMIT = 20
TOP_MODELS_DEFAULT_CACHE_TTL_SECONDS = 300
TOP_MODELS_MIN_CACHE_TTL_SECONDS = 0
TOP_MODELS_MAX_CACHE_TTL_SECONDS = 3600
TOP_MODELS_DEFAULT_RETRY_ATTEMPTS = 3
TOP_MODELS_MIN_RETRY_ATTEMPTS = 1
TOP_MODELS_MAX_RETRY_ATTEMPTS = 5
SEND_MESSAGE_DEFAULT_RETRY_ATTEMPTS = 3
SEND_MESSAGE_MIN_RETRY_ATTEMPTS = 1
SEND_MESSAGE_MAX_RETRY_ATTEMPTS = 5
SEND_MESSAGE_DEFAULT_RETRY_BACKOFF_SECONDS = 2
SEND_MESSAGE_MIN_RETRY_BACKOFF_SECONDS = 0
SEND_MESSAGE_MAX_RETRY_BACKOFF_SECONDS = 30
TOP_MODELS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
_TOP_MODELS_CACHE: dict[str, Any] = {
    "payload": None,
    "fetched_at_monotonic": None,
}


def load_runtime_config() -> dict[str, Any]:
    return shared_load_runtime_config(RUNTIME_LOCAL_FILE)


def get_config_value(config: dict[str, Any], section: str, key: str) -> str:
    return shared_get_config_value(config, section, key)


def resolve_onepassword_secret(reference: str, label: str) -> str:
    try:
        return shared_secrets.resolve_onepassword_secret(reference, label)
    except SecretResolutionError as exc:
        raise SystemExit(str(exc)) from exc


def resolve_secret_value(raw_value: str, label: str) -> str:
    try:
        return shared_secrets.resolve_secret_value(raw_value, label)
    except SecretResolutionError as exc:
        raise SystemExit(str(exc)) from exc


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
    return shared_build_child_env(
        secret_env,
        safe_keys=SAFE_SUBPROCESS_ENV_KEYS,
        project_root_env_var="TELEGRAM_CONNECTOR_PROJECT_ROOT",
    )


def parse_allowed_chat_ids(config: dict[str, Any]) -> set[str]:
    return shared_parse_allowed_chat_ids(config, get_config_value=get_config_value)


def parse_allowed_user_ids(config: dict[str, Any]) -> set[str]:
    return shared_parse_allowed_user_ids(
        config,
        get_config_value=get_config_value,
        resolve_secret_value=resolve_secret_value,
    )


def parse_allowed_usernames(config: dict[str, Any]) -> set[str]:
    return shared_parse_allowed_usernames(
        config,
        get_config_value=get_config_value,
        resolve_secret_value=resolve_secret_value,
    )


def resolve_bridge_access(config: dict[str, Any]) -> dict[str, frozenset[str]]:
    return {"chats": frozenset(parse_allowed_chat_ids(config)),
            "users": frozenset(parse_allowed_user_ids(config)),
            "usernames": frozenset(parse_allowed_usernames(config))}


def resolve_sync_mode_limit(config: dict[str, Any], mode: str) -> str:
    raw = get_config_value(config, "sync", f"{mode}_limit")
    if not raw:
        raise SystemExit(f"Missing sync.{mode}_limit in runtime config.")
    return raw


def resolve_text_chunk_size(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    return shared_resolve_bridge_text_chunk_size(runtime_config)


def resolve_agent_stats_row_limit(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    return shared_resolve_agent_stats_row_limit(runtime_config)


def resolve_worker_process_timeout_seconds(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    return shared_resolve_bridge_worker_process_timeout_seconds(runtime_config)


def resolve_top_models_api_url(config: dict[str, Any] | None = None) -> str:
    runtime_config = config if config is not None else load_runtime_config()
    url = get_config_value(runtime_config, "bridge", "top_models_api_url") or TOP_MODELS_DEFAULT_URL
    if parse.urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("top_models_api_url must use http or https.")
    return url


def resolve_top_models_timeout_seconds(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "top_models_timeout_seconds")
    return shared_parse_int_range(
        raw,
        default=TOP_MODELS_DEFAULT_TIMEOUT_SECONDS,
        min_value=TOP_MODELS_MIN_TIMEOUT_SECONDS,
        max_value=TOP_MODELS_MAX_TIMEOUT_SECONDS,
    )


def resolve_top_models_default_limit(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "top_models_default_limit")
    return shared_parse_int_range(
        raw,
        default=TOP_MODELS_DEFAULT_LIMIT,
        min_value=TOP_MODELS_MIN_LIMIT,
        max_value=TOP_MODELS_MAX_LIMIT,
    )


def resolve_top_models_cache_ttl_seconds(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "top_models_cache_ttl_seconds")
    return shared_parse_int_range(
        raw,
        default=TOP_MODELS_DEFAULT_CACHE_TTL_SECONDS,
        min_value=TOP_MODELS_MIN_CACHE_TTL_SECONDS,
        max_value=TOP_MODELS_MAX_CACHE_TTL_SECONDS,
    )


def resolve_top_models_retry_attempts(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "top_models_retry_attempts")
    return shared_parse_int_range(
        raw,
        default=TOP_MODELS_DEFAULT_RETRY_ATTEMPTS,
        min_value=TOP_MODELS_MIN_RETRY_ATTEMPTS,
        max_value=TOP_MODELS_MAX_RETRY_ATTEMPTS,
    )


def resolve_export_csv_default_limit(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "export", "default_limit")
    return shared_parse_int_range(
        raw,
        default=DEFAULT_EXPORT_CSV_LIMIT,
        min_value=MIN_EXPORT_CSV_LIMIT,
        max_value=MAX_EXPORT_CSV_LIMIT,
    )


def resolve_ocr_pending_default_limit(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "ocr", "pending_default_limit")
    return shared_parse_int_range(
        raw,
        default=DEFAULT_OCR_PENDING_LIMIT,
        min_value=MIN_OCR_PENDING_LIMIT,
        max_value=MAX_OCR_PENDING_LIMIT,
    )


def resolve_send_message_retry_attempts(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "send_message_retry_attempts")
    return shared_parse_int_range(
        raw,
        default=SEND_MESSAGE_DEFAULT_RETRY_ATTEMPTS,
        min_value=SEND_MESSAGE_MIN_RETRY_ATTEMPTS,
        max_value=SEND_MESSAGE_MAX_RETRY_ATTEMPTS,
    )


def resolve_send_message_retry_backoff_seconds(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "send_message_retry_backoff_seconds")
    return shared_parse_int_range(
        raw,
        default=SEND_MESSAGE_DEFAULT_RETRY_BACKOFF_SECONDS,
        min_value=SEND_MESSAGE_MIN_RETRY_BACKOFF_SECONDS,
        max_value=SEND_MESSAGE_MAX_RETRY_BACKOFF_SECONDS,
    )


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
    return shared_normalize_bridge_command_text(text, supported_commands=SUPPORTED_BRIDGE_COMMANDS)


def bridge_command_name(text: str) -> str:
    """Classify only a known first token; never parse or log its arguments."""
    first = text.split(maxsplit=1)[0] if text.strip() else ""
    name = first.removeprefix("/").split("@", 1)[0].lower()
    return name if name in SUPPORTED_BRIDGE_COMMANDS | ANALYSIS_COMMANDS | ANALYSIS_CONTROLS else ""


def parse_analysis_control(text: str) -> tuple[str, str]:
    parts = shlex.split(text)
    command = bridge_command_name(parts[0]) if parts else ""
    if len(parts) != 2 or command not in ANALYSIS_CONTROLS or not re.fullmatch(r"[a-f0-9]{32}", parts[1]):
        raise ValueError("Invalid analysis control")
    return command, parts[1]


def format_analysis_status(view: dict[str, Any]) -> str:
    lines = [f"Задача: {view['job_id']}", f"Статус: {view['status']}",
             f"Этап: {view['phase']}", f"Доставка: {view['delivery']}",
             f"LLM-вызовы: {view['llm_calls']}; токены: {view['used_tokens']}; резерв: {view['reserved_tokens']}"]
    if view["cancel_requested"] and view["status"] == "running":
        lines.append("Отмена запрошена; остановка worker ещё не подтверждена.")
    if view.get("error"):
        lines.append(view["error"])
    return "\n".join(lines)


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


@dataclass(frozen=True)
class AnalysisRequest:
    command: str
    channels: tuple[str, ...]
    since: datetime
    until: datetime
    accepted_at: datetime
    cutoff_at: datetime
    query: str | None
    detail: str
    limit: int
    sources: int
    mode: str | None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["channels"] = list(self.channels)
        for field in ("since", "until", "accepted_at", "cutoff_at"):
            payload[field] = payload[field].isoformat()
        return payload


def _parse_analysis_options(parts: list[str], command: str) -> tuple[dict[str, str], list[str]]:
    options: dict[str, str] = {}
    positional: list[str] = []
    allowed = {"since", "until", "detail", "limit", "sources"}
    if command == "search":
        allowed.add("mode")
    index = 1
    while index < len(parts):
        part = parts[index]
        key, separator, value = part.partition("=")
        if part.startswith("--"):
            key = key[2:].lower()
            if key not in allowed:
                raise ValueError("Unsupported analysis option")
            if not separator:
                index += 1
                if index >= len(parts) or parts[index].startswith("--"):
                    raise ValueError("Missing analysis option value")
                value = parts[index]
        elif separator and key.lower() in {"since", "until"}:
            key = key.lower()
        else:
            positional.append(part)
            index += 1
            continue
        if key in options or not value.strip():
            raise ValueError("Repeated or empty analysis option")
        options[key] = value
        index += 1
    return options, positional


def parse_analysis_request(text: str | list[str], *, policy: dict[str, Any], now: datetime) -> AnalysisRequest:
    """Parse raw input once; policy and admission clock are supplied by the caller."""
    parts = shlex.split(text) if isinstance(text, str) else list(text)
    command = parts[0].removeprefix("/").split("@", 1)[0].lower() if parts else ""
    if command not in ANALYSIS_COMMANDS:
        raise ValueError("Unsupported analysis command")
    options, positional = _parse_analysis_options(parts, command)
    query = positional.pop() if command == "search" and positional else None
    channel_arg, remaining = consume_channel_argument(positional)
    if not channel_arg:
        raise ValueError("Channels must be explicitly specified")
    channels = tuple(token for token in re.split(r"[\s,]+", channel_arg) if token)
    analysis = policy["analysis"]
    if not channels or len(channels) > analysis["max_channels"]:
        raise ValueError("Channel count is outside the configured limit")
    if not all(is_channel_token(channel) for channel in channels):
        raise ValueError("Invalid channel reference")
    if command == "search" and (query is None or not query.strip() or len(query) > analysis["search"]["max_query_chars"]):
        raise ValueError("Query is empty or exceeds the configured limit")
    if len(remaining) > 1 or (remaining and not DIGEST_WINDOW_TOKEN_RE.fullmatch(remaining[0].lower())):
        raise ValueError("Unsupported analysis period or positional argument")
    shorthand = remaining[0] if remaining else None
    if __package__:
        from . import telegram_history_client as history_client
    else:
        import telegram_history_client as history_client
    current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    since_raw = shorthand or options.get("since", policy["digest"]["since"])
    until_raw = shorthand or options.get("until", policy["digest"]["until"])
    try:
        since = history_client.parse_filter_datetime_value(
            history_client.resolve_relative_date_token(since_raw, current.date()),
        ).astimezone(timezone.utc)
        until = history_client.parse_filter_datetime_value(
            history_client.resolve_relative_date_token(until_raw, current.date()), end_of_day=True,
        ).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid analysis period") from exc
    if since > until or since > current:
        raise ValueError("Analysis period is reversed or entirely in the future")
    output = analysis["output"]
    detail = options.get("detail", output["default_detail"]).lower()
    if detail not in output["profiles"]:
        raise ValueError("Unsupported analysis detail")
    profile = output["profiles"][detail]
    limit_raw = options.get("limit", str(profile["default_limit"]))
    sources_raw = options.get("sources", str(output["sources_per_item"]))
    if not limit_raw.isdecimal() or not sources_raw.isdecimal():
        raise ValueError("Analysis limit and sources must be positive integers")
    limit, sources = int(limit_raw), int(sources_raw)
    if not 1 <= limit <= min(output["max_items"], profile["max_items"]):
        raise ValueError("Analysis limit exceeds the configured profile")
    if not 1 <= sources <= output["sources_per_item"]:
        raise ValueError("Analysis sources exceed the configured limit")
    mode = options.get("mode", analysis["search"]["mode"]).lower() if command == "search" else None
    if mode is not None and mode not in {"fast", "full"}:
        raise ValueError("Unsupported analysis search mode")
    return AnalysisRequest(
        command=command, channels=channels, since=since, until=until,
        accepted_at=current, cutoff_at=current, query=query, detail=detail,
        limit=limit, sources=sources, mode=mode,
    )


def resolve_history_client_path(config: dict[str, Any]) -> Path:
    raw = get_config_value(config, "bridge", "history_client_path")
    return Path(raw) if raw else HISTORY_CLIENT_FILE


def append_option(argv: list[str], name: str, value: str) -> None:
    if value.startswith("-"):
        argv.append(f"{name}={value}")
    else:
        argv.extend([name, value])


def api_call(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: int = 65,
) -> Any:
    try:
        return shared_api_call(token, method, payload, timeout_seconds=timeout_seconds)
    except TelegramApiError as exc:
        raise SystemExit(str(exc)) from exc


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_offset() -> int | None:
    return shared_load_offset(OFFSET_FILE)


def save_offset(offset: int) -> None:
    shared_save_offset(OFFSET_FILE, offset)


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
    command = bridge_command_name(text)
    if not command:
        return redacted
    if command in ANALYSIS_COMMANDS | ANALYSIS_CONTROLS:
        redacted["command"] = f"/{command}"
        return redacted
    try:
        normalized = normalize_bridge_command_text(text)
    except ValueError:
        normalized = f"/{command}"
    if normalized.startswith("/"):
        redacted["command"] = normalized.split(maxsplit=1)[0]
        redacted["command_text"] = "/infoext" if redacted["command"] == "/infoext" else sanitize_text_for_storage(normalized)
    return redacted


def append_inbox(update: dict[str, Any]) -> None:
    shared_append_jsonl_record(INBOX_FILE, redact_update_for_storage(update))


def extract_text(update: dict[str, Any]) -> str:
    return shared_extract_text(update)


def extract_chat_id(update: dict[str, Any]) -> int | None:
    return shared_extract_chat_id(update)


def extract_user_id(update: dict[str, Any]) -> int | None:
    return shared_extract_user_id(update)


def extract_username(update: dict[str, Any]) -> str:
    return shared_extract_username(update)


def is_user_allowed(update: dict[str, Any], *, allowed_user_ids: set[str], allowed_usernames: set[str]) -> bool:
    return shared_is_user_allowed(
        user_id=extract_user_id(update),
        username=extract_username(update),
        allowed_user_ids=allowed_user_ids,
        allowed_usernames=allowed_usernames,
    )


def extract_date(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    unix_ts = message.get("date")
    if not unix_ts:
        return "-"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def send_analysis_text_once(token, chat_id, text, parse_mode, *, timeout_seconds):
    """One tracked physical send; the analytics owner persists intent and receipt."""
    payload = {"chat_id": str(chat_id), "text": text}
    if parse_mode:
        payload.update(parse_mode=parse_mode, disable_web_page_preview=True)
    return shared_api_call(token, "sendMessage", payload, timeout_seconds=timeout_seconds)


def send_text_message(
    token: str,
    chat_id: str | int,
    text: str,
    parse_mode: str | None = None,
    *,
    retry_attempts: int | None = None,
    retry_backoff_seconds: int | None = None,
) -> None:
    payload = {
        "chat_id": str(chat_id),
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if parse_mode == "HTML":
        payload["disable_web_page_preview"] = True
    attempts = resolve_send_message_retry_attempts() if retry_attempts is None else retry_attempts
    backoff_seconds = (
        resolve_send_message_retry_backoff_seconds() if retry_backoff_seconds is None else retry_backoff_seconds
    )
    shared_call_bot_api_with_retry(
        lambda: api_call(token, "sendMessage", payload),
        method="sendMessage",
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    )


def send_text_chunks(
    token: str,
    chat_id: str | int,
    text: str,
    chunk_size: int | None = None,
    parse_mode: str | None = None,
) -> None:
    active_chunk_size = resolve_text_chunk_size() if chunk_size is None else chunk_size
    for chunk in shared_split_text_chunks(text, active_chunk_size):
        send_text_message(token, chat_id, chunk, parse_mode=parse_mode)


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
        # Fixed Telegram Bot API HTTPS endpoint built in code.
        with request.urlopen(req, timeout=65) as resp:  # nosec B310
            response = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"Telegram API HTTP {exc.code} while calling sendDocument.") from exc
    except error.URLError as exc:
        raise SystemExit("Telegram API request failed while calling sendDocument.") from exc
    if not response.get("ok"):
        description = response.get("description") or "request failed"
        raise SystemExit(f"Telegram API error while calling sendDocument: {description}")


def redact_sensitive_text(text: str) -> str:
    return shared_redact_sensitive_text(text)


def command_error_summary(diagnostic: str) -> str:
    if re.search(r"PermissionError:|\[Errno (?:1|13)\]", diagnostic):
        reason = (
            "Команда не выполнена: нет доступа к файлу или папке. "
            "Проверьте разрешения macOS для Python."
        )
    else:
        reason = "Команда не выполнена."
    return f"{reason} Подробности: data/launchd/bridge.stderr.log."


def build_safe_command_response(command_text: str, completed: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any] | None]:
    status = "ok" if completed.returncode == 0 else f"failed ({completed.returncode})"
    lines = [f"Command: {command_text}", f"Status: {status}"]
    output = (completed.stdout or "").strip()
    error_output = redact_sensitive_text((completed.stderr or "").strip())
    if completed.returncode != 0 or "Traceback (" in output:
        lines.append(command_error_summary(error_output or output))
        return "\n".join(lines), None
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
    elif output and "Traceback (" not in output:
        lines.append(redact_sensitive_text(output))
    return "\n".join(lines), json_output if isinstance(json_output, dict) else None


def build_safe_command_response_any(command_text: str, completed: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any] | list[dict[str, Any]] | None]:
    if completed.returncode != 0 or "Traceback (" in (completed.stdout or ""):
        return build_safe_command_response(command_text, completed)
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
        "/infoext [NIE] [DD/MM/YYYY] [YYYY]\n"
        "  check InfoExt now and send the status of the configured expediente\n"
        "/agent-stats\n"
        "  show local Digest AI usage and prompt-cache summary for digest runs\n"
        "/top-models [limit] [debug]\n"
        "  show the current top free models from the configured external ranking API\n"
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
        "/topics <channels> [since=...] [until=...] [options]\n"
        "  key events, changes and topics from saved channel history\n"
        "/search <channels> \"query\" [since=...] [until=...] [options]\n"
        "  find and combine source-backed answers to a question\n"
        "/qa <channels> [since=...] [until=...] [options]\n"
        "  practical questions and answers, including non-reply discussions\n"
        "/status <job_id>\n"
        "  show analytic job progress and coverage\n"
        "/cancel <job_id>\n"
        "  cancel an analytic job\n"
        "/resume <job_id>\n"
        "  resume an eligible analytic job with its saved budget\n"
        "/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]\n"
        "  export saved history to CSV\n"
        "/ocr [limit] [channel] [since=YYYY-MM-DD] [until=YYYY-MM-DD]\n"
        "  OCR only for already-downloaded pending images\n"
        "\nNotes:\n"
        "- channel may be omitted to use default channels from config, except /topics, /search and /qa\n"
        "- channel may be a comma-separated list\n"
        "- analytics options: --detail short|normal|full (default normal), --limit N, --sources N\n"
        "- --detail full saves Markdown in scratch; the bot returns only its relative path\n"
        "- /search --mode fast|full (default fast): search candidates or scan the available window\n"
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
        "/topics @vcnews since=week --detail short\n"
        "/search @vcnews since=month \"What changed?\" --mode fast\n"
        "/qa @vcnews since=week --detail full\n"
        "/agent-stats\n"
        "/top-models\n"
        "/top-models 10\n"
        "/top-models 3 debug\n"
        "/exportcsv @vcnews since=2026-03-15\n"
        "/ocr @vcnews since=2026-03-15 until=2026-03-16"
    )


def parse_top_models_request(text: str, *, default_limit: int) -> tuple[int, bool]:
    parts = shlex.split(text)
    if not parts or parts[0].lower() != "/top-models":
        raise ValueError("Unsupported command")
    limit = default_limit
    debug = False
    for part in parts[1:]:
        lowered = part.lower()
        if part.isdigit():
            limit = max(TOP_MODELS_MIN_LIMIT, min(TOP_MODELS_MAX_LIMIT, int(part)))
            continue
        if lowered == "debug":
            debug = True
            continue
        raise ValueError("Usage: /top-models [limit] [debug]")
    return limit, debug


def humanize_token_count(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "?"
    if number >= 1000:
        rounded = number / 1000
        if rounded.is_integer():
            return f"{int(rounded)}k"
        return f"{rounded:.1f}k"
    return str(number)


def format_top_models_updated_at(value: str) -> str:
    raw = value.strip()
    if not raw:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_top_models_message(payload: dict[str, Any], *, limit: int, debug: bool = False) -> str:
    models = payload.get("models")
    if not isinstance(models, list):
        raise SystemExit("Top-models API returned an unexpected payload.")

    lines = [
        "Top free LLM models",
        f"Обновлено: {format_top_models_updated_at(str(payload.get('updatedAt', '')))}",
        f"Источник: {str(payload.get('source', '')).strip() or 'unknown'}",
        f"Версия ранжирования: {str(payload.get('rankingVersion', '')).strip() or 'unknown'}",
    ]
    fallback = payload.get("fallback") or {}
    fallback_id = str(fallback.get("id", "")).strip()
    if fallback_id:
        lines.append(f"Fallback: {fallback_id}")
    lines.append("")

    selected_models = models[:limit]
    if not selected_models:
        lines.append("Список моделей пуст.")
        return "\n".join(lines)

    for item in selected_models:
        if not isinstance(item, dict):
            continue
        rank = item.get("rank", "?")
        name = str(item.get("name", "")).strip() or str(item.get("id", "")).strip() or "unknown model"
        score = item.get("score", "?")
        context_length = humanize_token_count(item.get("contextLength"))
        max_completion_tokens = humanize_token_count(item.get("maxCompletionTokens"))
        latency_ms = item.get("latencyMs")
        latency_text = f"{latency_ms} ms" if latency_ms not in {None, ""} else "n/a"
        health_status = str(item.get("healthStatus", "")).strip() or "unknown"
        capabilities: list[str] = []
        if item.get("supportsTools"):
            capabilities.append("tools")
        if item.get("supportsStructuredOutputs"):
            capabilities.append("structured")
        if item.get("supportsReasoning"):
            capabilities.append("reasoning")
        capability_text = ", ".join(capabilities) if capabilities else "basic"
        reason = str(item.get("reason", "")).strip()
        lines.append(f"{rank}. {name}")
        lines.append(
            f"score {score} | ctx {context_length} | out {max_completion_tokens} | {capability_text} | {latency_text} | {health_status}"
        )
        if reason:
            lines.append(reason)
        if debug:
            debug_fields = [
                ("id", item.get("id")),
                ("metadataScore", item.get("metadataScore")),
                ("healthScore", item.get("healthScore")),
                ("latencyScore", item.get("latencyScore")),
                ("liteEvalScore", item.get("liteEvalScore")),
                ("supportsToolChoice", item.get("supportsToolChoice")),
                ("supportsResponseFormat", item.get("supportsResponseFormat")),
                ("supportsIncludeReasoning", item.get("supportsIncludeReasoning")),
                ("supportsSeed", item.get("supportsSeed")),
                ("supportsStop", item.get("supportsStop")),
                ("evalSuite", item.get("evalSuite")),
                ("healthStatus", item.get("healthStatus")),
                ("latencyMs", item.get("latencyMs")),
                ("instabilityPenalty", item.get("instabilityPenalty")),
            ]
            for key, value in debug_fields:
                lines.append(f"{key}: {value}")
            eval_summary = item.get("evalSummary")
            if isinstance(eval_summary, dict):
                lines.append(
                    "evalSummary: "
                    + json.dumps(eval_summary, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
                )
        lines.append("")

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def format_top_models_html_message(message: str) -> str:
    lines: list[str] = []
    for index, line in enumerate(message.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped:
            lines.append(f"**{stripped}**")
            continue
        if re.match(r"^\d+\.\s+\S", stripped):
            lines.append(f"**{stripped}**")
            continue
        lines.append(line)
    return shared_format_telegram_html("\n".join(lines))


def fetch_top_models_payload(
    *,
    url: str,
    timeout_seconds: int,
    cache_ttl_seconds: int,
    retry_attempts: int,
) -> dict[str, Any]:
    cached_payload = _TOP_MODELS_CACHE.get("payload")
    fetched_at_monotonic = _TOP_MODELS_CACHE.get("fetched_at_monotonic")
    now_monotonic = time.monotonic()
    if (
        cache_ttl_seconds > 0
        and isinstance(cached_payload, dict)
        and isinstance(fetched_at_monotonic, (int, float))
        and now_monotonic - float(fetched_at_monotonic) <= cache_ttl_seconds
    ):
        return cached_payload

    request_obj = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": TOP_MODELS_USER_AGENT,
        },
        method="GET",
    )
    last_error: BaseException | None = None
    for attempt in range(1, retry_attempts + 1):
        try:
            # URL scheme is validated in resolve_top_models_api_url.
            with request.urlopen(request_obj, timeout=timeout_seconds) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise SystemExit("Top-models API returned an unexpected payload.")
            _TOP_MODELS_CACHE["payload"] = payload
            _TOP_MODELS_CACHE["fetched_at_monotonic"] = now_monotonic
            return payload
        except error.HTTPError as exc:
            raise SystemExit(f"Top-models API HTTP {exc.code}.") from exc
        except (TimeoutError, error.URLError, OSError) as exc:
            last_error = exc
            if attempt >= retry_attempts:
                break
            time.sleep(attempt)
    raise SystemExit(f"Top-models API request failed: {str(last_error) or last_error.__class__.__name__}")


def resolve_history_db_path(config: dict[str, Any]) -> Path:
    raw = get_config_value(config, "paths", "history_db")
    return Path(raw) if raw else DATA_DIR / "telegram_history.sqlite3"


def fetch_digest_usage_summary(config: dict[str, Any], *, row_limit: int, recent_rounds_limit: int = 3) -> dict[str, Any] | None:
    return shared_fetch_ai_usage_summary(
        resolve_history_db_path(config),
        feature="digest",
        row_limit=row_limit,
        recent_rows_limit=recent_rounds_limit,
    )


def format_digest_usage_summary(summary: dict[str, Any]) -> str:
    return shared_format_ai_usage_summary(
        summary,
        title="Digest AI usage",
        hide_unavailable=True,
    )


def append_period_option(argv: list[str], part: str) -> bool:
    lowered = part.lower()
    if lowered.startswith("since="):
        append_option(argv, "--since", part.split("=", 1)[1])
        return True
    if lowered.startswith("until="):
        append_option(argv, "--until", part.split("=", 1)[1])
        return True
    return False


def consume_leading_limit(parts: list[str], default: str) -> tuple[str, list[str]]:
    if parts and parts[0].isdigit():
        return parts[0], parts[1:]
    return default, parts


def build_digest_command(parts: list[str], digest_base: list[str]) -> list[str]:
    argv = digest_base + ["run"]
    auth_mode = "user"
    single_window_token = None
    extra_parts = parts[1:]
    channel_arg, extra_parts = consume_channel_argument(extra_parts)
    if channel_arg:
        argv += ["--channel", channel_arg]
    for part in extra_parts:
        lowered = part.lower()
        if append_period_option(argv, part):
            continue
        if lowered in AUTH_MODES:
            auth_mode = lowered
        elif DIGEST_WINDOW_TOKEN_RE.fullmatch(lowered):
            if single_window_token is not None:
                raise ValueError(f"Unsupported digest argument: {part}")
            single_window_token = part
        else:
            raise ValueError(f"Unsupported digest argument: {part}")
    if single_window_token is not None:
        append_option(argv, "--since", single_window_token)
        append_option(argv, "--until", single_window_token)
    return argv + ["--auth-mode", auth_mode]


def build_ocr_pending_command(parts: list[str], base: list[str], config: dict[str, Any]) -> list[str]:
    argv = base + ["ocr-pending"]
    extra_parts = parts[1:]
    channel_arg, extra_parts = consume_channel_argument(extra_parts)
    if channel_arg:
        argv += ["--channel", channel_arg]
    limit, extra_parts = consume_leading_limit(extra_parts, str(resolve_ocr_pending_default_limit(config)))
    argv += ["--limit", limit]
    for part in extra_parts:
        if append_period_option(argv, part):
            continue
        raise ValueError(f"Unsupported ocr argument: {part}")
    return argv


def build_export_csv_command(parts: list[str], base: list[str], config: dict[str, Any]) -> list[str]:
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
        elif append_period_option(argv, part):
            has_filter = True
        elif lowered in AUTH_MODES:
            auth_mode = lowered
        else:
            raise ValueError(f"Unsupported exportcsv argument: {part}")
    if not has_filter:
        argv += ["--limit", str(resolve_export_csv_default_limit(config))]
    return argv + ["--auth-mode", auth_mode]


def build_ocr_history_command(parts: list[str], base: list[str], config: dict[str, Any]) -> list[str]:
    argv = base + ["sync", "--mode", "tail"]
    extra_parts = parts[1:]
    channel_arg, extra_parts = consume_channel_argument(extra_parts)
    if channel_arg:
        argv += ["--channel", channel_arg]
    limit, extra_parts = consume_leading_limit(extra_parts, resolve_sync_mode_limit(config, "tail"))
    argv += ["--limit", limit, "--download-media", "--ocr"]
    auth_mode = "user"
    for part in extra_parts:
        lowered = part.lower()
        if append_period_option(argv, part):
            continue
        if lowered in AUTH_MODES:
            auth_mode = lowered
            continue
        raise ValueError(f"Unsupported ocrhistory argument: {part}")
    return argv + ["--auth-mode", auth_mode]


def build_sync_command(command: str, parts: list[str], base: list[str], config: dict[str, Any]) -> list[str]:
    subcommand = "backfill" if command == "/backfill" else "update" if command == "/update" else "tail"
    argv = base + ["sync", "--mode", subcommand]
    extra_parts = parts[1:]
    channel_arg, extra_parts = consume_channel_argument(extra_parts)
    if channel_arg:
        argv += ["--channel", channel_arg]
    limit, extra_parts = consume_leading_limit(extra_parts, resolve_sync_mode_limit(config, subcommand))
    argv += ["--limit", limit]
    auth_mode = "user"
    for part in extra_parts:
        lowered = part.lower()
        if append_period_option(argv, part):
            continue
        if lowered == "media":
            argv.append("--download-media")
        elif lowered == "ocr":
            if "--download-media" not in argv:
                argv.append("--download-media")
            argv.append("--ocr")
        elif lowered == "read":
            argv.append("--mark-read")
        elif lowered in AUTH_MODES:
            auth_mode = lowered
        else:
            raise ValueError(f"Unsupported {command.lstrip('/')} argument: {part}")
    return argv + ["--auth-mode", auth_mode]


def parse_infoext_query(parts: list[str]) -> dict[str, str]:
    if len(parts) > 4 or any(not value.strip() or value.startswith("-") for value in parts[1:]):
        raise ValueError("Usage: /infoext [NIE] [DD/MM/YYYY] [YYYY]")
    if len(parts) == 4 and (not re.fullmatch(r"[0-9]{4}", parts[3]) or int(parts[3]) == 0):
        raise ValueError("Год рождения должен содержать ровно 4 цифры: YYYY.")
    return dict(zip(("nie", "fecha_presentacion", "ano_nacimiento"), parts[1:]))


def resolve_infoext_paths() -> tuple[Path, Path]:
    """Read the canonical installer artifact instead of duplicating its paths."""
    try:
        with INFOEXT_LAUNCH_AGENT.open("rb") as handle:
            payload = plistlib.load(handle)
        if not isinstance(payload, dict) or payload.get("Label") != "com.infoext.monitor":
            raise ValueError
        runtime = payload.get("WorkingDirectory")
        environment = payload.get("EnvironmentVariables")
        project = environment.get("INFOEXT_PROJECT_ROOT") if isinstance(environment, dict) else None
        if not isinstance(runtime, str) or not isinstance(project, str):
            raise ValueError
        runtime_root, project_root = Path(runtime), Path(project)
        if not runtime_root.is_absolute() or not project_root.is_absolute():
            raise ValueError
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException, ExpatError) as exc:
        raise ValueError("InfoExt LaunchAgent отсутствует или некорректен. Выполните install.sh проекта InfoExt.") from exc
    if not all(path.is_file() for path in (
        runtime_root / "main.py", runtime_root / ".venv/bin/python",
        project_root / "config/runtime.local.toml",
    )) or not os.access(runtime_root / ".venv/bin/python", os.X_OK):
        raise ValueError("InfoExt runtime или конфиг недоступен. Повторите установку через install.sh.")
    return runtime_root, project_root


def build_history_command(text: str, *, infoext_paths: tuple[Path, Path] | None = None) -> list[str] | None:
    parts = shlex.split(text)
    if not parts:
        return None

    config = load_runtime_config()
    command = parts[0].lower()
    base = [sys.executable, str(HISTORY_CLIENT_FILE)]
    digest_base = [sys.executable, str(DIGEST_FILE)]

    if command == "/help":
        return []
    if command == "/infoext":
        query = parse_infoext_query(parts)
        runtime_root, _ = infoext_paths if infoext_paths is not None else resolve_infoext_paths()
        argv = [str(runtime_root / ".venv/bin/python"), str(runtime_root / "main.py"), "--check-now", "--notify"]
        return argv + (["--query-stdin"] if query else [])
    if command == "/digest":
        return build_digest_command(parts, digest_base)
    if command == "/ocr":
        return build_ocr_pending_command(parts, base, config)
    if command == "/exportcsv":
        return build_export_csv_command(parts, base, config)
    if command == "/ocrhistory":
        return build_ocr_history_command(parts, base, config)
    if command in {"/backfill", "/tail", "/update"}:
        return build_sync_command(command, parts, base, config)
    raise ValueError(f"Unsupported command: {parts[0]}")


def handle_history_command(token: str, config: dict[str, Any], update: dict[str, Any], secret_env: dict[str, str] | None = None,
                           *, access: dict[str, frozenset[str]] | None = None) -> None:
    chat_id = extract_chat_id(update)
    if chat_id is None:
        return

    active_access = access if access is not None else resolve_bridge_access(config)
    allowed_chat_ids = active_access["chats"]
    if not allowed_chat_ids:
        send_text_message(token, chat_id, "Bridge chat allowlist is not configured; command execution is disabled.")
        return
    if allowed_chat_ids and str(chat_id) not in allowed_chat_ids:
        send_text_message(token, chat_id, f"Chat {chat_id} is not allowed to run bridge commands.")
        return
    if not is_user_allowed(
        update,
        allowed_user_ids=active_access["users"],
        allowed_usernames=active_access["usernames"],
    ):
        send_text_message(token, chat_id, "This Telegram user is not allowed to run bot commands.")
        return

    text = normalize_bridge_command_text(extract_text(update))
    if not text.startswith("/"):
        return

    if text == "/help":
        send_text_message(token, chat_id, command_help_text())
        return
    if text == "/agent-stats":
        summary = fetch_digest_usage_summary(config, row_limit=resolve_agent_stats_row_limit(config))
        if summary is None:
            send_text_message(token, chat_id, "Digest AI usage is not available yet. Run at least one /digest request first.")
            return
        send_text_chunks(
            token,
            chat_id,
            shared_format_telegram_html(format_digest_usage_summary(summary)),
            chunk_size=resolve_text_chunk_size(config),
            parse_mode="HTML",
        )
        return
    if text.startswith("/top-models"):
        try:
            limit, debug = parse_top_models_request(text, default_limit=resolve_top_models_default_limit(config))
        except ValueError as exc:
            send_text_message(token, chat_id, str(exc))
            return
        try:
            payload = fetch_top_models_payload(
                url=resolve_top_models_api_url(config),
                timeout_seconds=resolve_top_models_timeout_seconds(config),
                cache_ttl_seconds=resolve_top_models_cache_ttl_seconds(config),
                retry_attempts=resolve_top_models_retry_attempts(config),
            )
            formatted = format_top_models_message(payload, limit=limit, debug=debug)
        except SystemExit as exc:
            send_text_message(token, chat_id, redact_sensitive_text(str(exc)))
            return
        send_text_chunks(
            token,
            chat_id,
            format_top_models_html_message(formatted),
            chunk_size=resolve_text_chunk_size(config),
            parse_mode="HTML",
        )
        return

    is_infoext_command = text.split(maxsplit=1)[0] == "/infoext"
    try:
        infoext_paths = resolve_infoext_paths() if is_infoext_command else None
        argv = build_history_command(text, infoext_paths=infoext_paths) if is_infoext_command else build_history_command(text)
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

    worker_timeout_seconds = resolve_worker_process_timeout_seconds(config)
    child_env = build_history_client_subprocess_env({} if is_infoext_command else secret_env or {})
    run_func = subprocess.run
    if is_infoext_command:
        child_env["INFOEXT_PROJECT_ROOT"] = str(infoext_paths[1])
        query = parse_infoext_query(shlex.split(text))
        if query:
            # Personal data travels through stdin, never process arguments or logs.
            run_func = partial(subprocess.run, input=json.dumps(query))
    try:
        completed = shared_run_worker_subprocess(
            argv,
            cwd=script_path.parent if is_infoext_command else BASE_DIR,
            env=child_env,
            # InfoExt owns its hard TTL and process-group cleanup. Killing its
            # supervisor with a second timeout could orphan the browser worker.
            timeout_seconds=None if is_infoext_command else worker_timeout_seconds,
            run_func=run_func,
        )
    except subprocess.TimeoutExpired:
        send_text_message(token, chat_id, f"Command timed out after {worker_timeout_seconds} seconds.")
        return
    except OSError:
        send_text_message(token, chat_id, "Command could not start; check the configured runtime and interpreter.")
        return

    if is_infoext_command:
        # Return fixed diagnostics only; monitor notifications own status formatting.
        if completed.returncode != 0:
            response = "InfoExt: проверка не удалась. Подробности в logs/infoext.log."
        elif "InfoExt check skipped:" in (completed.stdout or ""):
            response = "InfoExt: проверка пропущена — минимальный интервал ещё не прошёл."
        elif "InfoExt check\n" in (completed.stdout or ""):
            if "Telegram notification: delivered" in (completed.stdout or "").splitlines():
                return
            response = "InfoExt: статус получен, но доставка уведомления не подтверждена. Проверьте logs/infoext.log; недоставленные уведомления остаются в очереди."
        else:
            response = "InfoExt: новый результат не получен; возможно, другая проверка уже выполняется."
        send_text_message(token, chat_id, response)
        return

    # launchd owns this stderr stream even if the worker cannot open its logs.
    diagnostics = []
    if completed.stderr:
        diagnostics.append(completed.stderr)
    if completed.stdout and (completed.returncode != 0 or "Traceback (" in completed.stdout):
        diagnostics.append(completed.stdout)
    if diagnostics:
        timestamp = datetime.now(timezone.utc).isoformat()
        print(
            f"[{timestamp}] Worker {script_path.name} exited ({completed.returncode})\n"
            + redact_sensitive_text("\n".join(diagnostics)),
            file=sys.stderr, flush=True,
        )
    safe_response, json_output = build_safe_command_response_any(" ".join(argv[2:]), completed)
    is_digest_command = text.split(maxsplit=1)[0] == "/digest"
    if not (is_digest_command and completed.returncode == 0):
        send_text_chunks(token, chat_id, safe_response, chunk_size=resolve_text_chunk_size(config))
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
    try:
        return shared_fetch_updates(token, offset, timeout, api_call_func=api_call)
    except TelegramApiError as exc:
        raise SystemExit(str(exc)) from exc


def print_update(update: dict[str, Any]) -> None:
    chat_id = extract_chat_id(update)
    username = extract_username(update)
    text = extract_text(update)
    date = extract_date(update)
    name = bridge_command_name(text)
    command = f"/{name}" if name else "<message>"
    print(f"[{date}] chat_id={chat_id} from={username} event={command} text_length={len(text)}")


def is_retryable_listen_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    message = str(exc).lower()
    if isinstance(exc, SystemExit):
        return "getupdates" in message and ("timed out" in message or "request failed" in message)
    return "timed out" in message


@contextmanager
def analysis_ownership_lock(db_path: Path, *, owner: str):
    if owner != "supervisor" and not re.fullmatch(r"[a-f0-9]{32}", owner):
        raise ValueError("Invalid analysis lock owner")
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db_path.with_name(f"{db_path.name}.analysis-{owner}.lock")
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        try:
            yield handle.fileno()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    # Keep the inode: unlinking could let contenders lock different files.


def recover_analysis_jobs(store, db_path: Path, *, now: datetime, exclude_job_ids=()) -> None:
    if __package__:
        from .analysis_store import StaleGenerationError
    else:
        from analysis_store import StaleGenerationError
    for job in store.iter_jobs("running"):
        if job["job_id"] in exclude_job_ids:
            continue
        with analysis_ownership_lock(db_path, owner=job["job_id"]) as owned:
            if owned is not None:
                try:
                    store.finish_job(job["job_id"], generation=job["generation"],
                                     status="cancelled" if job["cancel_requested"] else "interrupted", now=now)
                except StaleGenerationError:
                    # A terminal state won before this recovery acquired ownership.
                    continue


@contextmanager
def open_analysis_store(config: dict[str, Any], *, initialize: bool = True):
    from common import sqlite as common_sqlite
    if __package__:
        from . import telegram_history_client as history_client
        from .analysis_store import AnalysisStore
    else:
        import telegram_history_client as history_client
        from analysis_store import AnalysisStore
    conn = common_sqlite.connect_sqlite(resolve_history_db_path(config))
    try:
        if initialize:
            history_client.init_db(conn)
        yield AnalysisStore(conn)
    finally:
        conn.close()


class BridgeSupervisor:
    """Own queue scheduling and a serial legacy lane outside long polling."""

    def __init__(self, config, secret_env, access, *, launch=None, legacy_handler=None,
                 progress_api=None, access_provider=None):
        if __package__:
            from . import telegram_analysis as analysis
        else:
            import telegram_analysis as analysis
        from common import process as common_process
        self.analysis = analysis
        self.config, self.secret_env, self.access = config, secret_env, access
        self.db_path = resolve_history_db_path(config).resolve()
        self.token = require_bot_token_from_secrets(secret_env)
        self.namespace = f"telegram:{self.token.partition(':')[0]}"
        self.analysis_enabled = True
        try:
            self.policy = analysis.resolve_analysis_policy(config)["analysis"]
        except ValueError:
            # Invalid analytics options must not disable existing bridge commands.
            self.policy = analysis.resolve_analysis_policy({})["analysis"]
            self.analysis_enabled = False
            print("Analytics configuration is invalid; analytics scheduling disabled.", file=sys.stderr)
        self.interval = common_process.load_process_config().poll_interval_seconds
        self.launch = launch or self._launch
        self.legacy_handler = legacy_handler or handle_history_command
        self.legacy_queue = queue.Queue(maxsize=self.policy["max_queued_jobs"])
        self.stopping, self.wakeup, self.ready = Event(), Event(), Event()
        self.error = False
        self.schema_ready = False
        self.active = {}
        self.progress_api = progress_api or shared_api_call
        self.access_provider = access_provider or (lambda: resolve_bridge_access(load_runtime_config()))
        self.progress_pending = set()
        self.progress_initialized = False
        self.scheduler = Thread(target=self._run, name="connector-analysis-supervisor")
        self.legacy = Thread(target=self._run_legacy, name="connector-legacy-commands")

    def __enter__(self):
        self.scheduler.start()
        self.legacy.start()
        self.ready.wait()
        if self.error:
            self.__exit__(None, None, None)
            raise RuntimeError("Analytics supervisor could not start")
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        self.wakeup.set()
        self.scheduler.join()
        # Preserve accepted legacy command order and --once completion semantics.
        self.legacy.join()

    def wake(self):
        self.wakeup.set()

    def submit_legacy(self, update):
        try:
            self.legacy_queue.put_nowait(update)
        except queue.Full:
            return False
        return True

    def _run_legacy(self):
        while not self.stopping.is_set() or not self.legacy_queue.empty():
            try:
                update = self.legacy_queue.get(timeout=self.interval)
            except queue.Empty:
                continue
            try:
                self.legacy_handler(self.token, self.config, update, secret_env=self.secret_env, access=self.access)
            except (Exception, SystemExit):
                print("Legacy bridge command failed.", file=sys.stderr)
            finally:
                self.legacy_queue.task_done()

    def _launch(self, job):
        return subprocess.Popen(
            [sys.executable, str(APP_DIR / "telegram_analysis.py"), "--db", str(self.db_path),
             "--job-id", job["job_id"], "--generation", str(job["generation"])],
            cwd=str(BASE_DIR), env=build_history_client_subprocess_env(self.secret_env),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )

    def _run(self):
        try:
            if not self.analysis_enabled:
                return
            with analysis_ownership_lock(self.db_path, owner="supervisor") as owned:
                if owned is None:
                    return
                # This connection is created, used and closed by this thread alone.
                with open_analysis_store(self.config) as store:
                    self.schema_ready = True
                    self._tick(store)
                    self.ready.set()
                    while not self.stopping.is_set():
                        self.wakeup.wait(self.interval)
                        self.wakeup.clear()
                        if not self.stopping.is_set():
                            self._tick(store)
        except (Exception, SystemExit):
            self.error = True
            print("Analytics supervisor failed; durable jobs retained.", file=sys.stderr)
        finally:
            self.ready.set()

    def _tick(self, store):
        now = datetime.now(timezone.utc)
        self.progress_pending.update(self.active)
        for job_id, (job, process) in list(self.active.items()):
            code = process.poll()
            if code is None:
                continue
            del self.active[job_id]
            current = store.get_job(job_id)
            if current and current["status"] == "running" and current["generation"] == job["generation"]:
                with analysis_ownership_lock(self.db_path, owner=job_id) as owned:
                    if owned is not None:
                        store.finish_job(job_id, generation=job["generation"],
                                         status="cancelled" if current["cancel_requested"] else ("failed" if code else "interrupted"), now=now)
        # A newly spawned child may not have acquired its lock yet. Its live
        # Popen handle is authoritative within this supervisor's ownership.
        recover_analysis_jobs(store, self.db_path, now=now, exclude_job_ids=self.active)
        for candidate in store.iter_jobs("queued"):
            if self.stopping.is_set():
                return
            internal = (candidate["kind"] == "fts_backfill"
                        and candidate["admission_key"] == self.analysis.history_client.FTS_INDEX_ADMISSION_KEY)
            if not internal:
                if (candidate["kind"] != "analysis"
                        or candidate["request"].get("principal", {}).get("namespace") != self.namespace):
                    continue
            with analysis_ownership_lock(self.db_path, owner=candidate["job_id"]) as owned:
                if owned is None:
                    continue
                job = store.claim_job(candidate["job_id"], max_running_jobs=self.policy["max_running_jobs"], now=now)
            if job is None:
                continue
            self.progress_pending.add(job["job_id"])
            frozen = job["request"].get("settings", {})
            valid_version = (frozen.get("schema_version") == self.analysis.ANALYSIS_SCHEMA_VERSION
                             and frozen.get("algorithm_version") == self.analysis.ANALYSIS_ALGORITHM_VERSION)
            principal = job["request"].get("principal", {})
            event = {"message": {"chat": {"id": job["destination"]},
                                 "from": {"id": job["requester_id"], "username": principal.get("username") or ""}}}
            try:
                if internal:
                    self.analysis.validate_fts_backfill_job(job)
                else:
                    self.analysis.require_analysis_access(event, self.access)
                if not valid_version:
                    raise ValueError("Incompatible admitted version")
                process = self.launch(job)
            except (self.analysis.AnalysisRejection, ValueError, OSError):
                store.finish_job(job["job_id"], generation=job["generation"], status="failed", now=now)
                continue
            self.active[job["job_id"]] = (job, process)
        self.progress_pending.update(self.active)
        if not self.progress_initialized:
            for job in store.iter_progress_jobs():
                self._progress_tick(store, job, now)
            self.progress_initialized = True
        for job_id in list(self.progress_pending):
            job = store.get_job(job_id)
            self.progress_pending.discard(job_id)
            if job is not None:
                self._progress_tick(store, job, now)

    def _progress_tick(self, store, job, now):
        try:
            if self._report_progress(store, job, now):
                self.progress_pending.add(job["job_id"])
        except (Exception, SystemExit):
            # Progress is derived UX: neither an uncertain receipt nor a failed
            # UI checkpoint may kill scheduling or expose provider diagnostics.
            print("Analytics progress unavailable; analysis continues.", file=sys.stderr)

    def _report_progress(self, store, job, now):
        principal = job["request"].get("principal", {})
        if job["kind"] != "analysis" or principal.get("namespace") != self.namespace:
            return False
        previous = job["checkpoint"].get("progress")
        if previous and previous["outcome"] in {"unknown", "disabled"}:
            return False
        if previous and previous["outcome"] == "pending" and previous["message_id"] is None:
            return False  # Restart after a physical create with a missing receipt.
        event = {"message": {"chat": {"id": job["destination"]},
            "from": {"id": job["requester_id"], "username": principal.get("username") or ""}}}
        try:
            self.analysis.require_analysis_access(event, self.access_provider())
        except self.analysis.AnalysisRejection:
            return True
        view = self.analysis.analysis_status_view(job)
        signature = [view[key] for key in ("status", "phase", "cancel_requested", "delivery")]
        digest = hashlib.sha256(self.analysis.canonical_json(signature).encode("utf-8")).hexdigest()
        if previous and previous["view_digest"] == digest and previous["outcome"] == "confirmed":
            return False
        interval = job["request"]["settings"]["analysis"]["progress_min_interval_seconds"]
        if previous:
            elapsed = (now - datetime.fromisoformat(previous["updated_at"])).total_seconds()
            if 0 <= elapsed < interval:
                return True
        identity = previous["message_id"] if previous else None
        pending = {"outcome": "pending", "message_id": identity,
            "view_digest": previous["view_digest"] if previous else None, "updated_at": now.isoformat()}
        if not store.update_progress(job["job_id"], generation=job["generation"], expected=previous, value=pending):
            return True
        payload = {"chat_id": job["destination"], "text": format_analysis_status(view)}
        if identity is not None:
            payload["message_id"] = identity
        try:
            from telegram_shared.bot_api import DEFAULT_API_TIMEOUT_SECONDS
            receipt = self.progress_api(self.token, "editMessageText" if identity is not None else "sendMessage",
                payload, timeout_seconds=min(interval, DEFAULT_API_TIMEOUT_SECONDS))
            message_id = receipt.get("message_id") if isinstance(receipt, dict) else None
            chat = receipt.get("chat") if isinstance(receipt, dict) else None
            if (type(message_id) is not int or message_id <= 0 or not isinstance(chat, dict)
                    or str(chat.get("id")) != job["destination"] or (identity is not None and message_id != identity)):
                raise ValueError("Invalid progress receipt")
            outcome = {**pending, "outcome": "confirmed", "message_id": message_id, "view_digest": digest}
        except (Exception, SystemExit):
            outcome = {**pending, "outcome": "unknown" if identity is None else "disabled"}
            print("Analytics progress unavailable; analysis continues.", file=sys.stderr)
        store.update_progress(job["job_id"], generation=job["generation"], expected=pending, value=outcome)
        return False


def create_bridge_supervisor(config, secret_env, access):
    return BridgeSupervisor(config, secret_env, access)


def cmd_listen(args: argparse.Namespace) -> int:
    config = load_runtime_config()
    secret_env = resolve_bridge_secrets(config)
    token = require_bot_token_from_secrets(secret_env)
    access = resolve_bridge_access(config) if args.run_commands else None
    offset = None if args.from_scratch else load_offset()
    print("Listening for Telegram updates.")

    with ExitStack() as resources:
        store = None
        supervisor = resources.enter_context(create_bridge_supervisor(config, secret_env, access)) if args.run_commands else None
        while True:
            if supervisor is not None and supervisor.error:
                return 1
            try:
                updates = fetch_updates(token, offset, args.timeout)
            except BaseException as exc:
                if not is_retryable_listen_error(exc):
                    raise
                print(
                    f"Telegram long poll timed out or failed transiently; continuing listen loop: {str(exc) or exc.__class__.__name__}",
                    file=sys.stderr,
                )
                if args.once:
                    return 1
                time.sleep(2)
                continue
            if not updates:
                if args.once:
                    return 0
                continue

            for update in updates:
                append_inbox(update)
                print_update(update)
                name = bridge_command_name(extract_text(update))
                if args.run_commands and name in ANALYSIS_COMMANDS | ANALYSIS_CONTROLS:
                    if __package__:
                        from . import telegram_analysis as analysis
                    else:
                        import telegram_analysis as analysis
                    try:
                        try:
                            # Denied callers cannot open or initialize analytics storage.
                            analysis.require_analysis_access(update, access)
                            bot_id = token.partition(":")[0]
                            if not bot_id.isdecimal():
                                raise ValueError("Invalid bot namespace")
                            if name in ANALYSIS_CONTROLS:
                                try:
                                    control_name, job_id = parse_analysis_control(extract_text(update))
                                except ValueError as exc:
                                    raise analysis.AnalysisRejection("invalid_control") from exc
                            if store is None:
                                store = resources.enter_context(open_analysis_store(
                                    config, initialize=supervisor is None or not supervisor.schema_ready,
                                ))
                            if name in ANALYSIS_CONTROLS:
                                view = analysis.control_analysis_job(
                                    store, config, update, command=control_name, job_id=job_id, access=access,
                                    now=datetime.now(timezone.utc), config_dir=CONFIG_DIR,
                                )
                                acknowledgement = format_analysis_status(view)
                            else:
                                job, _ = analysis.admit_analysis_job(
                                    store, config, update, access=access, bot_namespace=f"telegram:{bot_id}",
                                    now=datetime.now(timezone.utc), config_dir=CONFIG_DIR,
                                )
                                acknowledgement = f"Задача аналитики принята в очередь: {job['job_id']}"
                        except analysis.AnalysisRejection as exc:
                            acknowledgement = exc.public_message
                        next_offset = update["update_id"] + 1
                        save_offset(next_offset)
                        offset = next_offset
                        if supervisor is not None:
                            supervisor.wake()
                    except (sqlite3.Error, OSError, ValueError):
                        # Fail closed: no larger offset can hide this unaccepted update.
                        # Restart/replay reuses a job already committed before an offset failure.
                        print("Analytics admission or offset persistence failed; stopping listener before acknowledgement.", file=sys.stderr)
                        return 1
                    chat_id = extract_chat_id(update)
                    if chat_id is not None:
                        try:
                            send_text_message(
                                token, chat_id, acknowledgement,
                                retry_attempts=resolve_send_message_retry_attempts(config),
                                retry_backoff_seconds=resolve_send_message_retry_backoff_seconds(config),
                            )
                        except (SystemExit, OSError, TelegramApiError):
                            print("Analytics acknowledgement failed; durable admission and offset are retained.", file=sys.stderr)
                    continue

                next_offset = update["update_id"] + 1
                save_offset(next_offset)
                offset = next_offset
                if args.echo:
                    chat_id = extract_chat_id(update)
                    if chat_id is not None:
                        send_text_message(token, chat_id, f"Echo: {extract_text(update)}")
                if args.run_commands:
                    if (supervisor is None or str(extract_chat_id(update)) not in access["chats"]
                            or not is_user_allowed(update, allowed_user_ids=access["users"], allowed_usernames=access["usernames"])):
                        handle_history_command(token, config, update, secret_env=secret_env, access=access)
                    elif not supervisor.submit_legacy(update):
                        chat_id = extract_chat_id(update)
                        if chat_id is not None:
                            send_text_message(token, chat_id, "Очередь команд заполнена. Повторите позже.")

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
