#!/usr/bin/env python3
import argparse
import asyncio
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import sqlite as common_sqlite
# Compatibility for callers that classify failures of a leased history session.
from telegram_shared.async_retry import AsyncCleanupError

if __package__:
    from . import analysis_history
else:
    import analysis_history

AnalysisHistoryRequestError = analysis_history.AnalysisHistoryRequestError
TELEGRAM_HISTORY_PAGE_LIMIT = analysis_history.TELEGRAM_HISTORY_PAGE_LIMIT
_active_analysis_history_source = analysis_history._active_analysis_history_source
_analysis_download_cap = analysis_history._analysis_download_cap
_analysis_history_bounds = analysis_history._analysis_history_bounds
_analysis_history_job = analysis_history._analysis_history_job
_analysis_history_windows = analysis_history._analysis_history_windows
_analysis_rpc_retry = analysis_history._analysis_rpc_retry
_analysis_source_access_error = analysis_history._analysis_source_access_error
_begin_analysis_source = analysis_history._begin_analysis_source
_coalesce_analysis_history_windows = analysis_history._coalesce_analysis_history_windows
_mark_history_source_complete_no_tx = analysis_history._mark_history_source_complete_no_tx
_new_analysis_history_source = analysis_history._new_analysis_history_source
_next_analysis_history_window = analysis_history._next_analysis_history_window
_plan_analysis_history_source = analysis_history._plan_analysis_history_source
_previous_analysis_history = analysis_history._previous_analysis_history
_record_analysis_source_error = analysis_history._record_analysis_source_error
_save_history_checkpoint_no_tx = analysis_history._save_history_checkpoint_no_tx
_settle_analysis_page_failure = analysis_history._settle_analysis_page_failure
backfill_fts_page = analysis_history.backfill_fts_page
commit_analysis_history_page = analysis_history.commit_analysis_history_page
drain_analysis_history_page = analysis_history.drain_analysis_history_page
ensure_analysis_history = analysis_history.ensure_analysis_history
reserve_analysis_history_page = analysis_history.reserve_analysis_history_page
stage_analysis_history_page = analysis_history.stage_analysis_history_page
sync_analysis_source = analysis_history.sync_analysis_source

if __package__:
    from . import history_session
else:
    import history_session

RETRYABLE_CONNECTION_ERRNOS = history_session.RETRYABLE_CONNECTION_ERRNOS
TelegramConnectionError = history_session.TelegramConnectionError
_acquire_session_lock = history_session._acquire_session_lock
build_entity_lookup_reference = history_session.build_entity_lookup_reference
canonical_session_identity = history_session.canonical_session_identity
classify_channel_reference = history_session.classify_channel_reference
current_read_inbox_max_id = history_session.current_read_inbox_max_id
is_retryable_connection_error = history_session.is_retryable_connection_error
make_session_path = history_session.make_session_path
normalize_auth_mode = history_session.normalize_auth_mode
open_telethon_client = history_session.open_telethon_client
open_telethon_session = history_session.open_telethon_session
require_telethon = history_session.require_telethon
resolve_auth_mode = history_session.resolve_auth_mode
resolve_channel_entity = history_session.resolve_channel_entity
channel_dir = history_session.channel_dir
download_media_if_present = history_session.download_media_if_present
resolve_sender_metadata = history_session.resolve_sender_metadata

if __package__:
    from . import history_store
else:
    import history_store

