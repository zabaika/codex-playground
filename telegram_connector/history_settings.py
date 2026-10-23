"""Connector runtime configuration, paths and input date semantics."""
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_shared import secrets as shared_secrets
from telegram_shared.config import get_config_value as shared_get_config_value
from telegram_shared.config import load_runtime_config as shared_load_runtime_config
from telegram_shared.errors import SecretResolutionError


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
# Runtime state stays next to the executed module, not the source checkout.
BASE_DIR = APP_DIR
CONFIG_DIR = BASE_DIR / "config"
RUNTIME_LOCAL_FILE = CONFIG_DIR / "runtime.local.toml"
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "telegram_history.sqlite3"
MEDIA_DIR = DATA_DIR / "media"
SESSION_DIR = DATA_DIR / "sessions"
EXPORT_DIR = DATA_DIR / "exports"
SINGLE_MESSAGE_EXPORT_DIR = REPO_ROOT / "scratch" / "article-to-obsidian-kb" / "telegram"
OP_REFERENCE_PREFIX = shared_secrets.OP_REFERENCE_PREFIX
_SECRET_CACHE = shared_secrets._SECRET_CACHE
DEFAULT_EXPORT_CSV_LIMIT = 100
DEFAULT_OCR_PENDING_LIMIT = 100
DEFAULT_TESSERACT_TIMEOUT_SECONDS = 60
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 60
DEFAULT_CONNECTION_ATTEMPTS = 3
DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS = 2
DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS = 10


@dataclass
class RuntimeConfig:
    db_path: Path
    media_root: Path
    user_session_name: str
    bot_session_name: str
    api_id: str
    api_hash: str
    phone: str
    bot_token: str
    user_password: str
    tesseract_binary: str
    vision_prompt: str
    sync_batch_size: int
    default_auth_mode: str
    public_auth_mode: str
    private_auth_mode: str
    default_channels: list[str]
    export_default_limit: int = DEFAULT_EXPORT_CSV_LIMIT
    ocr_pending_default_limit: int = DEFAULT_OCR_PENDING_LIMIT
    tesseract_timeout_seconds: int = DEFAULT_TESSERACT_TIMEOUT_SECONDS
    sync_total_limit: int = 0
    sync_mode_limits: dict[str, int] = field(default_factory=dict)
    connection_timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS
    connection_attempts: int = DEFAULT_CONNECTION_ATTEMPTS
    connection_retry_backoff_seconds: int = DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS
    connection_cleanup_timeout_seconds: int = DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS


def load_runtime_config() -> dict[str, Any]:
    return shared_load_runtime_config(RUNTIME_LOCAL_FILE)


def get_config_value(config: dict[str, Any], section: str, key: str) -> str:
    return shared_get_config_value(config, section, key)


def require_int_config_value(config: dict[str, Any], section: str, key: str, *, min_value: int = 0) -> int:
    raw = get_config_value(config, section, key)
    if not raw:
        raise SystemExit(f"Missing {section}.{key} in runtime config.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer in {section}.{key}: {raw}") from exc
    return max(min_value, value)


def parse_int_config_value(
    config: dict[str, Any],
    section: str,
    key: str,
    *,
    default: int,
    min_value: int = 0,
) -> int:
    raw = get_config_value(config, section, key)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(min_value, value)


