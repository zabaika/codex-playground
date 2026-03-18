#!/usr/bin/env python3
import argparse
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
BASE_DIR = PROJECT_ROOT
CONFIG_DIR = BASE_DIR / "config"
RUNTIME_LOCAL_FILE = CONFIG_DIR / "runtime.local.toml"
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "telegram_history.sqlite3"
MEDIA_DIR = DATA_DIR / "media"
SESSION_DIR = DATA_DIR / "sessions"
EXPORT_DIR = DATA_DIR / "exports"
OP_REFERENCE_PREFIX = "op://"
_SECRET_CACHE: dict[str, str] = {}


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY,
    access_hash TEXT,
    username TEXT,
    title TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    grouped_id TEXT,
    date_utc TEXT NOT NULL,
    edit_date_utc TEXT,
    sender_id TEXT,
    sender_username TEXT,
    sender_display_name TEXT,
    text TEXT NOT NULL,
    views INTEGER,
    forwards INTEGER,
    replies INTEGER,
    has_media INTEGER NOT NULL DEFAULT 0,
    media_kind TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, message_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_assets (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    media_kind TEXT NOT NULL,
    local_path TEXT,
    mime_type TEXT,
    file_size INTEGER,
    ocr_status TEXT NOT NULL DEFAULT 'pending',
    ocr_text TEXT,
    ocr_error TEXT,
    ocr_processed_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, message_id, ordinal),
    FOREIGN KEY (channel_id, message_id) REFERENCES messages(channel_id, message_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_state (
    channel_id INTEGER PRIMARY KEY,
    last_backfill_message_id INTEGER,
    last_tail_message_id INTEGER,
    last_tail_at TEXT,
    last_live_event_at TEXT,
    last_full_sync_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    feature TEXT NOT NULL,
    stage TEXT NOT NULL,
    channel TEXT,
    since TEXT,
    until TEXT,
    model TEXT NOT NULL,
    request_index INTEGER,
    message_count INTEGER,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, date_utc DESC);
CREATE INDEX IF NOT EXISTS idx_media_assets_ocr_status ON media_assets(ocr_status, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_log_created_at ON ai_usage_log(created_at DESC);
"""

MESSAGES_TABLE_SQL = """
CREATE TABLE messages (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    grouped_id TEXT,
    date_utc TEXT NOT NULL,
    edit_date_utc TEXT,
    sender_id TEXT,
    sender_username TEXT,
    sender_display_name TEXT,
    text TEXT NOT NULL,
    views INTEGER,
    forwards INTEGER,
    replies INTEGER,
    has_media INTEGER NOT NULL DEFAULT 0,
    media_kind TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, message_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
)
"""

MEDIA_ASSETS_TABLE_SQL = """
CREATE TABLE media_assets (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    media_kind TEXT NOT NULL,
    local_path TEXT,
    mime_type TEXT,
    file_size INTEGER,
    ocr_status TEXT NOT NULL DEFAULT 'pending',
    ocr_text TEXT,
    ocr_error TEXT,
    ocr_processed_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, message_id, ordinal),
    FOREIGN KEY (channel_id, message_id) REFERENCES messages(channel_id, message_id) ON DELETE CASCADE
)
"""


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
    sync_total_limit: int = 0
    sync_mode_limits: dict[str, int] = field(default_factory=dict)


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


def require_int_config_value(config: dict[str, Any], section: str, key: str, *, min_value: int = 0) -> int:
    raw = get_config_value(config, section, key)
    if not raw:
        raise SystemExit(f"Missing {section}.{key} in runtime config.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer in {section}.{key}: {raw}") from exc
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
    sync_batch_size = require_int_config_value(config, "sync", "batch_size", min_value=0)
    sync_total_limit = require_int_config_value(config, "sync", "sync_limit", min_value=0)
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
        vision_prompt=vision_prompt,
        sync_batch_size=sync_batch_size,
        default_auth_mode=default_auth_mode,
        public_auth_mode=public_auth_mode,
        private_auth_mode=private_auth_mode,
        default_channels=default_channels,
        sync_total_limit=sync_total_limit,
        sync_mode_limits=sync_mode_limits,
    )


def now_utc() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs(runtime: RuntimeConfig) -> None:
    runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.media_root.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def connect_db(runtime: RuntimeConfig) -> sqlite3.Connection:
    ensure_dirs(runtime)
    conn = sqlite3.connect(runtime.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    migrate_sqlite_schema(conn)
    conn.commit()


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def entity_display_name(entity: Any) -> str | None:
    if entity is None:
        return None
    first = getattr(entity, "first_name", None) or ""
    last = getattr(entity, "last_name", None) or ""
    full_name = f"{first} {last}".strip()
    if full_name:
        return full_name
    return optional_text(getattr(entity, "title", None)) or optional_text(getattr(entity, "username", None))


def table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row[1]) for row in rows]


def migrate_sqlite_schema(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "messages")
    needs_messages_rebuild = bool(columns) and (
        "post_author" in columns or "sender_username" not in columns or "sender_display_name" not in columns
    )
    if not needs_messages_rebuild:
        conn.execute("UPDATE channels SET access_hash = NULL WHERE access_hash = ''")
        conn.execute("UPDATE messages SET grouped_id = NULL WHERE grouped_id = ''")
        conn.execute("UPDATE messages SET sender_id = NULL WHERE sender_id = ''")
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE messages RENAME TO messages_old")
    conn.execute("ALTER TABLE media_assets RENAME TO media_assets_old")
    conn.execute(MESSAGES_TABLE_SQL)
    conn.execute(MEDIA_ASSETS_TABLE_SQL)
    conn.execute(
        """
        INSERT INTO messages (
            channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id,
            sender_username, sender_display_name, text, views, forwards, replies,
            has_media, media_kind, raw_json, content_hash, imported_at
        )
        SELECT
            channel_id,
            message_id,
            NULLIF(grouped_id, ''),
            date_utc,
            edit_date_utc,
            NULLIF(sender_id, ''),
            NULL,
            NULL,
            text,
            views,
            forwards,
            replies,
            has_media,
            media_kind,
            raw_json,
            content_hash,
            imported_at
        FROM messages_old
        """
    )
    conn.execute(
        """
        INSERT INTO media_assets (
            channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size,
            ocr_status, ocr_text, ocr_error, ocr_processed_at, created_at
        )
        SELECT
            channel_id, message_id, ordinal, media_kind, NULLIF(local_path, ''), NULLIF(mime_type, ''),
            file_size, ocr_status, NULLIF(ocr_text, ''), NULLIF(ocr_error, ''), ocr_processed_at, created_at
        FROM media_assets_old
        """
    )
    conn.execute("DROP TABLE media_assets_old")
    conn.execute("DROP TABLE messages_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, date_utc DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_assets_ocr_status ON media_assets(ocr_status, created_at)")
    conn.execute("UPDATE channels SET access_hash = NULL WHERE access_hash = ''")
    conn.execute("PRAGMA foreign_keys = ON")


def require_telethon() -> Any:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit(
            "Telethon is not installed. Install it first, for example: python3 -m pip install telethon"
        ) from exc
    return TelegramClient


async def current_read_inbox_max_id(client: Any, entity: Any) -> int | None:
    try:
        from telethon.tl import functions, types
    except ImportError as exc:
        raise SystemExit(
            "Telethon is not installed. Install it first, for example: python3 -m pip install telethon"
        ) from exc

    response = await client(
        functions.messages.GetPeerDialogsRequest(
            peers=[types.InputDialogPeer(peer=entity)]
        )
    )
    dialogs = getattr(response, "dialogs", None) or []
    if not dialogs:
        return None
    return getattr(dialogs[0], "read_inbox_max_id", None)


def require_tesseract(runtime: RuntimeConfig) -> str:
    binary = shutil.which(runtime.tesseract_binary)
    if not binary:
        raise SystemExit(
            f"Tesseract binary '{runtime.tesseract_binary}' was not found. Install tesseract or set [paths].tesseract_binary."
        )
    return binary


def make_session_path(runtime: RuntimeConfig, auth_mode: str) -> Path:
    session_name = runtime.user_session_name if auth_mode == "user" else runtime.bot_session_name
    return SESSION_DIR / session_name


def normalize_auth_mode(auth_mode: str) -> str:
    if auth_mode not in {"auto", "bot", "user"}:
        raise SystemExit(f"Unsupported auth mode: {auth_mode}")
    return auth_mode


def classify_channel_reference(channel: str) -> str:
    channel = channel.strip()
    if channel.startswith("@"):
        return "public"
    if channel.startswith("https://t.me/+") or channel.startswith("t.me/+"):
        return "private"
    if channel.startswith("https://t.me/joinchat/") or channel.startswith("t.me/joinchat/"):
        return "private"
    if channel.startswith("https://t.me/") or channel.startswith("t.me/"):
        return "public"
    if channel.lstrip("-").isdigit():
        return "private"
    return "private"


def resolve_auth_mode(runtime: RuntimeConfig, auth_mode: str, channel: str) -> str:
    auth_mode = normalize_auth_mode(auth_mode)
    if auth_mode != "auto":
        return auth_mode

    if classify_channel_reference(channel) == "public":
        return normalize_auth_mode(runtime.public_auth_mode)
    return normalize_auth_mode(runtime.private_auth_mode)


async def open_telethon_client(runtime: RuntimeConfig, auth_mode: str) -> Any:
    if not runtime.api_id or not runtime.api_hash:
        raise SystemExit(
            "Missing Telethon credentials. Fill [telethon].api_id and [secrets].api_hash in runtime.local.toml."
        )

    TelegramClient = require_telethon()
    client = TelegramClient(str(make_session_path(runtime, auth_mode)), int(runtime.api_id), runtime.api_hash)
    if auth_mode == "bot":
        if not runtime.bot_token:
            raise SystemExit("Missing bot token for bot auth mode. Fill [secrets].bot_token in runtime.local.toml.")
        await client.start(bot_token=runtime.bot_token)
    else:
        if not runtime.phone:
            raise SystemExit("Missing phone for user auth mode. Fill [telethon].phone in runtime.local.toml.")
        await client.start(phone=runtime.phone, password=(runtime.user_password or None))
    return client


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return str(value)


def safe_to_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def is_image_media_kind(media_kind: str | None) -> bool:
    if not media_kind:
        return False
    lowered = media_kind.lower()
    return lowered == "photo" or lowered.startswith("image/")


def message_text(message: Any) -> str:
    return getattr(message, "message", None) or getattr(message, "text", None) or ""


def message_media_kind(message: Any) -> str | None:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "document", None):
        mime_type = getattr(getattr(message, "document", None), "mime_type", None)
        if mime_type:
            return mime_type
        return "document"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "sticker", None):
        return "sticker"
    return None


def channel_dir(runtime: RuntimeConfig, channel_id: int) -> Path:
    path = runtime.media_root / str(channel_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def minimal_entity_json(entity: Any) -> str:
    payload = {
        "id": getattr(entity, "id", None),
        "username": getattr(entity, "username", None),
        "title": getattr(entity, "title", None) or getattr(entity, "first_name", None),
        "type": getattr(entity, "__class__", type(entity)).__name__,
    }
    return safe_to_json(payload)


def minimal_message_json(message: Any, media_kind: str | None, text: str) -> str:
    payload = {
        "id": getattr(message, "id", None),
        "date_utc": serialize_datetime(getattr(message, "date", None)),
        "edit_date_utc": serialize_datetime(getattr(message, "edit_date", None)),
        "text_length": len(text),
        "has_media": bool(getattr(message, "media", None)),
        "media_kind": media_kind,
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies": getattr(getattr(message, "replies", None), "replies", None),
    }
    return safe_to_json(payload)


def direct_sender_metadata(message: Any) -> tuple[str | None, str | None]:
    sender = getattr(message, "sender", None)
    username = optional_text(getattr(message, "sender_username", None))
    display_name = optional_text(getattr(message, "sender_display_name", None))
    if sender is not None:
        username = username or optional_text(getattr(sender, "username", None))
        display_name = display_name or entity_display_name(sender)
    return username, display_name


async def resolve_sender_metadata(entity: Any, message: Any) -> tuple[str | None, str | None]:
    is_channel_post = bool(getattr(message, "post", False))
    sender_id = getattr(message, "sender_id", None)
    if is_channel_post and (sender_id is None or sender_id == getattr(entity, "id", None)):
        return optional_text(getattr(entity, "username", None)), entity_display_name(entity)

    username, display_name = direct_sender_metadata(message)
    if username or display_name:
        return username, display_name

    sender = getattr(message, "sender", None)
    if sender is None and sender_id is not None:
        sender = await message.get_sender()
    if sender is None:
        return None, None
    return optional_text(getattr(sender, "username", None)), entity_display_name(sender)


async def download_media_if_present(runtime: RuntimeConfig, client: Any, message: Any, channel_id: int) -> tuple[str | None, int | None]:
    if not getattr(message, "media", None):
        return None, None

    target_dir = channel_dir(runtime, channel_id)
    file_base = target_dir / f"{getattr(message, 'id')}"
    downloaded = await client.download_media(message, file=str(file_base))
    if not downloaded:
        return None, None

    path = Path(downloaded)
    size = path.stat().st_size if path.exists() else None
    return str(path), size


def upsert_channel(conn: sqlite3.Connection, entity: Any) -> None:
    now = now_utc()
    conn.execute(
        """
        INSERT INTO channels (
            channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            access_hash = excluded.access_hash,
            username = excluded.username,
            title = excluded.title,
            channel_type = excluded.channel_type,
            raw_json = excluded.raw_json,
            last_seen_at = excluded.last_seen_at
        """,
        (
            entity.id,
            optional_text(getattr(entity, "access_hash", None)),
            optional_text(getattr(entity, "username", None)),
            getattr(entity, "title", None) or getattr(entity, "first_name", "unknown"),
            getattr(entity, "__class__", type(entity)).__name__,
            minimal_entity_json(entity),
            now,
            now,
        ),
    )


def upsert_message(
    conn: sqlite3.Connection,
    entity: Any,
    message: Any,
    sender_username: str | None,
    sender_display_name: str | None,
    downloaded_path: str | None,
    downloaded_size: int | None,
) -> None:
    text = message_text(message)
    media_kind = message_media_kind(message)
    imported_at = now_utc()
    content_hash = f"{getattr(message, 'id')}:{serialize_datetime(getattr(message, 'edit_date', None))}:{len(text)}:{media_kind or '-'}"
    grouped_id = getattr(message, "grouped_id", None)
    grouped_id_value = str(grouped_id) if grouped_id is not None else None

    conn.execute(
        """
        INSERT INTO messages (
            channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, sender_username, sender_display_name, text,
            views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, message_id) DO UPDATE SET
            grouped_id = excluded.grouped_id,
            date_utc = excluded.date_utc,
            edit_date_utc = excluded.edit_date_utc,
            sender_id = excluded.sender_id,
            sender_username = excluded.sender_username,
            sender_display_name = excluded.sender_display_name,
            text = excluded.text,
            views = excluded.views,
            forwards = excluded.forwards,
            replies = excluded.replies,
            has_media = excluded.has_media,
            media_kind = excluded.media_kind,
            raw_json = excluded.raw_json,
            content_hash = excluded.content_hash,
            imported_at = excluded.imported_at
        """,
        (
            entity.id,
            message.id,
            grouped_id_value,
            serialize_datetime(getattr(message, "date", None)) or imported_at,
            serialize_datetime(getattr(message, "edit_date", None)),
            optional_text(getattr(message, "sender_id", None)),
            optional_text(sender_username),
            optional_text(sender_display_name),
            text,
            getattr(message, "views", None),
            getattr(message, "forwards", None),
            getattr(getattr(message, "replies", None), "replies", None),
            1 if getattr(message, "media", None) else 0,
            media_kind,
            minimal_message_json(message, media_kind, text),
            content_hash,
            imported_at,
        ),
    )

    if downloaded_path or media_kind:
        conn.execute(
            """
            INSERT INTO media_assets (
                channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size,
                ocr_status, created_at
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, message_id, ordinal) DO UPDATE SET
                media_kind = excluded.media_kind,
                local_path = COALESCE(excluded.local_path, media_assets.local_path),
                mime_type = excluded.mime_type,
                file_size = excluded.file_size,
                ocr_status = CASE
                    WHEN excluded.local_path IS NOT NULL THEN 'pending'
                    ELSE media_assets.ocr_status
                END
            """,
            (
                entity.id,
                message.id,
                media_kind or "media",
                optional_text(downloaded_path),
                optional_text(media_kind),
                downloaded_size,
                "pending" if downloaded_path else "skipped",
                imported_at,
            ),
        )


def update_sync_state(
    conn: sqlite3.Connection,
    channel_id: int,
    *,
    last_backfill_message_id: int | None = None,
    last_tail_message_id: int | None = None,
    last_tail_at: str | None = None,
    last_full_sync_at: str | None = None,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (
            channel_id, last_backfill_message_id, last_tail_message_id, last_tail_at, last_full_sync_at, last_error
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            last_backfill_message_id = COALESCE(excluded.last_backfill_message_id, sync_state.last_backfill_message_id),
            last_tail_message_id = COALESCE(excluded.last_tail_message_id, sync_state.last_tail_message_id),
            last_tail_at = COALESCE(excluded.last_tail_at, sync_state.last_tail_at),
            last_full_sync_at = COALESCE(excluded.last_full_sync_at, sync_state.last_full_sync_at),
            last_error = excluded.last_error
        """,
        (channel_id, last_backfill_message_id, last_tail_message_id, last_tail_at, last_full_sync_at, last_error),
    )


def message_exists(conn: sqlite3.Connection, channel_id: int, message_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM messages WHERE channel_id = ? AND message_id = ? LIMIT 1",
        (channel_id, message_id),
    ).fetchone()
    return row is not None


def latest_stored_message_id(conn: sqlite3.Connection, channel_id: int) -> int | None:
    row = conn.execute(
        "SELECT MAX(message_id) AS max_id FROM messages WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    return row["max_id"] if row and row["max_id"] is not None else None


def latest_processed_message_id(conn: sqlite3.Connection, channel_id: int, mode: str) -> int | None:
    row = conn.execute(
        """
        SELECT last_backfill_message_id, last_tail_message_id
        FROM sync_state
        WHERE channel_id = ?
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    if row:
        if mode == "backfill" and row["last_backfill_message_id"] is not None:
            return row["last_backfill_message_id"]
        if mode in {"tail", "update"} and row["last_tail_message_id"] is not None:
            return row["last_tail_message_id"]
    return latest_stored_message_id(conn, channel_id)


def media_needs_download(conn: sqlite3.Connection, channel_id: int, message_id: int) -> bool:
    row = conn.execute(
        """
        SELECT local_path
        FROM media_assets
        WHERE channel_id = ? AND message_id = ? AND ordinal = 0
        LIMIT 1
        """,
        (channel_id, message_id),
    ).fetchone()
    if row is None:
        return True
    local_path = row["local_path"]
    return not local_path or not Path(str(local_path)).exists()


def parse_channel_list(raw: str) -> list[str]:
    channels = [item.strip() for item in raw.split(",") if item.strip()]
    if not channels:
        raise SystemExit("At least one channel is required.")
    return channels


def resolve_channels_argument(runtime: RuntimeConfig, raw_channel: str | None) -> list[str]:
    if raw_channel and raw_channel.strip():
        return parse_channel_list(raw_channel)
    if runtime.default_channels:
        return runtime.default_channels
    raise SystemExit("At least one channel is required. Pass --channel or set [channels].default_list in runtime.local.toml.")


def allocate_sync_limits(channels: list[str], total_limit: int, per_channel_limit: int | None) -> list[tuple[str, int]]:
    if not channels:
        return []
    if per_channel_limit is not None and per_channel_limit <= 0:
        per_channel_limit = None
    if total_limit <= 0:
        return [(channel, per_channel_limit if per_channel_limit is not None else -1) for channel in channels]
    remaining = total_limit
    plans: list[tuple[str, int]] = []
    for channel in channels:
        if remaining <= 0:
            plans.append((channel, 0))
            continue
        if per_channel_limit is None:
            limit = remaining
        else:
            limit = min(max(1, per_channel_limit), remaining)
        plans.append((channel, limit))
        remaining -= limit
    return plans


def default_sync_limit_for_mode(runtime: RuntimeConfig, mode: str) -> int:
    limit = runtime.sync_mode_limits.get(mode)
    if limit is None:
        raise SystemExit(f"Missing default sync limit for mode '{mode}' in runtime config.")
    return max(1, int(limit))


async def sync_one_channel(
    conn: sqlite3.Connection,
    runtime: RuntimeConfig,
    args: argparse.Namespace,
    mode: str,
    channel: str,
) -> dict[str, Any]:
    auth_mode = resolve_auth_mode(runtime, args.auth_mode, channel)
    client = await open_telethon_client(runtime, auth_mode)
    async with client:
        entity = await client.get_entity(channel)
        upsert_channel(conn, entity)
        batch_commit_size = max(0, int(getattr(args, "batch_size", 0) or runtime.sync_batch_size or 0))
        pending_db_changes = 0
        scan_limit = getattr(args, "limit", None)
        if scan_limit is not None and scan_limit <= 0:
            scan_limit = None

        if mode in {"backfill", "tail", "update"}:
            iterator = client.iter_messages(entity, limit=scan_limit)
        else:
            raise SystemExit(f"Unsupported sync mode: {mode}")

        processed = 0
        skipped_existing = 0
        refreshed_existing_media = 0
        highest_message_id = None
        mark_read_target_id = latest_processed_message_id(conn, entity.id, mode) if getattr(args, "mark_read", False) else None
        existing_max_id = latest_stored_message_id(conn, entity.id) if mode == "update" else None
        since_dt = parse_filter_datetime_value(getattr(args, "since", None))
        until_dt = parse_filter_datetime_value(getattr(args, "until", None), end_of_day=True)
        async for message in iterator:
            message_dt = getattr(message, "date", None)
            if message_dt is not None and message_dt.tzinfo is None:
                message_dt = message_dt.replace(tzinfo=timezone.utc)
            if until_dt is not None and message_dt is not None and message_dt > until_dt:
                continue
            if since_dt is not None and message_dt is not None and message_dt < since_dt:
                break
            if mode == "update" and existing_max_id is not None and message.id <= existing_max_id:
                break
            downloaded_path = None
            downloaded_size = None
            sender_username, sender_display_name = await resolve_sender_metadata(entity, message)
            exists = message_exists(conn, entity.id, message.id)
            if exists:
                skipped_existing += 1
                if args.download_media and getattr(message, "media", None) and media_needs_download(conn, entity.id, message.id):
                    downloaded_path, downloaded_size = await download_media_if_present(runtime, client, message, entity.id)
                    upsert_message(conn, entity, message, sender_username, sender_display_name, downloaded_path, downloaded_size)
                    if downloaded_path:
                        refreshed_existing_media += 1
                        pending_db_changes += 1
                        if batch_commit_size and pending_db_changes >= batch_commit_size:
                            conn.commit()
                            pending_db_changes = 0
                continue
            if args.download_media and getattr(message, "media", None):
                downloaded_path, downloaded_size = await download_media_if_present(runtime, client, message, entity.id)
            upsert_message(conn, entity, message, sender_username, sender_display_name, downloaded_path, downloaded_size)
            highest_message_id = max(highest_message_id or message.id, message.id)
            processed += 1
            pending_db_changes += 1
            if batch_commit_size and pending_db_changes >= batch_commit_size:
                conn.commit()
                pending_db_changes = 0

        now = now_utc()
        if mode == "backfill":
            update_sync_state(
                conn,
                entity.id,
                last_backfill_message_id=highest_message_id,
                last_full_sync_at=now,
                last_error=None,
            )
        else:
            update_sync_state(
                conn,
                entity.id,
                last_tail_message_id=highest_message_id,
                last_tail_at=now,
                last_error=None,
            )
        ocr_processed = 0
        if getattr(args, "ocr", False):
            binary = require_tesseract(runtime)
            ocr_processed = process_pending_ocr(conn, binary, limit=scan_limit, channel_id=entity.id)
        if mark_read_target_id is None and highest_message_id is not None:
            mark_read_target_id = highest_message_id
        current_read_max_id = None
        marked_read_from = None
        marked_read_until = None
        if getattr(args, "mark_read", False):
            if auth_mode != "user":
                raise SystemExit("Mark-as-read is only supported in user auth mode.")
            if mark_read_target_id is not None:
                current_read_max_id = await current_read_inbox_max_id(client, entity)
                if current_read_max_id is None or mark_read_target_id > current_read_max_id:
                    marked_read_from = (current_read_max_id + 1) if current_read_max_id is not None else 1
                    await client.send_read_acknowledge(entity, max_id=mark_read_target_id)
                    marked_read_until = mark_read_target_id
        conn.commit()
        return {
            "channel": channel,
            "channel_id": entity.id,
            "mode": mode,
            "auth_mode": auth_mode,
            "processed_messages": processed,
            "skipped_existing": skipped_existing,
            "refreshed_existing_media": refreshed_existing_media,
            "download_media": bool(args.download_media),
            "ocr_processed": ocr_processed,
            "since": getattr(args, "since", None),
            "until": getattr(args, "until", None),
            "current_read_max_id": current_read_max_id,
            "marked_read": bool(marked_read_until is not None),
            "marked_read_from": marked_read_from,
            "marked_read_until": marked_read_until,
        }


async def sync_messages(runtime: RuntimeConfig, args: argparse.Namespace, mode: str) -> int:
    conn = connect_db(runtime)
    init_db(conn)
    channels = resolve_channels_argument(runtime, args.channel)
    results = []
    per_channel_limit = getattr(args, "limit", None)
    if per_channel_limit is None:
        per_channel_limit = default_sync_limit_for_mode(runtime, mode)
    elif per_channel_limit <= 0:
        per_channel_limit = None
    plans = allocate_sync_limits(channels, runtime.sync_total_limit, per_channel_limit)
    for channel, limit in plans:
        if limit == 0:
            continue
        channel_args = argparse.Namespace(**vars(args))
        channel_args.limit = None if limit < 0 else limit
        results.append(await sync_one_channel(conn, runtime, channel_args, mode, channel))
    print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False, indent=2))
    return 0


def iter_pending_ocr(
    conn: sqlite3.Connection,
    limit: int | None,
    channel_id: int | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    where = [
        "ma.ocr_status = 'pending'",
        "ma.local_path IS NOT NULL",
        "(ma.media_kind = 'photo' OR ma.media_kind LIKE 'image/%')",
    ]
    if channel_id is not None:
        where.append("ma.channel_id = ?")
        params.append(channel_id)
    if since:
        where.append("m.date_utc >= ?")
        params.append(parse_since_datetime(since))
    if until:
        where.append("m.date_utc <= ?")
        params.append(parse_until_datetime(until))
    limit_sql = ""
    if limit is not None and limit > 0:
        params.append(limit)
        limit_sql = "\n            LIMIT ?"
    return list(
        conn.execute(
            f"""
            SELECT ma.channel_id, ma.message_id, ma.ordinal, ma.local_path, ma.media_kind
            FROM media_assets ma
            JOIN messages m
              ON m.channel_id = ma.channel_id AND m.message_id = ma.message_id
            WHERE {' AND '.join(where)}
            ORDER BY m.date_utc ASC, ma.created_at ASC{limit_sql}
            """,
            params,
        )
    )


def run_tesseract(binary: str, image_path: str) -> str:
    result = subprocess.run(
        [binary, image_path, "stdout"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Tesseract failed with exit code {result.returncode}")
    return result.stdout.strip()


def process_pending_ocr(
    conn: sqlite3.Connection,
    binary: str,
    *,
    limit: int | None,
    channel_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    rows = iter_pending_ocr(conn, limit, channel_id=channel_id, since=since, until=until)
    processed = 0
    for row in rows:
        try:
            if not is_image_media_kind(row["media_kind"]):
                conn.execute(
                    """
                    UPDATE media_assets
                    SET ocr_status = ?, ocr_error = ?, ocr_processed_at = ?
                    WHERE channel_id = ? AND message_id = ? AND ordinal = ?
                    """,
                    ("skipped", optional_text("OCR is only supported for image media."), now_utc(), row["channel_id"], row["message_id"], row["ordinal"]),
                )
                processed += 1
                continue
            text = run_tesseract(binary, row["local_path"])
            conn.execute(
                """
                UPDATE media_assets
                SET ocr_status = ?, ocr_text = ?, ocr_error = NULL, ocr_processed_at = ?
                WHERE channel_id = ? AND message_id = ? AND ordinal = ?
                """,
                ("done", optional_text(text), now_utc(), row["channel_id"], row["message_id"], row["ordinal"]),
            )
        except Exception as exc:
            conn.execute(
                """
                UPDATE media_assets
                SET ocr_status = ?, ocr_error = ?, ocr_processed_at = ?
                WHERE channel_id = ? AND message_id = ? AND ordinal = ?
                """,
                ("error", optional_text("OCR processing failed."), now_utc(), row["channel_id"], row["message_id"], row["ordinal"]),
            )
        processed += 1
    conn.commit()
    return processed


def resolve_channel_filter(conn: sqlite3.Connection, channel: str) -> sqlite3.Row | None:
    normalized = channel.strip()
    username = normalized[1:] if normalized.startswith("@") else normalized
    rows = conn.execute(
        """
        SELECT channel_id, username, title
        FROM channels
        WHERE username = ? OR CAST(channel_id AS TEXT) = ?
        LIMIT 1
        """,
        (username, normalized.lstrip("-")),
    ).fetchall()
    return rows[0] if rows else None


def parse_filter_datetime(value: str) -> str:
    value = value.strip()
    if len(value) == 10:
        return f"{value}T00:00:00+00:00"
    if value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


def parse_since_datetime(value: str) -> str:
    return parse_filter_datetime(value)


def parse_until_datetime(value: str) -> str:
    value = value.strip()
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


def sanitize_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return cleaned.strip("_") or "channel"


def export_channel_csv(
    conn: sqlite3.Connection,
    runtime: RuntimeConfig,
    *,
    channel: str,
    limit: int | None,
    since: str | None,
    until: str | None,
    output_path: str | None,
) -> tuple[Path, int]:
    channel_row = resolve_channel_filter(conn, channel)
    if channel_row is None:
        raise SystemExit(f"Channel '{channel}' is not present in the local database yet. Run sync --mode tail or sync --mode backfill first.")

    params: list[Any] = [channel_row["channel_id"]]
    where = ["m.channel_id = ?"]
    order_by = "ORDER BY m.message_id DESC"
    limit_sql = ""

    if since:
        where.append("m.date_utc >= ?")
        params.append(parse_since_datetime(since))
    if until:
        where.append("m.date_utc <= ?")
        params.append(parse_until_datetime(until))
    if since or until:
        order_by = "ORDER BY m.date_utc DESC, m.message_id DESC"
    elif limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    else:
        limit_sql = "LIMIT 100"

    rows = conn.execute(
        f"""
        SELECT
            c.channel_id,
            c.username,
            c.title,
            m.message_id,
            m.date_utc,
            m.edit_date_utc,
            m.sender_id,
            m.sender_username,
            m.sender_display_name,
            m.text,
            m.views,
            m.forwards,
            m.replies,
            m.has_media,
            m.media_kind,
            CASE WHEN ma.local_path IS NOT NULL THEN 1 ELSE 0 END AS has_local_media,
            ma.ocr_status,
            ma.ocr_text
        FROM messages m
        JOIN channels c ON c.channel_id = m.channel_id
        LEFT JOIN media_assets ma
            ON ma.channel_id = m.channel_id
           AND ma.message_id = m.message_id
           AND ma.ordinal = 0
        WHERE {" AND ".join(where)}
        {order_by}
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()

    output = Path(output_path) if output_path else EXPORT_DIR / (
        f"{sanitize_filename(channel_row['username'] or str(channel_row['channel_id']))}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            delimiter=";",
            fieldnames=[
                "channel_id",
                "username",
                "title",
                "message_id",
                "date_utc",
                "edit_date_utc",
                "sender_id",
                "sender_username",
                "sender_display_name",
                "text",
                "views",
                "forwards",
                "replies",
                "has_media",
                "media_kind",
                "has_local_media",
                "ocr_status",
                "ocr_text",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return output, len(rows)


def cmd_init_db(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    conn = connect_db(runtime)
    init_db(conn)
    print(json.dumps({"status": "initialized"}, ensure_ascii=False, indent=2))
    return 0


def cmd_ocr_pending(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    conn = connect_db(runtime)
    init_db(conn)
    binary = require_tesseract(runtime)
    channels = resolve_channels_argument(runtime, args.channel) if getattr(args, "channel", None) or runtime.default_channels else [None]
    results = []
    for channel in channels:
        channel_id = None
        if channel is not None:
            channel_row = resolve_channel_filter(conn, channel)
            if channel_row is None:
                raise SystemExit(f"Channel '{channel}' is not present in the local database yet. Run sync --mode tail or sync --mode backfill first.")
            channel_id = channel_row["channel_id"]
        processed = process_pending_ocr(
            conn,
            binary,
            limit=args.limit,
            channel_id=channel_id,
            since=args.since,
            until=args.until,
        )
        results.append(
            {
                "channel": channel,
                "processed_assets": processed,
                "limit": args.limit,
                "since": args.since,
                "until": args.until,
            }
        )
    print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False, indent=2))
    return 0


def cmd_export_csv(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    conn = connect_db(runtime)
    init_db(conn)
    channels = resolve_channels_argument(runtime, args.channel)
    results = []
    for channel in channels:
        output, row_count = export_channel_csv(
            conn,
            runtime,
            channel=channel,
            limit=args.limit,
            since=args.since,
            until=args.until,
            output_path=args.output if len(channels) == 1 else None,
        )
        results.append(
            {
                "channel": channel,
                "output_file": output.name,
                "row_count": row_count,
                "limit": args.limit,
                "since": args.since,
                "until": args.until,
            }
        )
    print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False, indent=2))
    return 0


def cmd_inspect_state(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    conn = connect_db(runtime)
    init_db(conn)

    rows = list(
        conn.execute(
            """
            SELECT c.channel_id, c.username, c.title, s.last_backfill_message_id, s.last_tail_message_id,
                   s.last_tail_at, s.last_full_sync_at, s.last_error
            FROM channels c
            LEFT JOIN sync_state s ON s.channel_id = c.channel_id
            ORDER BY c.last_seen_at DESC
            """
        )
    )
    payload = [dict(row) for row in rows]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    checks = {
        "user_session_name": runtime.user_session_name,
        "bot_session_name": runtime.bot_session_name,
        "has_api_id": bool(runtime.api_id),
        "has_api_hash": bool(runtime.api_hash),
        "has_phone": bool(runtime.phone),
        "has_bot_token": bool(runtime.bot_token),
        "has_user_password": bool(runtime.user_password),
        "default_auth_mode": runtime.default_auth_mode,
        "public_auth_mode": runtime.public_auth_mode,
        "private_auth_mode": runtime.private_auth_mode,
        "telethon_installed": False,
        "tesseract_found": bool(shutil.which(runtime.tesseract_binary)),
    }
    try:
        require_telethon()
        checks["telethon_installed"] = True
    except SystemExit:
        checks["telethon_installed"] = False
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Telegram history client using Telethon + SQLite + Tesseract."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Create or migrate the local SQLite database.")
    init_db.set_defaults(func=cmd_init_db)

    doctor = subparsers.add_parser("doctor", help="Check local configuration and dependency availability.")
    doctor.set_defaults(func=cmd_doctor)

    inspect_state = subparsers.add_parser("inspect-state", help="Print known channels and sync checkpoints.")
    inspect_state.set_defaults(func=cmd_inspect_state)

    export_csv = subparsers.add_parser("export-csv", help="Export saved channel history from SQLite into CSV.")
    export_csv.add_argument("--channel", help="Channel username/id or comma-separated list.")
    export_csv.add_argument("--limit", type=int, default=None, help="Export the latest N messages.")
    export_csv.add_argument("--since", help="Export messages since this UTC date or datetime.")
    export_csv.add_argument("--until", help="Export messages until this UTC date or datetime.")
    export_csv.add_argument("--output", help="Optional CSV output path.")
    export_csv.add_argument("--auth-mode", choices=["auto", "bot", "user"], default=None, help="Accepted for bot-command compatibility; export reads from local SQLite only.")
    export_csv.set_defaults(func=cmd_export_csv)

    sync = subparsers.add_parser("sync", help="Unified sync command for backfill, tail, and update modes.")
    sync.add_argument("--mode", choices=["backfill", "tail", "update"], required=True, help="Sync mode.")
    sync.add_argument("--channel", help="Channel username/link or comma-separated list, for example @vcnews,@another.")
    sync.add_argument("--limit", type=int, default=None, help="How many latest remote messages to scan. Defaults depend on mode; use 0 to remove the per-channel cap.")
    sync.add_argument("--since", help="Ingest only messages since this UTC date or datetime.")
    sync.add_argument("--until", help="Ingest only messages until this UTC date or datetime.")
    sync.add_argument("--download-media", action="store_true", help="Download message media locally.")
    sync.add_argument("--ocr", action="store_true", help="Run OCR for downloaded images from this sync run.")
    sync.add_argument("--mark-read", action="store_true", help="Mark the processed message range as read after a successful user-auth sync.")
    sync.add_argument("--auth-mode", choices=["auto", "bot", "user"], default=None, help="Choose Telegram authorization type.")
    sync.set_defaults(async_func="sync")

    ocr_pending = subparsers.add_parser("ocr-pending", help="Run Tesseract for downloaded images pending OCR.")
    ocr_pending.add_argument("--channel", help="Channel username/id or comma-separated list.")
    ocr_pending.add_argument("--limit", type=int, default=100, help="How many media assets to process.")
    ocr_pending.add_argument("--since", help="Process only assets whose message date is on or after this UTC date or datetime.")
    ocr_pending.add_argument("--until", help="Process only assets whose message date is on or before this UTC date or datetime.")
    ocr_pending.set_defaults(func=cmd_ocr_pending)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        return args.func(args)

    import asyncio

    runtime = resolve_runtime()
    if getattr(args, "auth_mode", None) is None:
        args.auth_mode = runtime.default_auth_mode
    mode = args.mode if getattr(args, "async_func", None) == "sync" else args.async_func
    return asyncio.run(sync_messages(runtime, args, mode))


if __name__ == "__main__":
    sys.exit(main())
