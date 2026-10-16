#!/usr/bin/env python3
import argparse
import asyncio
import csv
import errno
import fcntl
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import sqlite as common_sqlite
from common import process as common_process
from common.process import awake_time
from telegram_shared import secrets as shared_secrets
from telegram_shared.async_retry import AsyncCleanupError, AsyncRetryError, await_with_awake_timeout, retry_async
from telegram_shared.config import get_config_value as shared_get_config_value
from telegram_shared.config import load_runtime_config as shared_load_runtime_config
from telegram_shared.errors import SecretResolutionError


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
# Runtime state must live next to the executed module, not in the source checkout.
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
# Internal index protocol version, independent of operator sizing settings.
FTS_INDEX_VERSION = 1
FTS_INDEX_ADMISSION_KEY = f"internal:fts:v{FTS_INDEX_VERSION}"
RETRYABLE_CONNECTION_ERRNOS = frozenset(
    getattr(errno, name) for name in (
        "ETIMEDOUT", "ECONNRESET", "ECONNABORTED", "ECONNREFUSED", "EPIPE",
        "ENETDOWN", "ENETUNREACH", "EHOSTUNREACH", "EHOSTDOWN",
    ) if hasattr(errno, name)
) | frozenset({socket.EAI_AGAIN, socket.EAI_NONAME})
SQLITE_CONFIG = common_sqlite.load_sqlite_config()


SCHEMA_SQL = """
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
    response_id TEXT,
    prompt_cache_key TEXT,
    prompt_cache_retention TEXT,
    prompt_version_hash TEXT,
    request_index INTEGER,
    message_count INTEGER,
    system_chars INTEGER,
    prompt_chars INTEGER,
    shared_prefix_chars INTEGER,
    shared_prefix_hash TEXT,
    prompt_hash TEXT,
    previous_prompt_hash TEXT,
    previous_response_id TEXT,
    prefix_match_chars_with_previous INTEGER,
    prompt_text TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    cache_write_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    output_chars INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    response_status TEXT,
    incomplete_reason TEXT,
    status TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, date_utc DESC, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_media_assets_ocr_status ON media_assets(ocr_status, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_log_created_at ON ai_usage_log(created_at DESC);
"""

