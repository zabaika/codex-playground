"""Durable analytics history pages, reservations, recovery and FTS backfill."""
import asyncio
import json
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process
from common import sqlite as common_sqlite
from telegram_shared.async_retry import await_with_awake_timeout
if __package__:
    from . import history_messages, history_session, history_settings, history_store
else:
    import history_messages
    import history_session
    import history_settings
    import history_store


def backfill_fts_page(store, job_id: str, *, generation: int) -> dict[str, Any]:
    """Read and replace the current source revision under one short writer lock."""
    conn = store.conn
    with common_sqlite.write_tx(conn, immediate=True):
        job = store.require_running(job_id, generation=generation)
        if job["kind"] != "fts_backfill" or job["admission_key"] != history_store.FTS_INDEX_ADMISSION_KEY:
            raise ValueError("Invalid internal index job")
        if not history_store._message_fts_enabled(conn):
            raise ValueError("fts_unavailable")
        state = job["checkpoint"]["fts"]
        if (state["state"] != "building" or state["version"] != history_store.FTS_INDEX_VERSION
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
            history_store._refresh_message_fts_no_tx(conn, row[1], row[2])
        state["indexed_messages"] += len(rows)
        if rows:
            state["cursor"] = rows[-1][0]
        if len(rows) < size:
            state["state"] = "ready"
        _save_history_checkpoint_no_tx(store, job)
        if state["state"] == "ready":
            conn.execute("UPDATE analysis_jobs SET status='completed',finished_at=? WHERE job_id=?", (history_store.now_utc(), job_id))
    return state


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
    now = history_store.now_utc()
    previous = conn.execute("SELECT last_backfill_message_id,last_tail_message_id FROM sync_state WHERE channel_id=?", (entity.id,)).fetchone()
    backfill = source["mode"] == "backfill"
    old_id = previous[0 if backfill else 1] if previous else None
    highest = max(old_id or 0, source.get("highest_seen_id") or 0) or None
    history_store._update_sync_state_no_tx(conn, entity.id,
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
        history_store._upsert_channel_no_tx(store.conn, entity)
        for item in part:
            if item["row"] is not None:
                history_store._upsert_prepared_message_no_tx(store.conn, item["row"])
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
    if history_store._telegram_peer_kind(name) == "user":
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
        kind = history_store._telegram_peer_kind(type(entity).__name__)
        if source is not None and source.get("peer_kind") is not None and kind is not None and source["peer_kind"] != kind:
            raise history_store.TelegramPeerIdentityError("Analysis source peer kind changed")
        if source is None:
            source = {"mode": mode, "stop_id": history_store.latest_stored_message_id(store.conn, entity.id) if mode == "update" else None,
                      "cursor": 0, "state": "running", "returned_messages": 0, "peer_kind": kind}
            sources[str(entity.id)] = source
        elif source["mode"] != mode:
            raise ValueError("Analysis source mode changed")
        elif source["state"] != "complete":
            source["state"] = "running"
            source.pop("reason", None)
        history_store._upsert_channel_no_tx(store.conn, entity)
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
            transient = flood or isinstance(exc, (errors.ServerError, errors.InvalidDCError)) or history_session.is_retryable_connection_error(exc)
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


async def sync_analysis_source(store, job_id: str, runtime: history_settings.RuntimeConfig, *, generation: int,
                                channel: str, mode: str = "backfill", client=None, deadline=None, resolved_entity=None):
    job = _analysis_history_job(store, job_id, generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    if channel not in request["channels"]:
        raise ValueError("Analysis channel is outside the admitted scope")
    deadline = deadline if deadline is not None else common_process.continuous_time() + settings["bridge"]["worker_process_timeout_seconds"]
    profile = replace(runtime, **settings["telethon"], default_auth_mode=settings["auth"]["default_mode"],
                      public_auth_mode=settings["auth"]["public_channel_mode"], private_auth_mode=settings["auth"]["private_channel_mode"])
    if client is None:
        auth = history_session.resolve_auth_mode(profile, profile.default_auth_mode, channel)
        async with history_session.open_telethon_session(profile, auth, managed_requests=True) as owned_client:
            return await sync_analysis_source(store, job_id, profile, generation=generation, channel=channel,
                                              mode=mode, client=owned_client, deadline=deadline)
    entity = resolved_entity
    if entity is None:
        try:
            entity, _ = await _analysis_rpc_retry(store, job_id, generation, settings, deadline,
                                                  lambda _: history_session.resolve_channel_entity(client, channel))
        except AnalysisHistoryRequestError as exc:
            return _record_analysis_source_error(store, job_id, generation, channel, exc)
    access_error = _analysis_source_access_error(entity)
    if access_error is not None:
        return _record_analysis_source_error(store, job_id, generation, channel, access_error)
    try:
        source = _begin_analysis_source(store, job_id, generation, channel, entity, mode)
    except history_store.TelegramPeerIdentityError:
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
            except history_store.TelegramPeerIdentityError:
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
                                                               lambda _: history_session.resolve_sender_metadata(entity, message))
                        username, display_name = sender
                    except AnalysisHistoryRequestError as exc:
                        if exc.reason == "run_deadline":
                            raise
                        username, display_name = history_messages.direct_sender_metadata(message)
                    media_path, media_size = None, None
                    if (settings["processing"]["ocr"] and history_messages.is_image_media_kind(history_messages.message_media_kind(message))
                            and history_store.media_needs_download(store.conn, entity.id, message.id)):
                        media, _ = await _analysis_rpc_retry(store, job_id, generation, settings, deadline,
                                                              lambda _: history_session.download_media_if_present(profile, client, message, entity.id))
                        media_path, media_size = media
                    row = history_store.prepare_history_write(history_store.StagedMessageWrite(entity, message, username, display_name, media_path, media_size))
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
    kind = history_store._telegram_peer_kind(type(entity).__name__)
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
              "returned_messages": 0, "peer_kind": history_store._telegram_peer_kind(type(entity).__name__)}
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
            raise history_store.TelegramPeerIdentityError("Analysis source identity changed")
        history_store._upsert_channel_no_tx(store.conn, entity)
        kind = history_store._telegram_peer_kind(type(entity).__name__)
        source = sources.get(str(entity.id))
        if source is not None and source.get("peer_kind") is not None and kind is not None and source["peer_kind"] != kind:
            raise history_store.TelegramPeerIdentityError("Analysis source peer kind changed")
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
                                                  lambda _: history_session.resolve_channel_entity(owned_client, channel))
        except AnalysisHistoryRequestError as exc:
            return _record_analysis_source_error(store, job_id, generation, channel, exc)
        access_error = _analysis_source_access_error(entity)
        if access_error is not None:
            return _record_analysis_source_error(store, job_id, generation, channel, access_error)
        try:
            source = _plan_analysis_history_source(store, job_id, generation, channel, entity)
        except history_store.TelegramPeerIdentityError:
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
            auth = history_session.resolve_auth_mode(profile, profile.default_auth_mode, channel)
            try:
                async with history_session.open_telethon_session(profile, auth, managed_requests=True) as owned_client:
                    await one(channel, owned_client)
            except history_session.TelegramConnectionError as exc:
                from telethon import errors
                if not (history_session.is_retryable_connection_error(exc.cause) or isinstance(exc.cause, (errors.ServerError, errors.InvalidDCError))):
                    raise
                _record_analysis_source_error(store, job_id, generation, channel,
                                                AnalysisHistoryRequestError("session_unavailable"))
            except TimeoutError:
                _record_analysis_source_error(store, job_id, generation, channel,
                                                AnalysisHistoryRequestError("session_wait_timeout"))
    return store.get_job(job_id)["checkpoint"].get("history", {})


def commit_analysis_history_page(store, job_id: str, *, generation: int, entity: Any,
                                  writes: list[history_store.StagedMessageWrite], expected_cursor: int, next_cursor: int,
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
        history_store._upsert_channel_no_tx(conn, entity)
        for item in writes:
            history_store._upsert_message_no_tx(conn, entity, item.message, item.sender_username, item.sender_display_name,
                                  item.downloaded_path, item.downloaded_size)
        if complete:
            _mark_history_source_complete_no_tx(conn, entity, source)
        store._write_checkpoint_no_tx(job_id, encoded, generation=generation, downloaded_delta=returned_messages)
    return source