ANALYSIS_SCHEMA_STATEMENTS = history_store.ANALYSIS_SCHEMA_STATEMENTS
FTS_INDEX_ADMISSION_KEY = history_store.FTS_INDEX_ADMISSION_KEY
FTS_INDEX_VERSION = history_store.FTS_INDEX_VERSION
MEDIA_ASSETS_TABLE_SQL = history_store.MEDIA_ASSETS_TABLE_SQL
MESSAGES_TABLE_SQL = history_store.MESSAGES_TABLE_SQL
SCHEMA_SQL = history_store.SCHEMA_SQL
SQLITE_CONFIG = history_store.SQLITE_CONFIG
StagedMessageWrite = history_store.StagedMessageWrite
TelegramPeerIdentityError = history_store.TelegramPeerIdentityError
_invalidate_fts_index_no_tx = history_store._invalidate_fts_index_no_tx
_message_fts_enabled = history_store._message_fts_enabled
_refresh_message_fts_no_tx = history_store._refresh_message_fts_no_tx
_telegram_peer_kind = history_store._telegram_peer_kind
_update_sync_state_no_tx = history_store._update_sync_state_no_tx
_upsert_channel_no_tx = history_store._upsert_channel_no_tx
_upsert_message_no_tx = history_store._upsert_message_no_tx
_upsert_prepared_message_no_tx = history_store._upsert_prepared_message_no_tx
connect_db = history_store.connect_db
delete_history_message = history_store.delete_history_message
delete_media_asset = history_store.delete_media_asset
flush_staged_message_writes = history_store.flush_staged_message_writes
fts5_available = history_store.fts5_available
init_db = history_store.init_db
iter_pending_ocr = history_store.iter_pending_ocr
latest_processed_message_id = history_store.latest_processed_message_id
latest_stored_message_id = history_store.latest_stored_message_id
media_needs_download = history_store.media_needs_download
message_exists = history_store.message_exists
migrate_ai_usage_log_schema = history_store.migrate_ai_usage_log_schema
migrate_sqlite_schema = history_store.migrate_sqlite_schema
now_utc = history_store.now_utc
prepare_history_write = history_store.prepare_history_write
require_fts_ready = history_store.require_fts_ready
resolve_channel_filter = history_store.resolve_channel_filter
save_media_ocr_result = history_store.save_media_ocr_result
table_columns = history_store.table_columns
update_sync_state = history_store.update_sync_state
upsert_channel = history_store.upsert_channel
upsert_message = history_store.upsert_message

if __package__:
    from . import history_messages
else:
    import history_messages

build_utf16_offset_map = history_messages.build_utf16_offset_map
compose_single_message_title = history_messages.compose_single_message_title
direct_sender_metadata = history_messages.direct_sender_metadata
entity_display_name = history_messages.entity_display_name
escape_markdown_link_label = history_messages.escape_markdown_link_label
extract_message_links = history_messages.extract_message_links
is_image_media_kind = history_messages.is_image_media_kind
message_media_kind = history_messages.message_media_kind
message_subject_line = history_messages.message_subject_line
message_text = history_messages.message_text
minimal_entity_json = history_messages.minimal_entity_json
minimal_message_json = history_messages.minimal_message_json
optional_text = history_messages.optional_text
parse_message_url = history_messages.parse_message_url
render_message_body_with_inline_links = history_messages.render_message_body_with_inline_links
render_single_message_source = history_messages.render_single_message_source
safe_to_json = history_messages.safe_to_json
sanitize_note_filename = history_messages.sanitize_note_filename
serialize_datetime = history_messages.serialize_datetime
utf16_entity_range_to_python_range = history_messages.utf16_entity_range_to_python_range

if __package__:
    from . import history_settings
else:
    import history_settings

APP_DIR = history_settings.APP_DIR
BASE_DIR = history_settings.BASE_DIR
CONFIG_DIR = history_settings.CONFIG_DIR
DATA_DIR = history_settings.DATA_DIR
DB_FILE = history_settings.DB_FILE
DEFAULT_CONNECTION_ATTEMPTS = history_settings.DEFAULT_CONNECTION_ATTEMPTS
DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS = history_settings.DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS
DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS = history_settings.DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS
DEFAULT_CONNECTION_TIMEOUT_SECONDS = history_settings.DEFAULT_CONNECTION_TIMEOUT_SECONDS
DEFAULT_EXPORT_CSV_LIMIT = history_settings.DEFAULT_EXPORT_CSV_LIMIT
DEFAULT_OCR_PENDING_LIMIT = history_settings.DEFAULT_OCR_PENDING_LIMIT
DEFAULT_TESSERACT_TIMEOUT_SECONDS = history_settings.DEFAULT_TESSERACT_TIMEOUT_SECONDS
EXPORT_DIR = history_settings.EXPORT_DIR
MEDIA_DIR = history_settings.MEDIA_DIR
OP_REFERENCE_PREFIX = history_settings.OP_REFERENCE_PREFIX
PROJECT_ROOT = history_settings.PROJECT_ROOT
RUNTIME_LOCAL_FILE = history_settings.RUNTIME_LOCAL_FILE
RuntimeConfig = history_settings.RuntimeConfig
SESSION_DIR = history_settings.SESSION_DIR
SINGLE_MESSAGE_EXPORT_DIR = history_settings.SINGLE_MESSAGE_EXPORT_DIR
_SECRET_CACHE = history_settings._SECRET_CACHE
ensure_dirs = history_settings.ensure_dirs
get_config_value = history_settings.get_config_value
get_default_channels = history_settings.get_default_channels
load_runtime_config = history_settings.load_runtime_config
parse_default_channel_entry = history_settings.parse_default_channel_entry
parse_filter_datetime = history_settings.parse_filter_datetime
parse_filter_datetime_value = history_settings.parse_filter_datetime_value
parse_int_config_value = history_settings.parse_int_config_value
parse_since_datetime = history_settings.parse_since_datetime
parse_until_datetime = history_settings.parse_until_datetime
require_int_config_value = history_settings.require_int_config_value
resolve_onepassword_secret = history_settings.resolve_onepassword_secret
resolve_relative_date_token = history_settings.resolve_relative_date_token
resolve_runtime = history_settings.resolve_runtime
resolve_secret_value = history_settings.resolve_secret_value
normalize_channel_lookup_id = history_settings.normalize_channel_lookup_id