ANALYSIS_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS analysis_jobs (
        job_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(job_id)) > 0),
        kind TEXT NOT NULL CHECK(kind IN ('analysis','fts_backfill')),
        command TEXT,
        requester_id TEXT,
        destination TEXT,
        admission_key TEXT UNIQUE,
        request_json TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        accepted_at TEXT NOT NULL,
        cutoff_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK(status IN ('queued','running','completed','partial','failed','timed_out','cancelled','interrupted')),
        phase TEXT NOT NULL DEFAULT 'sync',
        generation INTEGER NOT NULL DEFAULT 0 CHECK(typeof(generation)='integer' AND generation >= 0),
        cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
        llm_calls INTEGER NOT NULL DEFAULT 0 CHECK(typeof(llm_calls)='integer' AND llm_calls >= 0),
        provider_requests INTEGER NOT NULL DEFAULT 0 CHECK(typeof(provider_requests)='integer' AND provider_requests >= 0),
        used_tokens INTEGER NOT NULL DEFAULT 0 CHECK(typeof(used_tokens)='integer' AND used_tokens >= 0),
        reserved_tokens INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved_tokens)='integer' AND reserved_tokens >= 0),
        downloaded_messages INTEGER NOT NULL DEFAULT 0 CHECK(typeof(downloaded_messages)='integer' AND downloaded_messages >= 0),
        intermediate_bytes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(intermediate_bytes)='integer' AND intermediate_bytes >= 0),
        evidence_records INTEGER NOT NULL DEFAULT 0 CHECK(typeof(evidence_records)='integer' AND evidence_records >= 0),
        checkpoint_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT,
        delivery_json TEXT NOT NULL DEFAULT '{}',
        full_path TEXT,
        full_hash TEXT,
        CHECK((kind='analysis' AND command IS NOT NULL AND command IN ('topics','search','qa'))
              OR (kind='fts_backfill' AND command IS NULL)),
        CHECK((full_path IS NULL) = (full_hash IS NULL))
    )""",
    """CREATE TABLE IF NOT EXISTS analysis_batches (
        batch_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(batch_id)) > 0),
        job_id TEXT NOT NULL REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
        step_key TEXT NOT NULL CHECK(length(trim(step_key)) > 0),
        kind TEXT NOT NULL CHECK(kind IN ('select','expand_query','extract','link','merge','synthesize')),
        state TEXT NOT NULL DEFAULT 'planned' CHECK(state IN ('planned','running','succeeded','failed','skipped')),
        input_digest TEXT,
        cache_key TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        attempts_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id,step_key)
    )""",
    """CREATE TABLE IF NOT EXISTS analysis_evidence (
        record_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(record_id)) > 0),
        batch_id TEXT NOT NULL REFERENCES analysis_batches(batch_id) ON DELETE CASCADE,
        record_type TEXT NOT NULL CHECK(record_type IN ('event','claim','problem','question','answer','experience','opinion')),
        topic_key TEXT,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS analysis_inputs (
        batch_id TEXT NOT NULL REFERENCES analysis_batches(batch_id) ON DELETE CASCADE,
        channel_id INTEGER NOT NULL CHECK(typeof(channel_id)='integer'),
        message_id INTEGER NOT NULL CHECK(typeof(message_id)='integer' AND message_id > 0),
        fragment_start INTEGER NOT NULL DEFAULT 0 CHECK(typeof(fragment_start)='integer' AND fragment_start >= 0),
        fragment_end INTEGER NOT NULL DEFAULT 0 CHECK(typeof(fragment_end)='integer' AND fragment_end >= 0),
        role TEXT NOT NULL CHECK(role IN ('selected','context')),
        date_utc TEXT NOT NULL,
        content_digest TEXT NOT NULL,
        PRIMARY KEY(batch_id,channel_id,message_id,fragment_start,fragment_end,role),
        CHECK((fragment_start=0 AND fragment_end=0) OR fragment_start < fragment_end)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_queue ON analysis_jobs(status,created_at)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_batches_job_state ON analysis_batches(job_id,state,step_key)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_batches_cache ON analysis_batches(cache_key,state)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_evidence_batch ON analysis_evidence(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_evidence_topic ON analysis_evidence(topic_key,record_id)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_inputs_source ON analysis_inputs(channel_id,message_id)",
)


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
    export_default_limit: int = DEFAULT_EXPORT_CSV_LIMIT
    ocr_pending_default_limit: int = DEFAULT_OCR_PENDING_LIMIT
    tesseract_timeout_seconds: int = DEFAULT_TESSERACT_TIMEOUT_SECONDS
    sync_total_limit: int = 0
    sync_mode_limits: dict[str, int] = field(default_factory=dict)
    connection_timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS
    connection_attempts: int = DEFAULT_CONNECTION_ATTEMPTS
    connection_retry_backoff_seconds: int = DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS
    connection_cleanup_timeout_seconds: int = DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS


@dataclass(slots=True)
class StagedMessageWrite:
    entity: Any
    message: Any
    sender_username: str | None
    sender_display_name: str | None
    downloaded_path: str | None
    downloaded_size: int | None


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


def now_utc() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs(runtime: RuntimeConfig) -> None:
    runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.media_root.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    SINGLE_MESSAGE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def connect_db(runtime: RuntimeConfig) -> sqlite3.Connection:
    ensure_dirs(runtime)
    return common_sqlite.connect_sqlite(runtime.db_path, config=SQLITE_CONFIG)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    migrate_sqlite_schema(conn)
    conn.commit()
    # Execute DDL statements individually: executescript would implicitly commit
    # an enclosing transaction and expose a partially created analytic schema.
    with common_sqlite.write_tx(conn, immediate=True):
        for statement in ANALYSIS_SCHEMA_STATEMENTS:
            conn.execute(statement)
        key = [row for row in conn.execute("PRAGMA index_xinfo(idx_messages_channel_date)") if row[5]]
        if [row[2] for row in key] != ["channel_id", "date_utc", "message_id"] or key[1][3] != key[2][3]:
            conn.execute("DROP INDEX IF EXISTS idx_messages_channel_date")
            conn.execute("CREATE INDEX idx_messages_channel_date ON messages(channel_id,date_utc DESC,message_id DESC)")
        if fts5_available(conn):
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                channel_id UNINDEXED,message_id UNINDEXED,text,ocr_text,tokenize='unicode61')""")
            row = conn.execute("SELECT checkpoint_json FROM analysis_jobs WHERE admission_key=?", (FTS_INDEX_ADMISSION_KEY,)).fetchone()
            if row and json.loads(row[0]).get("fts", {}).get("schema_version") != conn.execute("PRAGMA schema_version").fetchone()[0]:
                _invalidate_fts_index_no_tx(conn, "schema_changed")
        else:
            _invalidate_fts_index_no_tx(conn, "fts_unavailable")


def fts5_available(conn: sqlite3.Connection) -> bool:
    return any(row[0] == "fts5" for row in conn.execute("PRAGMA module_list"))


def _message_fts_enabled(conn: sqlite3.Connection) -> bool:
    return fts5_available(conn) and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'",
    ).fetchone() is not None


def _invalidate_fts_index_no_tx(conn: sqlite3.Connection, reason: str) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_jobs'").fetchone() is None:
        return
    row = conn.execute("SELECT job_id,checkpoint_json FROM analysis_jobs WHERE kind='fts_backfill' AND admission_key=?",
                       (FTS_INDEX_ADMISSION_KEY,)).fetchone()
    if row is None:
        return
    checkpoint = json.loads(row[1])
    state = checkpoint.setdefault("fts", {})
    if state.get("state") == "invalid" and state.get("reason") == reason:
        return
    state.update(state="invalid", reason=reason)
    conn.execute("UPDATE analysis_jobs SET checkpoint_json=?,updated_at=? WHERE job_id=?",
                 (json.dumps(checkpoint, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")), now_utc(), row[0]))


def require_fts_ready(conn: sqlite3.Connection) -> dict[str, Any]:
    if not fts5_available(conn):
        raise ValueError("fts_unavailable")
    if not _message_fts_enabled(conn):
        raise ValueError("fts_not_ready")
    row = conn.execute("SELECT status,checkpoint_json FROM analysis_jobs WHERE admission_key=? AND kind='fts_backfill'",
                       (FTS_INDEX_ADMISSION_KEY,)).fetchone()
    if row is None:
        raise ValueError("fts_not_ready")
    state = json.loads(row[1]).get("fts", {})
    if (state.get("version") != FTS_INDEX_VERSION or state.get("state") == "invalid"
            or state.get("schema_version") != conn.execute("PRAGMA schema_version").fetchone()[0]):
        raise ValueError("fts_rebuild_required")
    if row[0] != "completed" or state.get("state") != "ready":
        raise ValueError("fts_not_ready")
    return state


def backfill_fts_page(store, job_id: str, *, generation: int) -> dict[str, Any]:
    """Read and replace the current source revision under one short writer lock."""
    conn = store.conn
    with common_sqlite.write_tx(conn, immediate=True):
        job = store.require_running(job_id, generation=generation)
        if job["kind"] != "fts_backfill" or job["admission_key"] != FTS_INDEX_ADMISSION_KEY:
            raise ValueError("Invalid internal index job")
        if not _message_fts_enabled(conn):
            raise ValueError("fts_unavailable")
        state = job["checkpoint"]["fts"]
        if (state["state"] != "building" or state["version"] != FTS_INDEX_VERSION
                or state["schema_version"] != conn.execute("PRAGMA schema_version").fetchone()[0]):
            raise ValueError("fts_rebuild_required")
        if state["needs_clear"]:
            conn.execute("DELETE FROM messages_fts")
            state["upper_rowid"] = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
            state["needs_clear"] = False
        size = max(1, job["request"]["settings"]["sync"]["batch_size"])
        rows = conn.execute("""SELECT rowid,channel_id,message_id FROM messages
            WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?""", (state["cursor"], state["upper_rowid"], size)).fetchall()
        for row in rows:
            _refresh_message_fts_no_tx(conn, row[1], row[2])
        state["indexed_messages"] += len(rows)
        if rows:
            state["cursor"] = rows[-1][0]
        if len(rows) < size:
            state["state"] = "ready"
        _save_history_checkpoint_no_tx(store, job)
        if state["state"] == "ready":
            conn.execute("UPDATE analysis_jobs SET status='completed',finished_at=? WHERE job_id=?", (now_utc(), job_id))
    return state


def _refresh_message_fts_no_tx(conn: sqlite3.Connection, channel_id: int, message_id: int) -> None:
    if not _message_fts_enabled(conn):
        _invalidate_fts_index_no_tx(conn, "writer_without_index")
        return
    conn.execute("""INSERT OR REPLACE INTO messages_fts(rowid,channel_id,message_id,text,ocr_text)
        SELECT m.rowid,m.channel_id,m.message_id,m.text,
            COALESCE((SELECT group_concat(ocr_text,char(10)) FROM
                (SELECT ocr_text FROM media_assets WHERE channel_id=m.channel_id AND message_id=m.message_id
                 AND ocr_text IS NOT NULL ORDER BY ordinal)), '')
        FROM messages m WHERE m.channel_id=? AND m.message_id=?""", (channel_id, message_id))


def delete_history_message(conn: sqlite3.Connection, channel_id: int, message_id: int) -> bool:
    with common_sqlite.write_tx(conn):
        if _message_fts_enabled(conn):
            conn.execute("""DELETE FROM messages_fts WHERE rowid=(
                SELECT rowid FROM messages WHERE channel_id=? AND message_id=?)""", (channel_id, message_id))
        else:
            _invalidate_fts_index_no_tx(conn, "writer_without_index")
        return conn.execute("DELETE FROM messages WHERE channel_id=? AND message_id=?", (channel_id, message_id)).rowcount == 1


def delete_media_asset(conn: sqlite3.Connection, channel_id: int, message_id: int, ordinal: int) -> bool:
    with common_sqlite.write_tx(conn):
        changed = conn.execute("DELETE FROM media_assets WHERE channel_id=? AND message_id=? AND ordinal=?",
                               (channel_id, message_id, ordinal)).rowcount == 1
        if changed:
            _refresh_message_fts_no_tx(conn, channel_id, message_id)
        return changed


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


def migrate_ai_usage_log_schema(conn: sqlite3.Connection) -> None:
    ai_usage_columns = set(table_columns(conn, "ai_usage_log"))
    ai_usage_additions = {
        "response_id": "TEXT",
        "prompt_cache_key": "TEXT",
        "prompt_cache_retention": "TEXT",
        "prompt_version_hash": "TEXT",
        "system_chars": "INTEGER",
        "prompt_chars": "INTEGER",
        "shared_prefix_chars": "INTEGER",
        "shared_prefix_hash": "TEXT",
        "prompt_hash": "TEXT",
        "previous_prompt_hash": "TEXT",
        "previous_response_id": "TEXT",
        "prefix_match_chars_with_previous": "INTEGER",
        "prompt_text": "TEXT",
        "cache_write_tokens": "INTEGER",
        "reasoning_tokens": "INTEGER",
        "output_chars": "INTEGER",
        "response_status": "TEXT",
        "incomplete_reason": "TEXT",
        "job_id": "TEXT",
        "batch_id": "TEXT",
        "attempt_id": "TEXT",
    }
    for column_name, column_type in ai_usage_additions.items():
        if ai_usage_columns and column_name not in ai_usage_columns:
            conn.execute(f"ALTER TABLE ai_usage_log ADD COLUMN {column_name} {column_type}")


def migrate_sqlite_schema(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "messages")
    needs_messages_rebuild = bool(columns) and (
        "post_author" in columns or "sender_username" not in columns or "sender_display_name" not in columns
    )
    if not needs_messages_rebuild:
        conn.execute("UPDATE channels SET access_hash = NULL WHERE access_hash = ''")
        conn.execute("UPDATE messages SET grouped_id = NULL WHERE grouped_id = ''")
        conn.execute("UPDATE messages SET sender_id = NULL WHERE sender_id = ''")
        migrate_ai_usage_log_schema(conn)
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel_date ON messages(channel_id, date_utc DESC, message_id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_assets_ocr_status ON media_assets(ocr_status, created_at)")
    conn.execute("UPDATE channels SET access_hash = NULL WHERE access_hash = ''")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_ai_usage_log_schema(conn)


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


def canonical_session_identity(runtime: RuntimeConfig, auth_mode: str) -> Path:
    path = str(make_session_path(runtime, auth_mode))
    # SQLiteSession appends .session, rather than replacing an existing suffix.
    return Path(path if path.endswith(".session") else path + ".session").resolve()


async def _acquire_session_lock(runtime: RuntimeConfig, auth_mode: str) -> int:
    identity = canonical_session_identity(runtime, auth_mode)
    identity.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(identity) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = common_process.continuous_time() + runtime.connection_timeout_seconds
    interval = common_process.load_process_config().poll_interval_seconds
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                remaining = deadline - common_process.continuous_time()
                if remaining <= 0:
                    raise TimeoutError("Telegram session is already in use") from None
                await asyncio.sleep(min(interval, remaining))
    except BaseException:
        os.close(fd)
        raise


@asynccontextmanager
async def open_telethon_session(runtime: RuntimeConfig, auth_mode: str, *, managed_requests: bool = False):
    fd = await _acquire_session_lock(runtime, auth_mode)
    release = True
    try:
        try:
            client = (await open_telethon_client(runtime, auth_mode, managed_requests=True)
                      if managed_requests else await open_telethon_client(runtime, auth_mode))
        except TelegramConnectionError as exc:
            if isinstance(exc.cause, AsyncCleanupError):
                release = False
            raise
        try:
            try:
                if not managed_requests:
                    await await_with_awake_timeout(client.__aenter__(), runtime.connection_timeout_seconds,
                                                   cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds)
            except AsyncCleanupError:
                release = False
                raise
            try:
                yield client
            except AsyncCleanupError:
                release = False
                raise
        finally:
            try:
                cleanup = client.disconnect() if managed_requests else client.__aexit__(*sys.exc_info())
                await await_with_awake_timeout(cleanup, runtime.connection_cleanup_timeout_seconds,
                                               cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds)
            except BaseException as exc:
                release = False
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise AsyncCleanupError("Telegram session cleanup did not complete") from None
    finally:
        if release:
            os.close(fd)
        # On uncertain cleanup keep this raw OS descriptor owned until the fatal
        # worker exits. Releasing it would permit another still-overlapping client.


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


def build_entity_lookup_reference(channel: str) -> Any:
    normalized = channel.strip()
    if normalized.startswith("-100") and normalized[4:].isdigit():
        try:
            from telethon.tl.types import PeerChannel
        except ImportError:
            return normalized
        return PeerChannel(int(normalized[4:]))
    return normalized


class TelegramConnectionError(AsyncRetryError):
    def operator_summary(self) -> str:
        cause = self.cause
        if isinstance(cause, TimeoutError):
            reason = "timeout"
        elif isinstance(cause, AsyncCleanupError):
            reason = "cleanup_error"
        elif isinstance(cause, OSError) and cause.errno is not None:
            reason = f"{type(cause).__name__}: {errno.errorcode.get(cause.errno, cause.errno)}"
        else:
            reason = type(cause).__name__
        return f"Telegram connection {reason} (attempts: {self.attempts}/{self.max_attempts})"


def is_retryable_connection_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        return exc.errno in RETRYABLE_CONNECTION_ERRNOS or (
            isinstance(exc, ConnectionError) and exc.errno is None
        )
    return False


async def open_telethon_client(runtime: RuntimeConfig, auth_mode: str, *, managed_requests: bool = False) -> Any:
    if not runtime.api_id or not runtime.api_hash:
        raise SystemExit(
            "Missing Telethon credentials. Fill [telethon].api_id and [secrets].api_hash in runtime.local.toml."
        )

    TelegramClient = require_telethon()
    if auth_mode == "bot":
        if not runtime.bot_token:
            raise SystemExit("Missing bot token for bot auth mode. Fill [secrets].bot_token in runtime.local.toml.")
        start_args = {"bot_token": runtime.bot_token}
    else:
        if not runtime.phone:
            raise SystemExit("Missing phone for user auth mode. Fill [telethon].phone in runtime.local.toml.")
        start_args = {"phone": runtime.phone, "password": runtime.user_password or None}

    attempt_started = 0.0

    async def connect_once() -> Any:
        nonlocal attempt_started
        attempt_started = awake_time()
        # Analytics owns page retries, FloodWait and the outer TTL. Zero disables
        # SDK retry multiplication; legacy callers retain Telethon defaults.
        options = {"request_retries": 0, "connection_retries": 0, "retry_delay": 0,
                   "auto_reconnect": False, "flood_sleep_threshold": 0,
                   "raise_last_call_error": True, "receive_updates": False} if managed_requests else {}
        client = TelegramClient(str(make_session_path(runtime, auth_mode)), int(runtime.api_id), runtime.api_hash, **options)

        async def start_client():
            if managed_requests and auth_mode == "user":
                # A background analytics task may reuse an authenticated session,
                # but must not initiate phone/OTP prompts through .start().
                await client.connect()
                if await client.get_me() is None:
                    raise PermissionError("Telegram user session is not authorized")
            else:
                await client.start(**start_args)

        try:
            await await_with_awake_timeout(
                start_client(), runtime.connection_timeout_seconds,
                cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds,
            )
            return client
        except BaseException:
            try:
                await await_with_awake_timeout(
                    client.disconnect(), runtime.connection_cleanup_timeout_seconds,
                    cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds,
                )
            except Exception as cleanup_exc:
                raise AsyncCleanupError("Telegram disconnect did not complete") from cleanup_exc
            raise

    def log_failure(exc: Exception, attempt: int) -> None:
        summary = TelegramConnectionError(exc, attempt, runtime.connection_attempts).operator_summary()
        print(f"{summary}; awake_elapsed={awake_time() - attempt_started:.1f}s", file=sys.stderr, flush=True)

    def retryable_startup(exc: Exception) -> bool:
        if is_retryable_connection_error(exc):
            return True
        if managed_requests:
            from telethon import errors
            return isinstance(exc, (errors.ServerError, errors.InvalidDCError))
        return False

    try:
        return await retry_async(
            connect_once, max_attempts=runtime.connection_attempts,
            backoff_seconds=runtime.connection_retry_backoff_seconds,
            is_retryable=retryable_startup, on_failure=log_failure,
            on_recovery=lambda attempt: print(
                f"Telegram connection recovered (attempts: {attempt}/{runtime.connection_attempts})",
                file=sys.stderr, flush=True,
            ),
        )
    except AsyncRetryError as exc:
        raise TelegramConnectionError(exc.cause, exc.attempts, exc.max_attempts) from None


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


def parse_message_url(url: str) -> tuple[str, int]:
    raw = url.strip()
    if not raw:
        raise SystemExit("Telegram message URL is required.")
    normalized = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(normalized)
    if parsed.netloc not in {"t.me", "www.t.me"}:
        raise SystemExit(f"Unsupported Telegram URL: {url}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[1].isdigit():
        return f"@{parts[0]}", int(parts[1])
    if len(parts) == 3 and parts[0] == "s" and parts[2].isdigit():
        return f"@{parts[1]}", int(parts[2])
    if len(parts) == 3 and parts[0] == "c" and parts[1].isdigit() and parts[2].isdigit():
        return f"-100{parts[1]}", int(parts[2])
    raise SystemExit(f"Unsupported Telegram message URL format: {url}")


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


def extract_message_links(message: Any) -> list[dict[str, str]]:
    text = message_text(message)
    entities = getattr(message, "entities", None) or []
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entity in entities:
        url = optional_text(getattr(entity, "url", None))
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        label = None
        if length > 0:
            text_range = utf16_entity_range_to_python_range(text, offset, length)
            if text_range is None:
                continue
            start, end = text_range
            raw_label = text[start:end]
            label = optional_text(raw_label.splitlines()[0])
        if url is None and label and label.startswith(("http://", "https://")):
            url = label
        if url is None:
            continue
        effective_label = label or url
        item = (effective_label, url)
        if item in seen:
            continue
        seen.add(item)
        links.append({"label": effective_label, "url": url})
    return links


def escape_markdown_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def build_utf16_offset_map(text: str) -> dict[int, int]:
    offsets = {0: 0}
    utf16_units = 0
    for index, char in enumerate(text):
        utf16_units += 2 if ord(char) > 0xFFFF else 1
        offsets[utf16_units] = index + 1
    return offsets


def utf16_entity_range_to_python_range(text: str, offset: int, length: int) -> tuple[int, int] | None:
    if offset < 0 or length <= 0:
        return None
    utf16_offsets = build_utf16_offset_map(text)
    end_offset = offset + length
    if offset not in utf16_offsets or end_offset not in utf16_offsets:
        return None
    return utf16_offsets[offset], utf16_offsets[end_offset]


def render_message_body_with_inline_links(message: Any) -> tuple[str, list[dict[str, str]]]:
    text = message_text(message)
    entities = getattr(message, "entities", None) or []
    link_entities: list[tuple[int, int, str, str]] = []
    fallback_links: list[dict[str, str]] = []
    seen_fallbacks: set[tuple[str, str]] = set()

    for entity in entities:
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        text_range = utf16_entity_range_to_python_range(text, offset, length)
        if text_range is None:
            continue
        start, end = text_range
        raw_label = text[start:end]
        url = optional_text(getattr(entity, "url", None))
        label = optional_text(raw_label)
        if url is None and label and label.startswith(("http://", "https://")):
            url = label
        if url is None:
            continue
        if "\n" in raw_label:
            item = (raw_label.splitlines()[0].strip() or url, url)
            if item not in seen_fallbacks:
                seen_fallbacks.add(item)
                fallback_links.append({"label": item[0], "url": item[1]})
            continue
        link_entities.append((start, end, raw_label, url))

    if not link_entities:
        return text, fallback_links

    rendered_parts: list[str] = []
    cursor = 0
    for start, end, raw_label, url in sorted(link_entities, key=lambda item: (item[0], item[1])):
        if start < cursor:
            item = (raw_label.strip() or url, url)
            if item not in seen_fallbacks:
                seen_fallbacks.add(item)
                fallback_links.append({"label": item[0], "url": item[1]})
            continue
        rendered_parts.append(text[cursor:start])
        label = escape_markdown_link_label(raw_label)
        rendered_parts.append(f"[{label}]({url})")
        cursor = end
    rendered_parts.append(text[cursor:])
    return "".join(rendered_parts), fallback_links


def channel_dir(runtime: RuntimeConfig, channel_id: int) -> Path:
    path = runtime.media_root / str(channel_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def message_subject_line(message: Any) -> str:
    for line in message_text(message).splitlines():
        candidate = optional_text(line)
        if candidate:
            return candidate
    return f"message {getattr(message, 'id', 'unknown')}"


def compose_single_message_title(entity: Any, message: Any) -> str:
    channel_name = entity_display_name(entity) or optional_text(getattr(entity, "username", None)) or "Telegram"
    subject = message_subject_line(message)
    if subject.casefold().startswith(channel_name.casefold()):
        return subject
    return f"{channel_name} - {subject}"


def sanitize_note_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned or "telegram-message"


def render_single_message_source(entity: Any, message: Any, source_url: str) -> str:
    channel_name = entity_display_name(entity) or "Unknown channel"
    username = optional_text(getattr(entity, "username", None))
    channel_ref = f"@{username}" if username else str(getattr(entity, "id", "unknown"))
    date_utc = serialize_datetime(getattr(message, "date", None)) or "unknown"
    body, fallback_links = render_message_body_with_inline_links(message)
    body = body.strip() or "(message has no text body)"
    lines = [
        f"# {compose_single_message_title(entity, message)}",
        "",
        f"Source URL: {source_url}",
        f"Source type: telegram_message",
        f"Channel: {channel_name} ({channel_ref})",
        f"Message ID: {getattr(message, 'id', 'unknown')}",
        f"Date UTC: {date_utc}",
        "",
        "## Message Text",
        "",
        body,
    ]
    if fallback_links:
        lines.extend(
            [
                "",
                "## Unresolved Links",
                "",
            ]
        )
        for link in fallback_links:
            lines.append(f"- [{link['label']}]({link['url']})")
    return "\n".join(lines) + "\n"


def export_single_message_source(
    entity: Any,
    message: Any,
    source_url: str,
    output_dir: str | None,
) -> Path:
    base_dir = Path(output_dir).expanduser() if output_dir else SINGLE_MESSAGE_EXPORT_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    title = compose_single_message_title(entity, message)
    file_name = f"{sanitize_note_filename(title)} [tg-{getattr(message, 'id', 'unknown')}].md"
    output_path = base_dir / file_name
    output_path.write_text(render_single_message_source(entity, message, source_url), encoding="utf-8")
    return output_path


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
    reply_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
    if type(reply_id) is int and reply_id > 0:
        payload["reply_to"] = {"reply_to_msg_id": reply_id}
        peer = getattr(message.reply_to, "reply_to_peer_id", None)
        if peer is not None:
            identity = {key: getattr(peer, key) for key in ("channel_id", "chat_id", "user_id")
                        if type(getattr(peer, key, None)) is int}
            payload["reply_to"]["reply_to_peer_id"] = identity or {"type": type(peer).__name__}
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


class TelegramPeerIdentityError(ValueError):
    """The legacy numeric storage key is already owned by another peer kind."""


def _telegram_peer_kind(name: str) -> str | None:
    return {"channel": "channel", "channelforbidden": "channel",
            "chat": "chat", "chatempty": "chat", "chatforbidden": "chat",
            "user": "user", "userempty": "user"}.get(name.casefold())


def upsert_channel(conn: sqlite3.Connection, entity: Any) -> None:
    now = now_utc()
    with common_sqlite.write_tx(conn):
        _upsert_channel_no_tx(conn, entity, now)


def _upsert_channel_no_tx(conn: sqlite3.Connection, entity: Any, now: str | None = None) -> None:
    effective_now = now or now_utc()
    kind = _telegram_peer_kind(type(entity).__name__)
    previous = conn.execute("SELECT channel_type FROM channels WHERE channel_id=?", (entity.id,)).fetchone()
    previous_kind = _telegram_peer_kind(previous[0]) if previous else None
    if kind is not None and previous_kind is not None and kind != previous_kind:
        raise TelegramPeerIdentityError("Telegram peer identifier belongs to another peer kind")
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
            effective_now,
            effective_now,
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
    with common_sqlite.write_tx(conn):
        _upsert_message_no_tx(conn, entity, message, sender_username, sender_display_name, downloaded_path, downloaded_size)


def _upsert_message_no_tx(
    conn: sqlite3.Connection,
    entity: Any,
    message: Any,
    sender_username: str | None,
    sender_display_name: str | None,
    downloaded_path: str | None,
    downloaded_size: int | None,
) -> None:
    prepared = prepare_history_write(StagedMessageWrite(
        entity, message, sender_username, sender_display_name, downloaded_path, downloaded_size,
    ))
    _upsert_prepared_message_no_tx(conn, prepared)


def prepare_history_write(item: StagedMessageWrite) -> dict[str, Any]:
    """Normalize once so a bounded pending page can survive process restart."""
    entity, message = item.entity, item.message
    text = message_text(message)
    media_kind = message_media_kind(message)
    imported_at = now_utc()
    content_hash = f"{getattr(message, 'id')}:{serialize_datetime(getattr(message, 'edit_date', None))}:{len(text)}:{media_kind or '-'}"
    grouped_id = getattr(message, "grouped_id", None)
    values = [
        entity.id, message.id, str(grouped_id) if grouped_id is not None else None,
        serialize_datetime(getattr(message, "date", None)) or imported_at,
        serialize_datetime(getattr(message, "edit_date", None)), optional_text(getattr(message, "sender_id", None)),
        optional_text(item.sender_username), optional_text(item.sender_display_name), text,
        getattr(message, "views", None), getattr(message, "forwards", None),
        getattr(getattr(message, "replies", None), "replies", None),
        1 if getattr(message, "media", None) else 0, media_kind,
        minimal_message_json(message, media_kind, text), content_hash, imported_at,
    ]
    media = None
    if item.downloaded_path or media_kind:
        media = [entity.id, message.id, media_kind or "media", optional_text(item.downloaded_path),
                 optional_text(media_kind), item.downloaded_size,
                 "pending" if item.downloaded_path else "skipped", imported_at]
    return {"message": values, "media": media}


def _upsert_prepared_message_no_tx(conn: sqlite3.Connection, prepared: dict[str, Any]) -> None:
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
        prepared["message"],
    )
    if prepared["media"] is not None:
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
            prepared["media"],
        )
    elif not prepared["message"][12]:
        # A confirmed media removal must not leave old OCR attached to the new
        # source revision. Local files remain operator-owned artifacts.
        conn.execute("DELETE FROM media_assets WHERE channel_id=? AND message_id=?", tuple(prepared["message"][:2]))
    _refresh_message_fts_no_tx(conn, prepared["message"][0], prepared["message"][1])


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
    with common_sqlite.write_tx(conn):
        _update_sync_state_no_tx(
            conn,
            channel_id,
            last_backfill_message_id=last_backfill_message_id,
            last_tail_message_id=last_tail_message_id,
            last_tail_at=last_tail_at,
            last_full_sync_at=last_full_sync_at,
            last_error=last_error,
        )


def _update_sync_state_no_tx(
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


def flush_staged_message_writes(conn: sqlite3.Connection, staged_writes: list[StagedMessageWrite]) -> None:
    if not staged_writes:
        return
    with common_sqlite.write_tx(conn):
        for item in staged_writes:
            _upsert_message_no_tx(
                conn,
                item.entity,
                item.message,
                item.sender_username,
                item.sender_display_name,
                item.downloaded_path,
                item.downloaded_size,
            )


# Telethon's GetHistory iterator uses physical chunks of at most 100 messages.
TELEGRAM_HISTORY_PAGE_LIMIT = 100


def _active_analysis_history_source(store, job_id: str, generation: int, channel_id: int):
    if __package__:
        from .analysis_store import StaleGenerationError
    else:
        from analysis_store import StaleGenerationError
    job = store.get_job(job_id)
    if job is None or job["generation"] != generation or job["status"] != "running":
        raise StaleGenerationError("Analysis history generation changed")
    if job["cancel_requested"]:
        raise InterruptedError("Analysis history cancelled")
    source = job["checkpoint"].get("history", {}).get("sources", {}).get(str(channel_id))
    if not isinstance(source, dict) or source.get("mode") not in {"backfill", "update"}:
        raise ValueError("Invalid analysis history source plan")
    return job, source


def _analysis_download_cap(settings: dict[str, Any]) -> int:
    cap, sync_cap = settings["analysis"]["max_messages"], settings["sync"]["sync_limit"]
    return min(cap, sync_cap) if sync_cap > 0 else cap


def _save_history_checkpoint_no_tx(store, job, *, downloaded_delta=0, downloaded_release=0):
    if __package__:
        from .analysis_store import encode_payload
    else:
        from analysis_store import encode_payload
    encoded = encode_payload(job["checkpoint"], max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
    store._write_checkpoint_no_tx(job["job_id"], encoded, generation=job["generation"],
                                  downloaded_delta=downloaded_delta, downloaded_release=downloaded_release)


def reserve_analysis_history_page(store, job_id: str, *, generation: int, channel_id: int, limit: int):
    if type(limit) is not int or limit < 1:
        raise ValueError("Invalid history page limit")
    with common_sqlite.write_tx(store.conn, immediate=True):
        job, source = _active_analysis_history_source(store, job_id, generation, channel_id)
        if "page" in source:
            raise ValueError("A received history page is still pending")
        if source["state"] == "complete":
            return None
        previous = source.pop("reservation", None)
        if previous is not None:
            history = job["checkpoint"]["history"]
            history["unknown_download_messages"] = history.get("unknown_download_messages", 0) + previous["allowance"]
        settings = job["request"]["settings"]
        remaining = _analysis_download_cap(settings) - job["downloaded_messages"]
        if remaining <= 0:
            source.update(state="partial", reason="download_cap")
            _save_history_checkpoint_no_tx(store, job)
            return None
        allowance = min(limit, settings["analysis"]["history_page_size"], TELEGRAM_HISTORY_PAGE_LIMIT, remaining)
        reservation = {"token": uuid.uuid4().hex, "allowance": allowance, "cursor": source["cursor"]}
        source["reservation"] = reservation
        source["state"] = "running"
        _save_history_checkpoint_no_tx(store, job, downloaded_delta=allowance)
        return reservation


def stage_analysis_history_page(store, job_id: str, *, generation: int, channel_id: int,
                                token: str, items: list[dict[str, Any]], complete: bool):
    if __package__:
        from .analysis_store import StaleGenerationError
    else:
        from analysis_store import StaleGenerationError
    if not isinstance(items, list) or type(complete) is not bool or (not items and not complete):
        raise ValueError("Invalid received history page")
    with common_sqlite.write_tx(store.conn, immediate=True):
        job, source = _active_analysis_history_source(store, job_id, generation, channel_id)
        reservation = source.get("reservation")
        if not reservation or reservation["token"] != token or reservation["cursor"] != source["cursor"]:
            raise StaleGenerationError("Analysis history reservation changed")
        if len(items) > reservation["allowance"]:
            raise ValueError("Received history page exceeds its allowance")
        previous = source["cursor"] or None
        for item in items:
            message_id = item.get("id")
            row = item.get("row")
            if type(message_id) is not int or message_id < 1 or (previous is not None and message_id >= previous):
                raise ValueError("History page IDs must be strictly descending")
            previous = message_id
            if row is not None and (not isinstance(row, dict) or set(row) != {"message", "media"}
                    or not isinstance(row["message"], list) or len(row["message"]) != 17
                    or row["message"][:2] != [channel_id, message_id]
                    or (row["media"] is not None and (not isinstance(row["media"], list) or len(row["media"]) != 8
                                                       or row["media"][:2] != [channel_id, message_id]))):
                raise ValueError("Invalid prepared history message")
        source["page"] = {"items": items, "position": 0, "complete": complete}
        source["returned_messages"] = source.get("returned_messages", 0) + len(items)
        del source["reservation"]
        _save_history_checkpoint_no_tx(store, job, downloaded_release=reservation["allowance"] - len(items))


def _mark_history_source_complete_no_tx(conn, entity, source):
    now = now_utc()
    previous = conn.execute("SELECT last_backfill_message_id,last_tail_message_id FROM sync_state WHERE channel_id=?", (entity.id,)).fetchone()
    backfill = source["mode"] == "backfill"
    old_id = previous[0 if backfill else 1] if previous else None
    highest = max(old_id or 0, source.get("highest_seen_id") or 0) or None
    _update_sync_state_no_tx(conn, entity.id,
                             last_backfill_message_id=highest if backfill else None,
                             last_tail_message_id=highest if not backfill else None,
                             last_full_sync_at=now if backfill else None,
                             last_tail_at=now if not backfill else None)


def drain_analysis_history_page(store, job_id: str, *, generation: int, entity: Any):
    with common_sqlite.write_tx(store.conn, immediate=True):
        job, source = _active_analysis_history_source(store, job_id, generation, entity.id)
        pending = source.get("page")
        if pending is None:
            return source
        start = pending["position"]
        batch = max(1, job["request"]["settings"]["sync"]["batch_size"])
        part = pending["items"][start:start + batch]
        _upsert_channel_no_tx(store.conn, entity)
        for item in part:
            if item["row"] is not None:
                _upsert_prepared_message_no_tx(store.conn, item["row"])
                source["highest_seen_id"] = max(source.get("highest_seen_id") or 0, item["id"])
        if part:
            source["cursor"] = part[-1]["id"]
        pending["position"] += len(part)
        if pending["position"] >= len(pending["items"]):
            if pending["complete"]:
                source["state"] = "complete"
                _mark_history_source_complete_no_tx(store.conn, entity, source)
            del source["page"]
        _save_history_checkpoint_no_tx(store, job)
    return source


class AnalysisHistoryRequestError(RuntimeError):
    def __init__(self, reason: str, *, state: str = "partial"):
        super().__init__(reason)
        self.reason, self.state = reason, state


def _analysis_source_access_error(entity):
    name = type(entity).__name__
    if name in {"ChatForbidden", "ChannelForbidden"}:
        return AnalysisHistoryRequestError("source_unavailable", state="failed")
    if _telegram_peer_kind(name) == "user":
        return AnalysisHistoryRequestError("unsupported_source_type", state="failed")
    return None


def _analysis_history_job(store, job_id, generation):
    if __package__:
        from .analysis_store import StaleGenerationError
    else:
        from analysis_store import StaleGenerationError
    job = store.get_job(job_id)
    if job is None or job["generation"] != generation or job["status"] != "running":
        raise StaleGenerationError("Analysis history generation changed")
    if job["cancel_requested"]:
        raise InterruptedError("Analysis history cancelled")
    return job


def _begin_analysis_source(store, job_id, generation, channel, entity, mode):
    if mode not in {"backfill", "update"}:
        raise ValueError("Invalid analysis history mode")
    with common_sqlite.write_tx(store.conn, immediate=True):
        job = _analysis_history_job(store, job_id, generation)
        history = job["checkpoint"].setdefault("history", {})
        refs = history.setdefault("refs", {})
        if channel in refs and refs[channel] != entity.id:
            raise ValueError("Analysis source identity changed")
        refs[channel] = entity.id
        sources = history.setdefault("sources", {})
        source = sources.get(str(entity.id))
        kind = _telegram_peer_kind(type(entity).__name__)
        if source is not None and source.get("peer_kind") is not None and kind is not None and source["peer_kind"] != kind:
            raise TelegramPeerIdentityError("Analysis source peer kind changed")
        if source is None:
            source = {"mode": mode, "stop_id": latest_stored_message_id(store.conn, entity.id) if mode == "update" else None,
                      "cursor": 0, "state": "running", "returned_messages": 0, "peer_kind": kind}
            sources[str(entity.id)] = source
        elif source["mode"] != mode:
            raise ValueError("Analysis source mode changed")
        elif source["state"] != "complete":
            source["state"] = "running"
            source.pop("reason", None)
        _upsert_channel_no_tx(store.conn, entity)
        _save_history_checkpoint_no_tx(store, job)
    return source


def _record_analysis_source_error(store, job_id, generation, channel, error, channel_id=None):
    with common_sqlite.write_tx(store.conn, immediate=True):
        job = _analysis_history_job(store, job_id, generation)
        history = job["checkpoint"].setdefault("history", {})
        channel_id = channel_id if channel_id is not None else history.get("refs", {}).get(channel)
        source = history.get("sources", {}).get(str(channel_id))
        if source is None:
            source = {"state": error.state, "reason": error.reason, "channel": channel}
            history.setdefault("failed_refs", {})[channel] = source
        else:
            source.update(state=error.state, reason=error.reason)
        _save_history_checkpoint_no_tx(store, job)
    return source


def _settle_analysis_page_failure(store, job_id, generation, channel_id, reservation, *, known_count=None):
    if __package__:
        from .analysis_store import StaleGenerationError
    else:
        from analysis_store import StaleGenerationError
    with common_sqlite.write_tx(store.conn, immediate=True):
        job, source = _active_analysis_history_source(store, job_id, generation, channel_id)
        current = source.get("reservation")
        if not current or current["token"] != reservation["token"]:
            raise StaleGenerationError("Analysis history reservation changed")
        allowance = current["allowance"]
        if known_count is not None and not 0 <= known_count <= allowance:
            raise ValueError("Invalid received history count")
        del source["reservation"]
        if known_count is None:
            history = job["checkpoint"]["history"]
            history["unknown_download_messages"] = history.get("unknown_download_messages", 0) + allowance
        else:
            source["returned_messages"] = source.get("returned_messages", 0) + known_count
        _save_history_checkpoint_no_tx(store, job, downloaded_release=0 if known_count is None else allowance - known_count)


async def _analysis_rpc_retry(store, job_id, generation, settings, deadline, operation, *, before=None, failed=None):
    from telethon import errors
    attempts = settings["analysis"]["telegram_retry_attempts"]
    backoff = settings["telethon"]["connection_retry_backoff_seconds"]
    for attempt in range(attempts):
        if store.worker_stop_requested(job_id, generation=generation):
            raise InterruptedError("Analysis history cancelled")
        remaining = deadline - common_process.continuous_time()
        if remaining <= 0:
            raise AnalysisHistoryRequestError("run_deadline")
        context = before() if before is not None else None
        if before is not None and context is None:
            raise AnalysisHistoryRequestError("download_cap")
        try:
            timeout = min(settings["analysis"]["telegram_request_timeout_seconds"], remaining)
            result = await await_with_awake_timeout(operation(context), timeout,
                                                    cleanup_timeout_seconds=settings["telethon"]["connection_cleanup_timeout_seconds"])
            return result, context
        except (errors.RPCError, OSError, TimeoutError, ValueError) as exc:
            if failed is not None:
                failed(context, known_count=0 if isinstance(exc, errors.RPCError) else None)
            if isinstance(exc, errors.UnauthorizedError):
                raise
            remaining = deadline - common_process.continuous_time()
            if remaining <= 0:
                raise AnalysisHistoryRequestError("run_deadline") from None
            flood = isinstance(exc, (errors.FloodWaitError, errors.FloodPremiumWaitError,
                                     errors.SlowModeWaitError, errors.FloodTestPhoneWaitError))
            transient = flood or isinstance(exc, (errors.ServerError, errors.InvalidDCError)) or is_retryable_connection_error(exc)
            if not transient:
                raise AnalysisHistoryRequestError("source_unavailable", state="failed") from None
            if attempt + 1 >= attempts:
                raise AnalysisHistoryRequestError("request_retries_exhausted") from None
            delay = max(backoff * (2 ** attempt), exc.seconds if flood else 0)
            if delay >= remaining:
                raise AnalysisHistoryRequestError("flood_wait_exceeds_deadline" if flood else "retry_exceeds_deadline") from None
            until = common_process.continuous_time() + delay
            while common_process.continuous_time() < until:
                if store.worker_stop_requested(job_id, generation=generation):
                    raise InterruptedError("Analysis history cancelled")
                await asyncio.sleep(min(common_process.load_process_config().poll_interval_seconds,
                                        max(0, until - common_process.continuous_time())))
    raise AnalysisHistoryRequestError("request_retries_exhausted")


async def resolve_channel_entity(client, channel: str):
    reference = build_entity_lookup_reference(channel)
    try:
        return await client.get_entity(reference)
    except ValueError:
        if not (channel.startswith("-100") and channel[1:].isdigit()):
            raise
        await client.get_dialogs()
        return await client.get_entity(reference)


async def sync_analysis_source(store, job_id: str, runtime: RuntimeConfig, *, generation: int,
                                channel: str, mode: str = "backfill", client=None, deadline=None, resolved_entity=None):
    job = _analysis_history_job(store, job_id, generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    if channel not in request["channels"]:
        raise ValueError("Analysis channel is outside the admitted scope")
    deadline = deadline if deadline is not None else common_process.continuous_time() + settings["bridge"]["worker_process_timeout_seconds"]
    profile = replace(runtime, **settings["telethon"], default_auth_mode=settings["auth"]["default_mode"],
                      public_auth_mode=settings["auth"]["public_channel_mode"], private_auth_mode=settings["auth"]["private_channel_mode"])
    if client is None:
        auth = resolve_auth_mode(profile, profile.default_auth_mode, channel)
        async with open_telethon_session(profile, auth, managed_requests=True) as owned_client:
            return await sync_analysis_source(store, job_id, profile, generation=generation, channel=channel,
                                              mode=mode, client=owned_client, deadline=deadline)
    entity = resolved_entity
    if entity is None:
        try:
            entity, _ = await _analysis_rpc_retry(store, job_id, generation, settings, deadline,
                                                  lambda _: resolve_channel_entity(client, channel))
        except AnalysisHistoryRequestError as exc:
            return _record_analysis_source_error(store, job_id, generation, channel, exc)
    access_error = _analysis_source_access_error(entity)
    if access_error is not None:
        return _record_analysis_source_error(store, job_id, generation, channel, access_error)
    try:
        source = _begin_analysis_source(store, job_id, generation, channel, entity, mode)
    except TelegramPeerIdentityError:
        return _record_analysis_source_error(store, job_id, generation, channel,
                                              AnalysisHistoryRequestError("source_identity_conflict", state="failed"))
    since = max(datetime.fromisoformat(request["since"]), datetime.fromisoformat(source.get("since", request["since"])))
    upper = min(datetime.fromisoformat(request["until"]), datetime.fromisoformat(request["cutoff_at"]),
                datetime.fromisoformat(source.get("until", request["until"])))
    offset_date = upper.replace(microsecond=0) + timedelta(seconds=1)
    while source["state"] != "complete":
        if common_process.continuous_time() >= deadline:
            return _record_analysis_source_error(store, job_id, generation, channel,
                                                  AnalysisHistoryRequestError("run_deadline"), entity.id)
        if "page" in source:
            try:
                source = drain_analysis_history_page(store, job_id, generation=generation, entity=entity)
            except TelegramPeerIdentityError:
                return _record_analysis_source_error(store, job_id, generation, channel,
                                                      AnalysisHistoryRequestError("source_identity_conflict", state="failed"), entity.id)
            continue

        def reserve():
            return reserve_analysis_history_page(store, job_id, generation=generation, channel_id=entity.id,
                                                   limit=settings["analysis"]["history_page_size"])

        def failed(reservation, **kwargs):
            return _settle_analysis_page_failure(store, job_id, generation, entity.id, reservation, **kwargs)

        try:
            messages, reservation = await _analysis_rpc_retry(
                store, job_id, generation, settings, deadline,
                lambda reserved: client.get_messages(entity, limit=reserved["allowance"],
                                                       offset_id=reserved["cursor"], offset_date=offset_date, wait_time=0),
                before=reserve, failed=failed,
            )
        except AnalysisHistoryRequestError as exc:
            return _record_analysis_source_error(store, job_id, generation, channel, exc, entity.id)
        items, complete = [], not messages
        try:
            for message in messages:
                date = getattr(message, "date", None)
                if not isinstance(date, datetime):
                    raise ValueError("History message date is unavailable")
                date = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
                selected = since <= date <= upper
                if date < since or (source["stop_id"] is not None and message.id <= source["stop_id"]):
                    selected, complete = False, True
                row = None
                if selected:
                    try:
                        sender, _ = await _analysis_rpc_retry(store, job_id, generation, settings, deadline,
                                                               lambda _: resolve_sender_metadata(entity, message))
                        username, display_name = sender
                    except AnalysisHistoryRequestError as exc:
                        if exc.reason == "run_deadline":
                            raise
                        username, display_name = direct_sender_metadata(message)
                    media_path, media_size = None, None
                    if (settings["processing"]["ocr"] and is_image_media_kind(message_media_kind(message))
                            and media_needs_download(store.conn, entity.id, message.id)):
                        media, _ = await _analysis_rpc_retry(store, job_id, generation, settings, deadline,
                                                              lambda _: download_media_if_present(profile, client, message, entity.id))
                        media_path, media_size = media
                    row = prepare_history_write(StagedMessageWrite(entity, message, username, display_name, media_path, media_size))
                items.append({"id": message.id, "row": row})
        except AnalysisHistoryRequestError as exc:
            failed(reservation, known_count=len(messages))
            return _record_analysis_source_error(store, job_id, generation, channel, exc, entity.id)
        stage_analysis_history_page(store, job_id, generation=generation, channel_id=entity.id,
                                     token=reservation["token"], items=items, complete=complete)
        source = store.get_job(job_id)["checkpoint"]["history"]["sources"][str(entity.id)]
    return source


def _analysis_history_bounds(request):
    return (datetime.fromisoformat(request["since"]),
            min(datetime.fromisoformat(request["until"]), datetime.fromisoformat(request["cutoff_at"])))


def _coalesce_analysis_history_windows(windows):
    merged = []
    for window in sorted(windows, key=lambda item: item["since"]):
        if not merged or datetime.fromisoformat(window["since"]) > datetime.fromisoformat(merged[-1]["until"]) + timedelta(microseconds=1):
            merged.append(dict(window))
            continue
        previous = merged[-1]
        previous["until"] = max(previous["until"], window["until"])
        if previous["mode"] != "update" or window["mode"] != "update":
            previous.update(mode="backfill", stop_id=None)
        else:
            left, right = previous["stop_id"], window["stop_id"]
            previous["stop_id"] = min(left, right) if left is not None and right is not None else None
    return merged


def _previous_analysis_history(store, job, entity):
    """Inspect retained checkpoints one at a time, newest first (no interval ledger)."""
    since, upper = _analysis_history_bounds(job["request"]["request"])
    fresh = datetime.fromisoformat(job["accepted_at"]).replace(hour=0, minute=0, second=0, microsecond=0)
    closed_upper = min(upper, fresh - timedelta(microseconds=1))
    kind = _telegram_peer_kind(type(entity).__name__)
    sync = store.conn.execute("SELECT last_error FROM sync_state WHERE channel_id=?", (entity.id,)).fetchone()
    sync_error = sync is not None and bool(sync[0])
    has_messages = store.conn.execute(
        "SELECT 1 FROM messages WHERE channel_id=? AND date_utc>=? AND date_utc<=? LIMIT 1",
        (entity.id, since.isoformat(), upper.isoformat()),
    ).fetchone() is not None
    rows = store.conn.execute("""SELECT job_id,request_json,checkpoint_json FROM analysis_jobs WHERE kind='analysis'
        AND rowid < (SELECT rowid FROM analysis_jobs WHERE job_id=?) ORDER BY rowid DESC""", (job["job_id"],))
    for row in rows:
        source = json.loads(row[2]).get("history", {}).get("sources", {}).get(str(entity.id))
        if source is None or (source.get("peer_kind") is not None and kind is not None and source["peer_kind"] != kind):
            continue
        old_since, old_upper = _analysis_history_bounds(json.loads(row[1])["request"])
        if old_upper < since or old_since > upper:
            continue
        if source["state"] == "complete" and not source.get("pending_windows"):
            if not has_messages and source.get("mode") == "reuse":
                # Reusing archive edges never proved an empty range was fetched.
                continue
            if not sync_error and old_since <= since and old_upper >= closed_upper:
                return {"complete": True}
            continue
        start = datetime.fromisoformat(source.get("since", old_since.isoformat()))
        end = datetime.fromisoformat(source.get("until", old_upper.isoformat()))
        windows = [{"since": start.isoformat(), "until": end.isoformat()}] + source.get("pending_windows", [])
        if not any(datetime.fromisoformat(w["since"]) <= upper and datetime.fromisoformat(w["until"]) >= since for w in windows):
            continue
        return {"complete": False, "source": source, "job_id": row[0],
                "same_scope": (since, upper) == (old_since, old_upper), "windows": windows}
    if sync_error:
        return {"complete": False, "same_scope": False,
                "windows": [{"since": since.isoformat(), "until": upper.isoformat()}]}
    return None


def _analysis_history_windows(conn, entity, request, *, previous=None):
    """Plan at most two missing day edges; do not audit holes inside the archive."""
    since, upper = _analysis_history_bounds(request)
    if since > upper:
        return []
    first = conn.execute("SELECT date_utc FROM messages WHERE channel_id=? ORDER BY date_utc LIMIT 1", (entity.id,)).fetchone()
    last = conn.execute("SELECT date_utc FROM messages WHERE channel_id=? ORDER BY date_utc DESC LIMIT 1", (entity.id,)).fetchone()
    windows = []

    def append(start, end, mode="backfill"):
        if start > end:
            return
        stop = None
        if mode == "update":
            stop = conn.execute("SELECT MAX(message_id) FROM messages WHERE channel_id=? AND date_utc<=?",
                                (entity.id, end.isoformat())).fetchone()[0]
        windows.append({"mode": mode, "since": start.isoformat(), "until": end.isoformat(), "stop_id": stop})

    fresh_day = datetime.fromisoformat(request["accepted_at"]).replace(hour=0, minute=0, second=0, microsecond=0)
    if previous and previous["complete"]:
        if upper >= fresh_day:
            append(max(since, fresh_day), upper, "update")
        return windows
    has_messages = conn.execute(
        "SELECT 1 FROM messages WHERE channel_id=? AND date_utc>=? AND date_utc<=? LIMIT 1",
        (entity.id, since.isoformat(), upper.isoformat()),
    ).fetchone() is not None
    if first is None or not has_messages:
        append(since, upper)
        return windows
    oldest_day = datetime.fromisoformat(first[0]).replace(hour=0, minute=0, second=0, microsecond=0)
    newest_day = datetime.fromisoformat(last[0]).replace(hour=0, minute=0, second=0, microsecond=0)
    if oldest_day > upper:
        append(since, upper)
        return windows
    if since.date() < oldest_day.date():
        append(since, min(upper, oldest_day - timedelta(microseconds=1)))
    if upper.date() >= fresh_day.date():
        # Include any missing closed days before refreshing today's head.
        append(max(since, min(newest_day + timedelta(days=1), fresh_day)), upper, "update")
    elif upper.date() > newest_day.date():
        append(max(since, newest_day + timedelta(days=1)), upper)
    return windows


def _new_analysis_history_source(store, job, entity):
    request = job["request"]["request"]
    previous = _previous_analysis_history(store, job, entity)
    if previous and not previous["complete"] and previous["same_scope"]:
        source = previous["source"]
        source.setdefault("since", request["since"])
        source.setdefault("until", _analysis_history_bounds(request)[1].isoformat())
        source.update(returned_messages=0, resumed_from_job=previous["job_id"])
        # Pending received rows are reusable; a lost physical reply is not.
        source.pop("reservation", None)
        return source
    windows = _analysis_history_windows(store.conn, entity, request, previous=previous)
    if previous and not previous["complete"]:
        since, upper = _analysis_history_bounds(request)
        for window in previous["windows"]:
            start = max(since, datetime.fromisoformat(window["since"]))
            end = min(upper, datetime.fromisoformat(window["until"]))
            if start <= end:
                windows.append({"mode": "backfill", "since": start.isoformat(), "until": end.isoformat(), "stop_id": None})
    windows = _coalesce_analysis_history_windows(windows)
    source = {"mode": "reuse", "state": "complete", "cursor": 0, "stop_id": None,
              "returned_messages": 0, "peer_kind": _telegram_peer_kind(type(entity).__name__)}
    if windows:
        source.update(windows[0], state="running", pending_windows=windows[1:])
    return source


def _plan_analysis_history_source(store, job_id, generation, channel, entity):
    job = _analysis_history_job(store, job_id, generation)
    planned = None
    if str(entity.id) not in job["checkpoint"].get("history", {}).get("sources", {}):
        planned = _new_analysis_history_source(store, job, entity)
    with common_sqlite.write_tx(store.conn, immediate=True):
        job = _analysis_history_job(store, job_id, generation)
        history = job["checkpoint"].setdefault("history", {})
        refs, sources = history.setdefault("refs", {}), history.setdefault("sources", {})
        if channel in refs and refs[channel] != entity.id:
            raise TelegramPeerIdentityError("Analysis source identity changed")
        _upsert_channel_no_tx(store.conn, entity)
        kind = _telegram_peer_kind(type(entity).__name__)
        source = sources.get(str(entity.id))
        if source is not None and source.get("peer_kind") is not None and kind is not None and source["peer_kind"] != kind:
            raise TelegramPeerIdentityError("Analysis source peer kind changed")
        refs[channel] = entity.id
        history.get("failed_refs", {}).pop(channel, None)
        if source is None:
            source = planned
            sources[str(entity.id)] = source
        if source["mode"] == "reuse":
            source["state"] = "complete"
            source.pop("reason", None)
        _save_history_checkpoint_no_tx(store, job)
    return source


def _next_analysis_history_window(store, job_id, generation, channel_id):
    with common_sqlite.write_tx(store.conn, immediate=True):
        job = _analysis_history_job(store, job_id, generation)
        source = job["checkpoint"]["history"]["sources"][str(channel_id)]
        pending = source.get("pending_windows", [])
        if source["state"] == "complete" and pending:
            source.update(pending.pop(0), cursor=0, state="running")
            source.pop("reason", None)
            _save_history_checkpoint_no_tx(store, job)
            return True
    return False


async def ensure_analysis_history(store, job_id, runtime, *, generation, client=None, deadline=None):
    """Resolve explicit sources, reuse closed days and load only missing/fresh edges."""
    job = _analysis_history_job(store, job_id, generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    deadline = deadline if deadline is not None else common_process.continuous_time() + settings["bridge"]["worker_process_timeout_seconds"]
    profile = replace(runtime, **settings["telethon"], default_auth_mode=settings["auth"]["default_mode"],
                      public_auth_mode=settings["auth"]["public_channel_mode"], private_auth_mode=settings["auth"]["private_channel_mode"])

    async def one(channel, owned_client):
        try:
            entity, _ = await _analysis_rpc_retry(store, job_id, generation, settings, deadline,
                                                  lambda _: resolve_channel_entity(owned_client, channel))
        except AnalysisHistoryRequestError as exc:
            return _record_analysis_source_error(store, job_id, generation, channel, exc)
        access_error = _analysis_source_access_error(entity)
        if access_error is not None:
            return _record_analysis_source_error(store, job_id, generation, channel, access_error)
        try:
            source = _plan_analysis_history_source(store, job_id, generation, channel, entity)
        except TelegramPeerIdentityError:
            return _record_analysis_source_error(store, job_id, generation, channel,
                                                  AnalysisHistoryRequestError("source_identity_conflict", state="failed"))
        while True:
            if source["mode"] != "reuse":
                source = await sync_analysis_source(store, job_id, profile, generation=generation, channel=channel,
                                                     mode=source["mode"], client=owned_client, deadline=deadline,
                                                     resolved_entity=entity)
            if not _next_analysis_history_window(store, job_id, generation, entity.id):
                return source
            source = store.get_job(job_id)["checkpoint"]["history"]["sources"][str(entity.id)]

    for channel in request["channels"]:
        _analysis_history_job(store, job_id, generation)
        if common_process.continuous_time() >= deadline:
            _record_analysis_source_error(store, job_id, generation, channel,
                                            AnalysisHistoryRequestError("run_deadline"))
            continue
        if client is not None:
            await one(channel, client)
        else:
            auth = resolve_auth_mode(profile, profile.default_auth_mode, channel)
            try:
                async with open_telethon_session(profile, auth, managed_requests=True) as owned_client:
                    await one(channel, owned_client)
            except TelegramConnectionError as exc:
                from telethon import errors
                if not (is_retryable_connection_error(exc.cause) or isinstance(exc.cause, (errors.ServerError, errors.InvalidDCError))):
                    raise
                _record_analysis_source_error(store, job_id, generation, channel,
                                                AnalysisHistoryRequestError("session_unavailable"))
            except TimeoutError:
                _record_analysis_source_error(store, job_id, generation, channel,
                                                AnalysisHistoryRequestError("session_wait_timeout"))
    return store.get_job(job_id)["checkpoint"].get("history", {})


def commit_analysis_history_page(store, job_id: str, *, generation: int, entity: Any,
                                  writes: list[StagedMessageWrite], expected_cursor: int, next_cursor: int,
                                  returned_messages: int, complete: bool = False) -> dict[str, Any]:
    if __package__:
        from .analysis_store import StaleGenerationError, encode_payload
    else:
        from analysis_store import StaleGenerationError, encode_payload
    if (any(type(value) is not int or value < 0 for value in (expected_cursor, next_cursor, returned_messages))
            or type(complete) is not bool or returned_messages < len(writes)
            or any(item.entity.id != entity.id for item in writes)):
        raise ValueError("Invalid analysis history page")
    if returned_messages:
        if next_cursor == 0 or (expected_cursor and next_cursor >= expected_cursor):
            raise ValueError("Analysis history cursor must advance")
    elif not complete or next_cursor != expected_cursor:
        raise ValueError("Only a completed empty page may retain its cursor")
    conn = store.conn
    with common_sqlite.write_tx(conn, immediate=True):
        job = store.get_job(job_id)
        if job is None or job["generation"] != generation or job["status"] != "running":
            raise StaleGenerationError("Analysis history generation changed")
        if job["cancel_requested"]:
            raise InterruptedError("Analysis history cancelled")
        checkpoint = job["checkpoint"]
        source = checkpoint.get("history", {}).get("sources", {}).get(str(entity.id))
        if not isinstance(source, dict) or source.get("cursor") != expected_cursor or source.get("state") != "running":
            raise StaleGenerationError("Analysis history cursor changed")
        if source.get("mode") not in {"backfill", "update"}:
            raise ValueError("Invalid analysis history mode")
        settings = job["request"]["settings"]
        cap = _analysis_download_cap(settings)
        if job["downloaded_messages"] + returned_messages > cap:
            raise ValueError("Analysis download budget exhausted")
        source.update(cursor=next_cursor, state="complete" if complete else "running",
                      returned_messages=source.get("returned_messages", 0) + returned_messages)
        if writes:
            source["highest_seen_id"] = max(source.get("highest_seen_id") or 0, *(item.message.id for item in writes))
        encoded = encode_payload(checkpoint, max_bytes=settings["analysis"]["max_intermediate_bytes"])
        _upsert_channel_no_tx(conn, entity)
        for item in writes:
            _upsert_message_no_tx(conn, entity, item.message, item.sender_username, item.sender_display_name,
                                  item.downloaded_path, item.downloaded_size)
        if complete:
            _mark_history_source_complete_no_tx(conn, entity, source)
        store._write_checkpoint_no_tx(job_id, encoded, generation=generation, downloaded_delta=returned_messages)
    return source


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


def normalize_channel_lookup_id(channel: str) -> str:
    normalized = channel.strip()
    if normalized.startswith("-100") and normalized[4:].isdigit():
        return normalized[4:]
    return normalized.lstrip("-")


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
    *,
    client: Any | None = None,
    auth_mode_override: str | None = None,
) -> dict[str, Any]:
    auth_mode = auth_mode_override or resolve_auth_mode(runtime, args.auth_mode, channel)

    async def _sync_with_client(active_client: Any) -> dict[str, Any]:
        entity = await resolve_channel_entity(active_client, channel)
        upsert_channel(conn, entity)
        batch_size = int(getattr(args, "batch_size", 0) or runtime.sync_batch_size or 0)
        flush_threshold = batch_size if batch_size > 0 else 1
        scan_limit = getattr(args, "limit", None)
        if scan_limit is not None and scan_limit <= 0:
            scan_limit = None

        since_dt = parse_filter_datetime_value(getattr(args, "since", None))
        until_dt = parse_filter_datetime_value(getattr(args, "until", None), end_of_day=True)
        if mode in {"backfill", "tail", "update"}:
            iterator_options = {"limit": scan_limit}
            if until_dt is not None:
                # Telethon's offset is exclusive; preserve our inclusive,
                # second-resolution upper bound without scanning newer posts.
                iterator_options["offset_date"] = until_dt + timedelta(seconds=1)
            iterator = active_client.iter_messages(entity, **iterator_options)
        else:
            raise SystemExit(f"Unsupported sync mode: {mode}")

        processed = 0
        skipped_existing = 0
        refreshed_existing_media = 0
        highest_message_id = None
        seen_messages = 0
        scan_boundary_reached = False
        mark_read_target_id = latest_processed_message_id(conn, entity.id, mode) if getattr(args, "mark_read", False) else None
        existing_max_id = latest_stored_message_id(conn, entity.id) if mode == "update" else None
        staged_writes: list[StagedMessageWrite] = []

        def stage_write(
            message_obj: Any,
            sender_username_value: str | None,
            sender_display_name_value: str | None,
            downloaded_path_value: str | None,
            downloaded_size_value: int | None,
        ) -> None:
            staged_writes.append(
                StagedMessageWrite(
                    entity=entity,
                    message=message_obj,
                    sender_username=sender_username_value,
                    sender_display_name=sender_display_name_value,
                    downloaded_path=downloaded_path_value,
                    downloaded_size=downloaded_size_value,
                )
            )
            if len(staged_writes) >= flush_threshold:
                flush_staged_message_writes(conn, staged_writes)
                staged_writes.clear()

        async for message in iterator:
            seen_messages += 1
            message_dt = getattr(message, "date", None)
            if message_dt is not None and message_dt.tzinfo is None:
                message_dt = message_dt.replace(tzinfo=timezone.utc)
            if until_dt is not None and message_dt is not None and message_dt > until_dt:
                continue
            if since_dt is not None and message_dt is not None and message_dt < since_dt:
                scan_boundary_reached = True
                break
            if mode == "update" and existing_max_id is not None and message.id <= existing_max_id:
                scan_boundary_reached = True
                break
            downloaded_path = None
            downloaded_size = None
            sender_username, sender_display_name = await resolve_sender_metadata(entity, message)
            exists = message_exists(conn, entity.id, message.id)
            if exists:
                skipped_existing += 1
                if args.download_media and getattr(message, "media", None) and media_needs_download(conn, entity.id, message.id):
                    downloaded_path, downloaded_size = await download_media_if_present(runtime, active_client, message, entity.id)
                    stage_write(message, sender_username, sender_display_name, downloaded_path, downloaded_size)
                    if downloaded_path:
                        refreshed_existing_media += 1
                continue
            if args.download_media and getattr(message, "media", None):
                downloaded_path, downloaded_size = await download_media_if_present(runtime, active_client, message, entity.id)
            stage_write(message, sender_username, sender_display_name, downloaded_path, downloaded_size)
            highest_message_id = max(highest_message_id or message.id, message.id)
            processed += 1

        flush_staged_message_writes(conn, staged_writes)

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
            ocr_processed = process_pending_ocr(
                conn,
                binary,
                limit=scan_limit,
                channel_id=entity.id,
                tesseract_timeout_seconds=runtime.tesseract_timeout_seconds,
            )
        if highest_message_id is not None:
            if mark_read_target_id is None:
                mark_read_target_id = highest_message_id
            elif mode == "backfill":
                # For historical digest-style backfills we want to mark the
                # full range that was actually processed in this run, even when
                # a previous backfill boundary already exists in sync_state.
                mark_read_target_id = max(mark_read_target_id, highest_message_id)
        current_read_max_id = None
        marked_read_from = None
        marked_read_until = None
        if getattr(args, "mark_read", False):
            if auth_mode != "user":
                raise SystemExit("Mark-as-read is only supported in user auth mode.")
            if mark_read_target_id is not None and highest_message_id is not None:
                current_read_max_id = await current_read_inbox_max_id(active_client, entity)
                if current_read_max_id is None or mark_read_target_id > current_read_max_id:
                    marked_read_from = (current_read_max_id + 1) if current_read_max_id is not None else 1
                    await active_client.send_read_acknowledge(entity, max_id=mark_read_target_id)
                    marked_read_until = mark_read_target_id
        return {
            "channel": channel,
            "channel_id": entity.id,
            "mode": mode,
            "auth_mode": auth_mode,
            "processed_messages": processed,
            "skipped_existing": skipped_existing,
            "sync_limit_reached": bool(
                scan_limit is not None and seen_messages >= scan_limit and not scan_boundary_reached
            ),
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

    if client is not None:
        return await _sync_with_client(client)

    async with open_telethon_session(runtime, auth_mode) as client:
        return await _sync_with_client(client)
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
            results.append(
                {
                    "channel": channel,
                    "mode": mode,
                    "auth_mode": getattr(args, "auth_mode", None) or runtime.default_auth_mode,
                    "status": "skipped",
                    "error": "shared sync_limit budget exhausted before this channel",
                    "limit": limit,
                    "since": getattr(args, "since", None),
                    "until": getattr(args, "until", None),
                }
            )
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
    where_sql = " AND ".join(where)
    sql = "SELECT ma.channel_id, ma.message_id, ma.ordinal, ma.local_path, ma.media_kind FROM media_assets ma JOIN messages m ON m.channel_id = ma.channel_id AND m.message_id = ma.message_id WHERE __WHERE__ ORDER BY m.date_utc ASC, ma.created_at ASC__LIMIT__".replace("__WHERE__", where_sql).replace("__LIMIT__", limit_sql)  # nosec B608
    return list(conn.execute(sql, params))


def run_tesseract(binary: str, image_path: str, *, timeout_seconds: int = DEFAULT_TESSERACT_TIMEOUT_SECONDS) -> str:
    result = subprocess.run(
        [binary, image_path, "stdout"],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
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
    tesseract_timeout_seconds: int = DEFAULT_TESSERACT_TIMEOUT_SECONDS,
) -> int:
    rows = iter_pending_ocr(conn, limit, channel_id=channel_id, since=since, until=until)
    processed = 0
    for row in rows:
        try:
            if not is_image_media_kind(row["media_kind"]):
                status, text, error = "skipped", None, "OCR is only supported for image media."
            else:
                text = run_tesseract(binary, row["local_path"], timeout_seconds=tesseract_timeout_seconds)
                status, error = "done", None
        except Exception:
            status, text, error = "error", None, "OCR processing failed."
        # Database/index failures must roll back and propagate, rather than be
        # relabelled as an OCR recognition failure by the broad handler above.
        save_media_ocr_result(conn, row["channel_id"], row["message_id"], row["ordinal"],
                              status=status, text=text, error=error)
        processed += 1
    return processed


def save_media_ocr_result(conn: sqlite3.Connection, channel_id: int, message_id: int, ordinal: int,
                           *, status: str, text: str | None = None, error: str | None = None) -> None:
    if status not in {"done", "error", "skipped"}:
        raise ValueError("Invalid OCR result status")
    with common_sqlite.write_tx(conn):
        conn.execute("""UPDATE media_assets SET ocr_status=?,
            ocr_text=CASE WHEN ?='done' THEN ? ELSE ocr_text END,ocr_error=?,ocr_processed_at=?
            WHERE channel_id=? AND message_id=? AND ordinal=?""",
            (status, status, optional_text(text), optional_text(error), now_utc(), channel_id, message_id, ordinal))
        _refresh_message_fts_no_tx(conn, channel_id, message_id)


def resolve_channel_filter(conn: sqlite3.Connection, channel: str) -> sqlite3.Row | None:
    normalized = channel.strip()
    username = normalized[1:] if normalized.startswith("@") else normalized
    lookup_id = normalize_channel_lookup_id(normalized)
    rows = conn.execute(
        """
        SELECT channel_id, username, title
        FROM channels
        WHERE username = ? OR CAST(channel_id AS TEXT) = ?
        LIMIT 1
        """,
        (username, lookup_id),
    ).fetchall()
    return rows[0] if rows else None


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
    else:
        limit_sql = "LIMIT ?"
        params.append(limit if limit is not None else runtime.export_default_limit)

    where_sql = " AND ".join(where)
    sql = "SELECT c.channel_id, c.username, c.title, m.message_id, m.grouped_id, m.date_utc, m.edit_date_utc, m.sender_id, m.sender_username, m.sender_display_name, m.text, m.views, m.forwards, m.replies, m.has_media, m.media_kind, m.content_hash, m.imported_at, CASE WHEN ma.local_path IS NOT NULL THEN 1 ELSE 0 END AS has_local_media, ma.ocr_status, ma.ocr_text FROM messages m JOIN channels c ON c.channel_id = m.channel_id LEFT JOIN media_assets ma ON ma.channel_id = m.channel_id AND ma.message_id = m.message_id AND ma.ordinal = 0 WHERE __WHERE__ __ORDER_BY__ __LIMIT__".replace("__WHERE__", where_sql).replace("__ORDER_BY__", order_by).replace("__LIMIT__", limit_sql)  # nosec B608
    rows = conn.execute(sql, tuple(params)).fetchall()

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
                "grouped_id",
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
                "content_hash",
                "imported_at",
                "has_local_media",
                "ocr_status",
                "ocr_text",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return output, len(rows)


async def fetch_and_export_message(runtime: RuntimeConfig, conn: sqlite3.Connection, url: str, output_dir: str | None) -> dict[str, Any]:
    channel, message_id = parse_message_url(url)
    async with open_telethon_session(runtime, "user") as client:
        entity = await resolve_channel_entity(client, channel)
        message = await client.get_messages(entity, ids=message_id)
        if message is None:
            raise SystemExit(f"Message {message_id} was not found for '{channel}'.")
        upsert_channel(conn, entity)
        sender_username, sender_display_name = await resolve_sender_metadata(entity, message)
        upsert_message(conn, entity, message, sender_username, sender_display_name, None, None)
        output_path = export_single_message_source(entity, message, url, output_dir)
    return {
        "channel": channel,
        "message_id": message_id,
        "output_file": str(output_path),
        "saved_to_db": True,
        "auth_mode": "user",
    }


def cmd_init_db(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    conn = connect_db(runtime)
    init_db(conn)
    print(json.dumps({"status": "initialized"}, ensure_ascii=False, indent=2))
    return 0


def cmd_index_history(args: argparse.Namespace) -> int:
    if __package__:
        from . import telegram_analysis as analysis
    else:
        import telegram_analysis as analysis
    config = load_runtime_config()
    conn = common_sqlite.connect_sqlite(analysis.bridge.resolve_history_db_path(config))
    try:
        init_db(conn)
        job = analysis.enqueue_fts_backfill(analysis.AnalysisStore(conn), config, rebuild=args.rebuild,
                                            now=datetime.now(timezone.utc))
        print(json.dumps({"job_id": job["job_id"], "status": job["status"]}, ensure_ascii=False))
        return 0
    except analysis.QueueFullError:
        print(json.dumps({"error": "queue_full"}), file=sys.stderr)
        return 1
    except ValueError as exc:
        reason = str(exc) if str(exc) in {"fts_unavailable", "fts_rebuild_required"} else "index_configuration_error"
        print(json.dumps({"error": reason}), file=sys.stderr)
        return 1
    finally:
        conn.close()


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
            limit=args.limit if args.limit is not None else runtime.ocr_pending_default_limit,
            channel_id=channel_id,
            since=args.since,
            until=args.until,
            tesseract_timeout_seconds=runtime.tesseract_timeout_seconds,
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


def cmd_fetch_message(args: argparse.Namespace) -> int:
    import asyncio

    runtime = resolve_runtime()
    conn = connect_db(runtime)
    init_db(conn)
    result = asyncio.run(fetch_and_export_message(runtime, conn, args.url, args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
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

    index_history = subparsers.add_parser("index-history", help="Queue or resume the local FTS index build for the bridge supervisor.")
    index_history.add_argument("--rebuild", action="store_true", help="Invalidate readiness and rebuild the derived index from the start.")
    index_history.set_defaults(func=cmd_index_history)

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

    fetch_message = subparsers.add_parser(
        "fetch-message",
        help="Fetch one Telegram message by URL, store it in SQLite, and export a Markdown source artifact.",
    )
    fetch_message.add_argument("--url", required=True, help="Telegram message URL such as https://t.me/channel/123.")
    fetch_message.add_argument("--output-dir", help="Directory for the exported Markdown source file.")
    fetch_message.set_defaults(func=cmd_fetch_message)

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
    ocr_pending.add_argument("--limit", type=int, default=None, help="How many media assets to process. Defaults to [ocr].pending_default_limit.")
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
