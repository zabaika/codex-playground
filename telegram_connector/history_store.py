"""History SQLite schema, indexes and transactional writers."""
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import sqlite as common_sqlite
if __package__:
    from . import history_messages, history_settings
else:
    import history_messages
    import history_settings


# Internal index protocol version, independent of operator sizing settings.
FTS_INDEX_VERSION = 1


FTS_INDEX_ADMISSION_KEY = f"internal:fts:v{FTS_INDEX_VERSION}"


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


@dataclass(slots=True)
class StagedMessageWrite:
    entity: Any
    message: Any
    sender_username: str | None
    sender_display_name: str | None
    downloaded_path: str | None
    downloaded_size: int | None


def now_utc() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def connect_db(runtime: history_settings.RuntimeConfig) -> sqlite3.Connection:
    history_settings.ensure_dirs(runtime)
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
            history_messages.optional_text(getattr(entity, "access_hash", None)),
            history_messages.optional_text(getattr(entity, "username", None)),
            getattr(entity, "title", None) or getattr(entity, "first_name", "unknown"),
            getattr(entity, "__class__", type(entity)).__name__,
            history_messages.minimal_entity_json(entity),
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
    text = history_messages.message_text(message)
    media_kind = history_messages.message_media_kind(message)
    imported_at = now_utc()
    content_hash = f"{getattr(message, 'id')}:{history_messages.serialize_datetime(getattr(message, 'edit_date', None))}:{len(text)}:{media_kind or '-'}"
    grouped_id = getattr(message, "grouped_id", None)
    values = [
        entity.id, message.id, str(grouped_id) if grouped_id is not None else None,
        history_messages.serialize_datetime(getattr(message, "date", None)) or imported_at,
        history_messages.serialize_datetime(getattr(message, "edit_date", None)), history_messages.optional_text(getattr(message, "sender_id", None)),
        history_messages.optional_text(item.sender_username), history_messages.optional_text(item.sender_display_name), text,
        getattr(message, "views", None), getattr(message, "forwards", None),
        getattr(getattr(message, "replies", None), "replies", None),
        1 if getattr(message, "media", None) else 0, media_kind,
        history_messages.minimal_message_json(message, media_kind, text), content_hash, imported_at,
    ]
    media = None
    if item.downloaded_path or media_kind:
        media = [entity.id, message.id, media_kind or "media", history_messages.optional_text(item.downloaded_path),
                 history_messages.optional_text(media_kind), item.downloaded_size,
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
        params.append(history_settings.parse_since_datetime(since))
    if until:
        where.append("m.date_utc <= ?")
        params.append(history_settings.parse_until_datetime(until))
    limit_sql = ""
    if limit is not None and limit > 0:
        params.append(limit)
        limit_sql = "\n            LIMIT ?"
    where_sql = " AND ".join(where)
    sql = "SELECT ma.channel_id, ma.message_id, ma.ordinal, ma.local_path, ma.media_kind FROM media_assets ma JOIN messages m ON m.channel_id = ma.channel_id AND m.message_id = ma.message_id WHERE __WHERE__ ORDER BY m.date_utc ASC, ma.created_at ASC__LIMIT__".replace("__WHERE__", where_sql).replace("__LIMIT__", limit_sql)  # nosec B608
    return list(conn.execute(sql, params))


def save_media_ocr_result(conn: sqlite3.Connection, channel_id: int, message_id: int, ordinal: int,
                           *, status: str, text: str | None = None, error: str | None = None) -> None:
    if status not in {"done", "error", "skipped"}:
        raise ValueError("Invalid OCR result status")
    with common_sqlite.write_tx(conn):
        conn.execute("""UPDATE media_assets SET ocr_status=?,
            ocr_text=CASE WHEN ?='done' THEN ? ELSE ocr_text END,ocr_error=?,ocr_processed_at=?
            WHERE channel_id=? AND message_id=? AND ordinal=?""",
            (status, status, history_messages.optional_text(text), history_messages.optional_text(error), now_utc(), channel_id, message_id, ordinal))
        _refresh_message_fts_no_tx(conn, channel_id, message_id)


def resolve_channel_filter(conn: sqlite3.Connection, channel: str) -> sqlite3.Row | None:
    normalized = channel.strip()
    username = normalized[1:] if normalized.startswith("@") else normalized
    lookup_id = history_settings.normalize_channel_lookup_id(normalized)
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