def require_tesseract(runtime: history_settings.RuntimeConfig) -> str:
    binary = shutil.which(runtime.tesseract_binary)
    if not binary:
        raise SystemExit(
            f"Tesseract binary '{runtime.tesseract_binary}' was not found. Install tesseract or set [paths].tesseract_binary."
        )
    return binary


        # On uncertain cleanup keep this raw OS descriptor owned until the fatal
        # worker exits. Releasing it would permit another still-overlapping client.


def export_single_message_source(
    entity: Any,
    message: Any,
    source_url: str,
    output_dir: str | None,
) -> Path:
    base_dir = Path(output_dir).expanduser() if output_dir else history_settings.SINGLE_MESSAGE_EXPORT_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    title = history_messages.compose_single_message_title(entity, message)
    file_name = f"{history_messages.sanitize_note_filename(title)} [tg-{getattr(message, 'id', 'unknown')}].md"
    output_path = base_dir / file_name
    output_path.write_text(history_messages.render_single_message_source(entity, message, source_url), encoding="utf-8")
    return output_path




def parse_channel_list(raw: str) -> list[str]:
    channels = [item.strip() for item in raw.split(",") if item.strip()]
    if not channels:
        raise SystemExit("At least one channel is required.")
    return channels


def resolve_channels_argument(runtime: history_settings.RuntimeConfig, raw_channel: str | None) -> list[str]:
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


def default_sync_limit_for_mode(runtime: history_settings.RuntimeConfig, mode: str) -> int:
    limit = runtime.sync_mode_limits.get(mode)
    if limit is None:
        raise SystemExit(f"Missing default sync limit for mode '{mode}' in runtime config.")
    return max(1, int(limit))