def parse_default_channel_entry(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    channel, _, _label = value.partition(",")
    return channel.strip()


def get_default_channels(config: dict[str, Any]) -> list[str]:
    section_data = config.get("channels", {})
    if not isinstance(section_data, dict):
        return []
    value = section_data.get("default_list", [])
    if isinstance(value, str):
        if "\n" not in value and ";" not in value:
            channel = parse_default_channel_entry(value)
            return [channel] if channel else []
        channels = [parse_default_channel_entry(item) for item in value.replace(";", "\n").splitlines()]
        return [channel for channel in channels if channel]
    if not isinstance(value, list):
        return []
    channels = [parse_default_channel_entry(str(item)) for item in value]
    return [channel for channel in channels if channel]


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


def resolve_runtime() -> RuntimeConfig:
    config = load_runtime_config()
    db_path = Path(get_config_value(config, "paths", "history_db") or DB_FILE)
    media_root = Path(get_config_value(config, "paths", "media_root") or MEDIA_DIR)
    user_session_name = get_config_value(config, "telethon", "user_session_name") or "telegram_history_user"
    bot_session_name = get_config_value(config, "telethon", "bot_session_name") or "telegram_history_bot"
    api_id = resolve_secret_value(
        os.environ.get("TELEGRAM_API_ID", "").strip() or get_config_value(config, "telethon", "api_id"),
        "Telegram API ID",
    )
    api_hash = resolve_secret_value(
        os.environ.get("TELEGRAM_API_HASH", "").strip() or get_config_value(config, "secrets", "api_hash"),
        "Telegram API hash",
    )
    phone = resolve_secret_value(
        os.environ.get("TELEGRAM_PHONE", "").strip() or get_config_value(config, "telethon", "phone"),
        "Telegram phone",
    )
    bot_token = resolve_secret_value(
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or get_config_value(config, "secrets", "bot_token"),
        "Telegram bot token",
    )
    user_password = resolve_secret_value(
        os.environ.get("TELEGRAM_USER_PASSWORD", "").strip() or get_config_value(config, "secrets", "user_password"),
        "Telegram user password",
    )
    tesseract_binary = get_config_value(config, "paths", "tesseract_binary") or "tesseract"
    vision_prompt = get_config_value(config, "ocr", "image_prompt") or "Extract all readable text from the image."
    tesseract_timeout_seconds = parse_int_config_value(
        config,
        "ocr",
        "tesseract_timeout_seconds",
        default=DEFAULT_TESSERACT_TIMEOUT_SECONDS,
        min_value=1,
    )
    ocr_pending_default_limit = parse_int_config_value(
        config,
        "ocr",
        "pending_default_limit",
        default=DEFAULT_OCR_PENDING_LIMIT,
        min_value=1,
    )
    sync_batch_size = require_int_config_value(config, "sync", "batch_size", min_value=0)
    sync_total_limit = require_int_config_value(config, "sync", "sync_limit", min_value=0)
    export_default_limit = require_int_config_value(config, "export", "default_limit", min_value=1)
    sync_mode_limits = {
        "backfill": require_int_config_value(config, "sync", "backfill_limit", min_value=1),
        "tail": require_int_config_value(config, "sync", "tail_limit", min_value=1),
        "update": require_int_config_value(config, "sync", "update_limit", min_value=1),
    }
    default_auth_mode = get_config_value(config, "auth", "default_mode") or "auto"
    public_auth_mode = get_config_value(config, "auth", "public_channel_mode") or "bot"
    private_auth_mode = get_config_value(config, "auth", "private_channel_mode") or "user"
    default_channels = get_default_channels(config)
    return RuntimeConfig(
        db_path=db_path,
        media_root=media_root,
        user_session_name=user_session_name,
        bot_session_name=bot_session_name,
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        bot_token=bot_token,
        user_password=user_password,
        tesseract_binary=tesseract_binary,
        tesseract_timeout_seconds=tesseract_timeout_seconds,
        vision_prompt=vision_prompt,
        sync_batch_size=sync_batch_size,
        default_auth_mode=default_auth_mode,
        public_auth_mode=public_auth_mode,
        private_auth_mode=private_auth_mode,
        default_channels=default_channels,
        export_default_limit=export_default_limit,
        ocr_pending_default_limit=ocr_pending_default_limit,
        sync_total_limit=sync_total_limit,
        sync_mode_limits=sync_mode_limits,
        connection_timeout_seconds=parse_int_config_value(
            config, "telethon", "connection_timeout_seconds", default=DEFAULT_CONNECTION_TIMEOUT_SECONDS, min_value=1,
        ),
        connection_attempts=parse_int_config_value(
            config, "telethon", "connection_attempts", default=DEFAULT_CONNECTION_ATTEMPTS, min_value=1,
        ),
        connection_retry_backoff_seconds=parse_int_config_value(
            config, "telethon", "connection_retry_backoff_seconds", default=DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS, min_value=0,
        ),
        connection_cleanup_timeout_seconds=parse_int_config_value(
            config, "telethon", "connection_cleanup_timeout_seconds", default=DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS, min_value=1,
        ),
    )


def ensure_dirs(runtime: RuntimeConfig) -> None:
    runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.media_root.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    SINGLE_MESSAGE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def resolve_relative_date_token(value: str, today: date) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    if raw == "today":
        return today.isoformat()
    if raw == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if raw == "week":
        return (today - timedelta(days=7)).isoformat()
    if raw == "month":
        return (today - timedelta(days=30)).isoformat()
    match = re.fullmatch(r"-?(\d+)d", raw)
    if match:
        return (today - timedelta(days=int(match.group(1)))).isoformat()
    return value.strip()


def parse_filter_datetime(value: str) -> str:
    value = resolve_relative_date_token(value, datetime.now(timezone.utc).date()).strip()
    if len(value) == 10:
        return f"{value}T00:00:00+00:00"
    if value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


def parse_since_datetime(value: str) -> str:
    return parse_filter_datetime(value)


def parse_until_datetime(value: str) -> str:
    value = resolve_relative_date_token(value, datetime.now(timezone.utc).date()).strip()
    if len(value) == 10:
        return f"{value}T23:59:59+00:00"
    if value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


def parse_filter_datetime_value(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw = parse_until_datetime(value) if end_of_day else parse_since_datetime(value)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_channel_lookup_id(channel: str) -> str:
    normalized = channel.strip()
    if normalized.startswith("-100") and normalized[4:].isdigit():
        return normalized[4:]
    return normalized.lstrip("-")