async def sync_one_channel(
    conn: sqlite3.Connection,
    runtime: history_settings.RuntimeConfig,
    args: argparse.Namespace,
    mode: str,
    channel: str,
    *,
    client: Any | None = None,
    auth_mode_override: str | None = None,
) -> dict[str, Any]:
    auth_mode = auth_mode_override or history_session.resolve_auth_mode(runtime, args.auth_mode, channel)

    async def _sync_with_client(active_client: Any) -> dict[str, Any]:
        entity = await history_session.resolve_channel_entity(active_client, channel)
        history_store.upsert_channel(conn, entity)
        batch_size = int(getattr(args, "batch_size", 0) or runtime.sync_batch_size or 0)
        flush_threshold = batch_size if batch_size > 0 else 1
        scan_limit = getattr(args, "limit", None)
        if scan_limit is not None and scan_limit <= 0:
            scan_limit = None

        since_dt = history_settings.parse_filter_datetime_value(getattr(args, "since", None))
        until_dt = history_settings.parse_filter_datetime_value(getattr(args, "until", None), end_of_day=True)
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
        mark_read_target_id = history_store.latest_processed_message_id(conn, entity.id, mode) if getattr(args, "mark_read", False) else None
        existing_max_id = history_store.latest_stored_message_id(conn, entity.id) if mode == "update" else None
        staged_writes: list[history_store.StagedMessageWrite] = []

        def stage_write(
            message_obj: Any,
            sender_username_value: str | None,
            sender_display_name_value: str | None,
            downloaded_path_value: str | None,
            downloaded_size_value: int | None,
        ) -> None:
            staged_writes.append(
                history_store.StagedMessageWrite(
                    entity=entity,
                    message=message_obj,
                    sender_username=sender_username_value,
                    sender_display_name=sender_display_name_value,
                    downloaded_path=downloaded_path_value,
                    downloaded_size=downloaded_size_value,
                )
            )
            if len(staged_writes) >= flush_threshold:
                history_store.flush_staged_message_writes(conn, staged_writes)
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
            sender_username, sender_display_name = await history_session.resolve_sender_metadata(entity, message)
            exists = history_store.message_exists(conn, entity.id, message.id)
            if exists:
                skipped_existing += 1
                if args.download_media and getattr(message, "media", None) and history_store.media_needs_download(conn, entity.id, message.id):
                    downloaded_path, downloaded_size = await history_session.download_media_if_present(runtime, active_client, message, entity.id)
                    stage_write(message, sender_username, sender_display_name, downloaded_path, downloaded_size)
                    if downloaded_path:
                        refreshed_existing_media += 1
                continue
            if args.download_media and getattr(message, "media", None):
                downloaded_path, downloaded_size = await history_session.download_media_if_present(runtime, active_client, message, entity.id)
            stage_write(message, sender_username, sender_display_name, downloaded_path, downloaded_size)
            highest_message_id = max(highest_message_id or message.id, message.id)
            processed += 1

        history_store.flush_staged_message_writes(conn, staged_writes)

        now = history_store.now_utc()
        if mode == "backfill":
            history_store.update_sync_state(
                conn,
                entity.id,
                last_backfill_message_id=highest_message_id,
                last_full_sync_at=now,
                last_error=None,
            )
        else:
            history_store.update_sync_state(
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
                current_read_max_id = await history_session.current_read_inbox_max_id(active_client, entity)
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

    async with history_session.open_telethon_session(runtime, auth_mode) as client:
        return await _sync_with_client(client)
async def sync_messages(runtime: history_settings.RuntimeConfig, args: argparse.Namespace, mode: str) -> int:
    conn = history_store.connect_db(runtime)
    history_store.init_db(conn)
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


def run_tesseract(binary: str, image_path: str, *, timeout_seconds: int = history_settings.DEFAULT_TESSERACT_TIMEOUT_SECONDS) -> str:
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
    tesseract_timeout_seconds: int = history_settings.DEFAULT_TESSERACT_TIMEOUT_SECONDS,
) -> int:
    rows = history_store.iter_pending_ocr(conn, limit, channel_id=channel_id, since=since, until=until)
    processed = 0
    for row in rows:
        try:
            if not history_messages.is_image_media_kind(row["media_kind"]):
                status, text, error = "skipped", None, "OCR is only supported for image media."
            else:
                text = run_tesseract(binary, row["local_path"], timeout_seconds=tesseract_timeout_seconds)
                status, error = "done", None
        except Exception:
            status, text, error = "error", None, "OCR processing failed."
        # Database/index failures must roll back and propagate, rather than be
        # relabelled as an OCR recognition failure by the broad handler above.
        history_store.save_media_ocr_result(conn, row["channel_id"], row["message_id"], row["ordinal"],
                              status=status, text=text, error=error)
        processed += 1
    return processed


def sanitize_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return cleaned.strip("_") or "channel"


def export_channel_csv(
    conn: sqlite3.Connection,
    runtime: history_settings.RuntimeConfig,
    *,
    channel: str,
    limit: int | None,
    since: str | None,
    until: str | None,
    output_path: str | None,
) -> tuple[Path, int]:
    channel_row = history_store.resolve_channel_filter(conn, channel)
    if channel_row is None:
        raise SystemExit(f"Channel '{channel}' is not present in the local database yet. Run sync --mode tail or sync --mode backfill first.")

    params: list[Any] = [channel_row["channel_id"]]
    where = ["m.channel_id = ?"]
    order_by = "ORDER BY m.message_id DESC"
    limit_sql = ""

    if since:
        where.append("m.date_utc >= ?")
        params.append(history_settings.parse_since_datetime(since))
    if until:
        where.append("m.date_utc <= ?")
        params.append(history_settings.parse_until_datetime(until))
    if since or until:
        order_by = "ORDER BY m.date_utc DESC, m.message_id DESC"
    else:
        limit_sql = "LIMIT ?"
        params.append(limit if limit is not None else runtime.export_default_limit)

    where_sql = " AND ".join(where)
    sql = "SELECT c.channel_id, c.username, c.title, m.message_id, m.grouped_id, m.date_utc, m.edit_date_utc, m.sender_id, m.sender_username, m.sender_display_name, m.text, m.views, m.forwards, m.replies, m.has_media, m.media_kind, m.content_hash, m.imported_at, CASE WHEN ma.local_path IS NOT NULL THEN 1 ELSE 0 END AS has_local_media, ma.ocr_status, ma.ocr_text FROM messages m JOIN channels c ON c.channel_id = m.channel_id LEFT JOIN media_assets ma ON ma.channel_id = m.channel_id AND ma.message_id = m.message_id AND ma.ordinal = 0 WHERE __WHERE__ __ORDER_BY__ __LIMIT__".replace("__WHERE__", where_sql).replace("__ORDER_BY__", order_by).replace("__LIMIT__", limit_sql)  # nosec B608
    rows = conn.execute(sql, tuple(params)).fetchall()

    output = Path(output_path) if output_path else history_settings.EXPORT_DIR / (
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


async def fetch_and_export_message(runtime: history_settings.RuntimeConfig, conn: sqlite3.Connection, url: str, output_dir: str | None) -> dict[str, Any]:
    channel, message_id = history_messages.parse_message_url(url)
    async with history_session.open_telethon_session(runtime, "user") as client:
        entity = await history_session.resolve_channel_entity(client, channel)
        message = await client.get_messages(entity, ids=message_id)
        if message is None:
            raise SystemExit(f"Message {message_id} was not found for '{channel}'.")
        history_store.upsert_channel(conn, entity)
        sender_username, sender_display_name = await history_session.resolve_sender_metadata(entity, message)
        history_store.upsert_message(conn, entity, message, sender_username, sender_display_name, None, None)
        output_path = export_single_message_source(entity, message, url, output_dir)
    return {
        "channel": channel,
        "message_id": message_id,
        "output_file": str(output_path),
        "saved_to_db": True,
        "auth_mode": "user",
    }


def cmd_init_db(args: argparse.Namespace) -> int:
    runtime = history_settings.resolve_runtime()
    conn = history_store.connect_db(runtime)
    history_store.init_db(conn)
    print(json.dumps({"status": "initialized"}, ensure_ascii=False, indent=2))
    return 0


def cmd_index_history(args: argparse.Namespace) -> int:
    if __package__:
        from . import telegram_analysis as analysis
    else:
        import telegram_analysis as analysis
    config = history_settings.load_runtime_config()
    conn = common_sqlite.connect_sqlite(analysis.bridge.resolve_history_db_path(config))
    try:
        history_store.init_db(conn)
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
    runtime = history_settings.resolve_runtime()
    conn = history_store.connect_db(runtime)
    history_store.init_db(conn)
    binary = require_tesseract(runtime)
    channels = resolve_channels_argument(runtime, args.channel) if getattr(args, "channel", None) or runtime.default_channels else [None]
    results = []
    for channel in channels:
        channel_id = None
        if channel is not None:
            channel_row = history_store.resolve_channel_filter(conn, channel)
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
    runtime = history_settings.resolve_runtime()
    conn = history_store.connect_db(runtime)
    history_store.init_db(conn)
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

    runtime = history_settings.resolve_runtime()
    conn = history_store.connect_db(runtime)
    history_store.init_db(conn)
    result = asyncio.run(fetch_and_export_message(runtime, conn, args.url, args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_inspect_state(args: argparse.Namespace) -> int:
    runtime = history_settings.resolve_runtime()
    conn = history_store.connect_db(runtime)
    history_store.init_db(conn)

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
    runtime = history_settings.resolve_runtime()
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
        history_session.require_telethon()
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

    runtime = history_settings.resolve_runtime()
    if getattr(args, "auth_mode", None) is None:
        args.auth_mode = runtime.default_auth_mode
    mode = args.mode if getattr(args, "async_func", None) == "sync" else args.async_func
    return asyncio.run(sync_messages(runtime, args, mode))


if __name__ == "__main__":
    sys.exit(main())
