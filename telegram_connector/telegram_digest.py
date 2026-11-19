#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.http import pooled_http_session
from common import python_environment
from common.json_io import write_json_atomic
import telegram_bridge as bridge
import telegram_history_client as history_client
from telegram_shared.bot_api import is_retryable_bot_api_error as shared_is_retryable_bot_api_error

if __package__:
    from . import digest_pipeline
else:
    import digest_pipeline

if __package__:
    from . import digest_rendering
else:
    import digest_rendering

if __package__:
    from . import digest_settings
else:
    import digest_settings

APP_DIR = MODULE_DIR
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
LAUNCHD_LOG_DIR = PROJECT_ROOT / "data" / "launchd"
DIGEST_LAST_ATTEMPT_LOG = LAUNCHD_LOG_DIR / "digest.last_attempt.json"


@dataclass
class SyncBatchPlan:
    channel: str
    limit: int


class TeeStream:
    def __init__(self, primary: Any, secondary: Any) -> None:
        self.primary = primary
        self.secondary = secondary

    def write(self, data: str) -> int:
        self.primary.write(data)
        self.secondary.write(data)
        return len(data)

    def flush(self) -> None:
        self.primary.flush()
        self.secondary.flush()

    def isatty(self) -> bool:
        primary_isatty = getattr(self.primary, "isatty", None)
        return bool(primary_isatty() if callable(primary_isatty) else False)


def audit_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_launch_context() -> dict[str, Any]:
    return {
        "source": "launchd" if os.environ.get("XPC_SERVICE_NAME") else "cli",
        "xpc_service_name": os.environ.get("XPC_SERVICE_NAME"),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "cwd": str(Path.cwd()),
        "project_root": str(PROJECT_ROOT),
        "python_executable": sys.executable,
        "stdin_is_tty": os.isatty(0),
        "stdout_is_tty": os.isatty(1),
        "stderr_is_tty": os.isatty(2),
    }


def write_digest_last_attempt(payload: dict[str, Any]) -> None:
    write_json_atomic(DIGEST_LAST_ATTEMPT_LOG, payload)


@contextlib.contextmanager
def configure_digest_cli_logging() -> Any:
    if os.environ.get("XPC_SERVICE_NAME"):
        yield
        return

    LAUNCHD_LOG_DIR.mkdir(parents=True, exist_ok=True)
    startup_log = LAUNCHD_LOG_DIR / "digest.startup.log"
    stdout_log = LAUNCHD_LOG_DIR / "digest.stdout.log"
    stderr_log = LAUNCHD_LOG_DIR / "digest.stderr.log"
    with startup_log.open("a", encoding="utf-8") as fh:
        fh.write(f"[{audit_timestamp()}] starting telegram digest from {Path.cwd()}\n")

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with stdout_log.open("a", encoding="utf-8") as stdout_fh, stderr_log.open("a", encoding="utf-8") as stderr_fh:
        sys.stdout = TeeStream(original_stdout, stdout_fh)
        sys.stderr = TeeStream(original_stderr, stderr_fh)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout = original_stdout
            sys.stderr = original_stderr


def allocate_sync_limits(channels: list[str], total_limit: int) -> list[SyncBatchPlan]:
    if not channels:
        return []
    remaining = max(0, total_limit)
    plans: list[SyncBatchPlan] = []
    for index, channel in enumerate(channels):
        channels_left = len(channels) - index
        if remaining <= 0:
            plans.append(SyncBatchPlan(channel=channel, limit=0))
            continue
        share = remaining // channels_left
        if remaining % channels_left:
            share += 1
        limit = max(1, share)
        plans.append(SyncBatchPlan(channel=channel, limit=limit))
        remaining -= limit
    return plans


async def run_sync(
    runtime: history_client.RuntimeConfig,
    *,
    channel: str | None,
    since: str,
    until: str,
    total_limit: int,
    use_ocr: bool,
    mark_read: bool,
    mode: str,
    auth_mode: str,
) -> list[dict[str, Any]]:
    conn = history_client.connect_db(runtime)
    history_client.init_db(conn)
    channels = history_client.resolve_channels_argument(runtime, channel)
    plans = allocate_sync_limits(channels, total_limit)
    try:
        results = []
        async with history_client.open_telethon_session(runtime, auth_mode) as client:
            for plan in plans:
                if plan.limit <= 0:
                    continue
                args = SimpleNamespace(
                    channel=plan.channel,
                    limit=plan.limit,
                    since=since,
                    until=until,
                    download_media=use_ocr,
                    ocr=use_ocr,
                    mark_read=mark_read,
                    auth_mode=auth_mode,
                )
                observer = digest_pipeline.digest_telemetry.CURRENT.get()
                channel_started = observer.clock() if observer else None
                try:
                    results.append(
                        await history_client.sync_one_channel(
                            conn,
                            runtime,
                            args,
                            mode,
                            plan.channel,
                            client=client,
                            auth_mode_override=auth_mode,
                        )
                    )
                except Exception as exc:
                    results.append(
                        {
                            "channel": plan.channel,
                            "mode": mode,
                            "auth_mode": auth_mode,
                            "status": "error",
                            "error": str(exc) or exc.__class__.__name__,
                        }
                    )
                finally:
                    if observer:
                        timings = observer.payload.setdefault('sync_channels', [])
                        timings.append({'channel': plan.channel,
                            'elapsed_ms': max(0, round((observer.clock() - channel_started) * 1000))})
                        observer.dirty = True
        return results
    finally:
        conn.close()


def iter_channel_messages(
    conn: Any,
    *,
    channel: str,
    since: str,
    until: str,
    max_messages: int | None = None,
) -> Any:
    channel_row = history_client.resolve_channel_filter(conn, channel)
    if channel_row is None:
        return iter(())
    base_sql = """
        SELECT
            c.channel_id,
            c.username,
            c.title,
            m.message_id,
            m.date_utc,
            m.sender_username,
            m.sender_display_name,
            m.text,
            m.forwards,
            m.replies,
            m.has_media,
            m.media_kind,
            GROUP_CONCAT(ma.ocr_text, '\n') AS ocr_text
        FROM messages m
        JOIN channels c ON c.channel_id = m.channel_id
        LEFT JOIN media_assets ma
          ON ma.channel_id = m.channel_id AND ma.message_id = m.message_id
        WHERE m.channel_id = ?
          AND m.date_utc >= ?
          AND m.date_utc <= ?
        GROUP BY
            c.channel_id,
            c.username,
            c.title,
            m.message_id,
            m.date_utc,
            m.sender_username,
            m.sender_display_name,
            m.text,
            m.forwards,
            m.replies,
            m.has_media,
            m.media_kind
    """
    params: list[Any] = [
        channel_row["channel_id"],
        history_client.parse_since_datetime(since),
        history_client.parse_until_datetime(until),
    ]
    if max_messages is not None:
        # base_sql is a local constant query and limit is parameterized.
        sql = (
            "SELECT * FROM ("  # nosec B608
            + base_sql
            + """
            ORDER BY m.date_utc DESC, m.message_id DESC
            LIMIT ?
            ) recent_messages
            ORDER BY date_utc ASC, message_id ASC
            """
        )
        params.append(max_messages)
    else:
        sql = base_sql + " ORDER BY m.date_utc ASC, m.message_id ASC"
    return (dict(row) for row in conn.execute(sql, tuple(params)))


def count_channel_messages(
    conn: Any,
    *,
    channel: str,
    since: str,
    until: str,
) -> int:
    channel_row = history_client.resolve_channel_filter(conn, channel)
    if channel_row is None:
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS message_count
        FROM messages
        WHERE channel_id = ?
          AND date_utc >= ?
          AND date_utc <= ?
        """,
        (
            channel_row["channel_id"],
            history_client.parse_since_datetime(since),
            history_client.parse_until_datetime(until),
        ),
    ).fetchone()
    if row is None:
        return 0
    return int(row["message_count"] or 0)


def resolve_display_channel_name(conn: Any, channel: str, preview_name: str = "") -> str:
    normalized_preview_name = preview_name.strip()
    if normalized_preview_name:
        return normalized_preview_name
    channel_row = history_client.resolve_channel_filter(conn, channel)
    if channel_row is None:
        return channel
    title = history_client.optional_text(channel_row["title"])
    if title:
        return title
    username = history_client.optional_text(channel_row["username"])
    if username:
        return f"@{username.lstrip('@')}"
    return channel


def is_fatal_openai_error(exc: digest_pipeline.OpenAIDigestRequestError) -> bool:
    return exc.status_code == 401


def send_digest_message(token: str, chat_id: str | int, text: str) -> None:
    recorder = digest_pipeline.digest_telemetry.CURRENT.get()
    if recorder is None:
        bridge.send_text_chunks(token, chat_id, text, parse_mode="HTML")
        return
    channel = recorder.phase.get('channel') if recorder.phase else None
    role = 'channel' if channel is not None else 'error_summary'
    with recorder.observe_delivery(channel, role=role):
        bridge.send_text_chunks(token, chat_id, text, parse_mode="HTML")


def prepare_channel_digest(
    conn: Any,
    *,
    channel: str,
    since: str,
    until: str,
    config: digest_settings.DigestConfig,
    sync_limit_reached: bool,
    summarize: Callable[[str, int, Any], tuple[int, str, bool, bool] | None],
) -> tuple[str, str | None]:
    message_rows = iter_channel_messages(
        conn, channel=channel, since=since, until=until, max_messages=None,
    )
    preview_row = next(iter_channel_messages(
        conn, channel=channel, since=since, until=until, max_messages=1,
    ), None)
    total_message_count = count_channel_messages(conn, channel=channel, since=since, until=until)
    preview_channel_name = ""
    if isinstance(preview_row, dict):
        preview_channel_name = str(preview_row.get("title") or preview_row.get("username") or "").strip()
    channel_name = resolve_display_channel_name(conn, channel, preview_channel_name)
    if not total_message_count:
        message = digest_rendering.build_channel_digest_message(
            channel_name,
            since=since,
            until=until,
            message_count=0,
            summary=("В загруженной части выбранного периода сообщений нет."
                     if sync_limit_reached else "Новых сообщений в выбранном периоде нет."),
            sync_limit_reached=sync_limit_reached,
            separator_text=config.separator_text,
        )
    elif total_message_count < config.min_messages_for_ai:
        message = digest_rendering.build_channel_digest_skip_message(
            channel_name, since=since, until=until, message_count=total_message_count,
            min_messages_for_ai=config.min_messages_for_ai,
            sync_limit_reached=sync_limit_reached, separator_text=config.separator_text,
        )
    else:
        result = summarize(channel_name, total_message_count, message_rows)
        if result is None:
            return channel_name, None
        message_count, summary, char_limit_reached, output_token_limit_reached = result
        message = digest_rendering.build_channel_digest_message(
            channel_name, since=since, until=until, message_count=message_count,
            summary=summary, char_limit_reached=char_limit_reached,
            output_token_limit_reached=output_token_limit_reached,
            output_token_limit=config.openai_max_output_tokens,
            sync_limit_reached=sync_limit_reached, separator_text=config.separator_text,
        )
    return channel_name, message


def cmd_run(args: argparse.Namespace) -> int:
    worker_started = time.monotonic()
    run_id = getattr(args, "audit_run_id", None) or digest_pipeline.digest_telemetry.build_digest_run_id()
    started_at = getattr(args, "audit_started_at", None) or audit_timestamp()
    launch_context = build_launch_context()
    audit_payload = {
        "run_id": run_id,
        "started_at": started_at,
        "updated_at": started_at,
        "finished_at": None,
        "status": "started",
        "phase": "starting",
        "wake_context": launch_context,
        "channel_override": args.channel,
        "since_override": args.since,
        "until_override": args.until,
        "auth_mode_override": args.auth_mode,
    }
    try:
        previous_audit = json.loads(DIGEST_LAST_ATTEMPT_LOG.read_text())
        if not isinstance(previous_audit, dict):
            previous_audit = None
    except (OSError, ValueError):
        previous_audit = None

    def persist_attempt(**changes: Any) -> None:
        nonlocal audit_payload
        audit_payload = {
            **audit_payload,
            **changes,
            "updated_at": audit_timestamp(),
        }
        write_digest_last_attempt(audit_payload)
        if recorder is not None:
            phase = changes.get('phase')
            mapped = {'sync_pending': 'sync', 'sync_complete': 'prepare',
                      'analyzing_channel': 'prepare', 'sending_channel': 'deliver',
                      'sending_error_summary': 'deliver'}.get(phase)
            if mapped:
                recorder.enter(mapped, changes.get('current_channel'))
            if phase == 'sync_complete':
                recorder.update(sync_results=sync_results)
                recorder.flush()

    runtime = None
    config = None
    digest_config = None
    since = None
    until = None
    auth_mode = None
    limits = None
    channels: list[str] = []
    sent_channel_messages = 0
    sync_results: list[dict[str, Any]] = []
    errors: list[str] = []
    analysis_errors: list[dict[str, Any]] = []
    conn = None
    log_conn = None
    recorder = None
    telemetry_token = None
    token = None
    chat_id = None
    http_sessions = contextlib.ExitStack()
    # Until storage opens, carry one compact receipt in the existing audit.
    # No recorder row is created while an older receipt is still pending.
    previous_receipt = digest_pipeline.digest_telemetry.supervisor_receipt(previous_audit)
    if previous_audit:
        pending = digest_pipeline.digest_telemetry.supervisor_receipt(
            previous_audit.get('previous_supervisor'))
        previous_receipt = pending or previous_receipt
    if previous_receipt:
        audit_payload['previous_supervisor'] = previous_receipt
    write_digest_last_attempt(audit_payload)
    try:
        python_environment.verify_application_environment(APP_DIR)
        http_sessions.enter_context(pooled_http_session())
        import asyncio

        runtime = history_client.resolve_runtime()
        config = history_client.load_runtime_config()
        digest_config = digest_settings.resolve_digest_config(config)
        if digest_config.sync_mode not in {"backfill", "tail", "update"}:
            raise SystemExit("digest.sync_mode must be one of 'backfill', 'tail', or 'update'.")
        default_since, default_until = digest_settings.resolve_digest_window(digest_config)
        since = args.since or default_since
        until = args.until or default_until
        since, until = digest_settings.normalize_digest_window_values(since, until)
        auth_mode = args.auth_mode or runtime.default_auth_mode
        limits = digest_settings.resolve_digest_limits(config, since, until)
        channels = history_client.resolve_channels_argument(runtime, args.channel)
        sync_timeout_seconds = digest_config.sync_total_timeout_seconds
        log_conn = history_client.connect_db(runtime)
        history_client.init_db(log_conn)
        digest_pipeline.digest_telemetry.retain_supervisor_outcome(
            log_conn, previous_receipt)
        audit_payload.pop('previous_supervisor', None)
        write_digest_last_attempt(audit_payload)
        recorder = digest_pipeline.digest_telemetry.DigestRecorder(log_conn, run_id, started_at,
                                                                 initial_clock=worker_started)
        recorder.update(since=since, until=until, channels=channels,
            settings={'model': digest_config.model,
                'sync_mode': digest_config.sync_mode, 'auth_mode': auth_mode,
                'sync_limit': limits.sync_limit,
                'messages_per_ai_pass': limits.messages_per_ai_pass,
                'message_text_max_chars': limits.message_text_max_chars,
                'message_ocr_max_chars': limits.message_ocr_max_chars,
                'message_block_max_chars': limits.message_block_max_chars,
                **{key: getattr(digest_config, key) for key in (
                    'run_total_timeout_seconds', 'termination_grace_seconds',
                    'sync_total_timeout_seconds', 'min_messages_for_ai',
                    'openai_reasoning_effort', 'openai_reasoning_summary',
                    'openai_max_output_tokens', 'openai_timeout_seconds',
                    'openai_retry_attempts', 'openai_retry_backoff_seconds')}})
        recorder.flush()
        telemetry_token = digest_pipeline.digest_telemetry.CURRENT.set(recorder)
        persist_attempt(
            phase="sync_pending",
            since=since,
            until=until,
            auth_mode=auth_mode,
            sync_mode=digest_config.sync_mode,
            run_total_timeout_seconds=digest_config.run_total_timeout_seconds,
            termination_grace_seconds=digest_config.termination_grace_seconds,
            sync_timeout_seconds=sync_timeout_seconds,
            channels=len(channels),
            resolved_channels=channels,
            limit_profile=limits.profile,
            sync_limit=limits.sync_limit,
            messages_per_ai_pass=limits.messages_per_ai_pass,
            message_text_max_chars=limits.message_text_max_chars,
            message_ocr_max_chars=limits.message_ocr_max_chars,
            message_block_max_chars=limits.message_block_max_chars,
        )
        persist_attempt(phase="syncing")
        try:
            sync_results = asyncio.run(
                asyncio.wait_for(
                    run_sync(
                        runtime,
                        channel=args.channel,
                        since=since,
                        until=until,
                        total_limit=limits.sync_limit,
                        use_ocr=digest_config.use_ocr,
                        mark_read=digest_config.mark_read,
                        mode=digest_config.sync_mode,
                        auth_mode=auth_mode,
                    ),
                    timeout=sync_timeout_seconds,
                )
            )
        except history_client.TelegramConnectionError as exc:
            summary = exc.operator_summary() + "; обработка каналов не началась"
            errors.append(summary)
            try:
                token = bridge.require_token()
                chat_id = history_client.get_config_value(config, "telegram", "default_chat_id")
                if chat_id:
                    send_digest_message(
                        token, chat_id,
                        digest_rendering.build_digest_error_message(since=since, until=until, errors=errors, separator_text=digest_config.separator_text),
                    )
            except (Exception, SystemExit) as notification_exc:
                # Do not replace the connection failure or expose credentials/provider payloads.
                errors.append(f"Digest error notification failed: {type(notification_exc).__name__}")
            raise SystemExit(summary) from exc
        except TimeoutError as exc:
            raise SystemExit(
                f"Digest sync timed out after {digest_config.sync_total_timeout_seconds} seconds."
            ) from exc
        persist_attempt(phase="sync_complete", sync_results=sync_results)
        token = bridge.require_token()
        chat_id = history_client.get_config_value(config, "telegram", "default_chat_id")
        if not chat_id:
            raise SystemExit("Missing telegram.default_chat_id for digest delivery.")
        conn = history_client.connect_db(runtime)

        sync_result_by_channel = {item.get("channel"): item for item in sync_results}
        api_key: str | None = None

        def send_channel_digest(channel_name: str, message: str) -> bool:
            nonlocal sent_channel_messages
            persist_attempt(
                phase="sending_channel",
                current_channel=channel,
                sent_channel_messages=sent_channel_messages,
                errors=errors,
            )
            try:
                send_digest_message(token, chat_id, message)
            except (Exception, SystemExit) as exc:
                if not shared_is_retryable_bot_api_error(exc, method="sendMessage"):
                    raise
                errors.append(digest_rendering.format_digest_delivery_error(channel_name, exc))
                persist_attempt(current_channel=channel, errors=errors, sent_channel_messages=sent_channel_messages)
                return False
            sent_channel_messages += 1
            persist_attempt(current_channel=channel, sent_channel_messages=sent_channel_messages)
            return True

        def summarize_prepared_channel(
            channel_name: str, total_message_count: int, message_rows: Any,
        ) -> tuple[int, str, bool, bool] | None:
            nonlocal api_key
            if api_key is None:
                api_key = digest_settings.require_openai_api_key(digest_config)
            recorder.enter('ai', channel)
            try:
                return digest_pipeline.summarize_channel_batches(
                    log_conn,
                    api_key=api_key,
                    config=replace(
                        digest_config,
                        messages_per_ai_pass=limits.messages_per_ai_pass,
                        message_text_max_chars=limits.message_text_max_chars,
                        message_ocr_max_chars=limits.message_ocr_max_chars,
                        message_block_max_chars=limits.message_block_max_chars,
                    ),
                    channel=channel,
                    channel_name=channel_name,
                    since=since,
                    until=until,
                    total_message_count=total_message_count,
                    messages=message_rows,
                )
            except digest_pipeline.OpenAIDigestRequestError as exc:
                recorder.close_phase('failed')
                error_details = {
                    "channel": channel,
                    "channel_name": channel_name,
                    "error": exc.diagnostic(),
                }
                analysis_errors.append(error_details)
                errors.append(
                    f"{digest_rendering.format_digest_channel_reference(channel, channel_name)}: "
                    f"analysis failed: {exc.operator_summary()}"
                )
                persist_attempt(
                    current_channel=channel,
                    errors=errors,
                    analysis_errors=analysis_errors,
                )
                if is_fatal_openai_error(exc):
                    raise
                return None
            except Exception as exc:
                if isinstance(exc, digest_pipeline.digest_telemetry.TelemetryStorageError):
                    raise
                recorder.close_phase('failed')
                errors.append(
                    f"{digest_rendering.format_digest_channel_reference(channel, channel_name)}: "
                    f"analysis failed: {str(exc) or exc.__class__.__name__}"
                )
                persist_attempt(current_channel=channel, errors=errors)
                return None

        for channel in channels:
            persist_attempt(
                phase="analyzing_channel",
                current_channel=channel,
                sent_channel_messages=sent_channel_messages,
                errors=errors,
                sync_results=sync_results,
            )
            sync_result = sync_result_by_channel.get(channel)
            if sync_result is None:
                errors.append(f"{channel}: not processed because the shared digest sync_limit budget was exhausted before this channel.")
                persist_attempt(current_channel=channel, errors=errors)
                continue
            if sync_result.get("status") == "error":
                errors.append(f"{channel}: sync failed: {sync_result.get('error', 'unknown error')}")
                persist_attempt(current_channel=channel, errors=errors)
                continue
            channel_name, message = prepare_channel_digest(
                conn, channel=channel, since=since, until=until, config=digest_config,
                sync_limit_reached=bool(sync_result.get("sync_limit_reached")),
                summarize=summarize_prepared_channel,
            )
            if message is not None:
                send_channel_digest(channel_name, message)

        if errors:
            persist_attempt(phase="sending_error_summary", errors=errors, sent_channel_messages=sent_channel_messages)
            try:
                send_digest_message(
                    token,
                    chat_id,
                    digest_rendering.build_digest_error_message(since=since, until=until, errors=errors, separator_text=digest_config.separator_text),
                )
            except (Exception, SystemExit) as exc:
                if not shared_is_retryable_bot_api_error(exc, method="sendMessage"):
                    raise
                errors.append(f"Digest error summary delivery failed: {str(exc) or exc.__class__.__name__}")
                persist_attempt(phase="sending_error_summary", errors=errors, sent_channel_messages=sent_channel_messages)
        payload = {
            "status": "partial" if errors else "sent",
            "channels": len(channels),
            "sent_channel_messages": sent_channel_messages,
            "since": since,
            "until": until,
            "limit_profile": limits.profile,
            "sync_limit": limits.sync_limit,
            "messages_per_ai_pass": limits.messages_per_ai_pass,
            "message_text_max_chars": limits.message_text_max_chars,
            "message_ocr_max_chars": limits.message_ocr_max_chars,
            "message_block_max_chars": limits.message_block_max_chars,
            "sync_mode": digest_config.sync_mode,
            "run_total_timeout_seconds": digest_config.run_total_timeout_seconds,
            "termination_grace_seconds": digest_config.termination_grace_seconds,
            "auth_mode": auth_mode,
            "sync_results": sync_results,
            "errors": errors,
            "analysis_errors": analysis_errors,
        }
        recorder.enter('cleanup')
        http_sessions.close()
        recorder.finish(payload['status'], sent_channel_messages=sent_channel_messages,
                        errors=errors, analysis_errors=analysis_errors)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        persist_attempt(
            current_channel=None,
            **payload,
            phase="completed",
            finished_at=audit_timestamp(),
        )
        return 0
    except BaseException as exc:
        if recorder is not None and not isinstance(exc, digest_pipeline.digest_telemetry.TelemetryStorageError):
            try:
                recorder.finish('failed', error=type(exc).__name__)
            except Exception as telemetry_exc:
                exc.add_note(f'Digest telemetry finalization failed: {type(telemetry_exc).__name__}')
        persist_attempt(
            finished_at=audit_timestamp(),
            status="failed",
            since=since,
            until=until,
            auth_mode=auth_mode,
            sync_results=sync_results,
            errors=errors,
            analysis_errors=analysis_errors,
            error=str(exc) or exc.__class__.__name__,
        )
        raise
    finally:
        if telemetry_token is not None:
            digest_pipeline.digest_telemetry.CURRENT.reset(telemetry_token)
        try:
            if conn is not None:
                conn.close()
            if log_conn is not None:
                log_conn.close()
        finally:
            http_sessions.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Morning Telegram digest runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run sync + AI digest + Telegram delivery using config defaults.")
    run.add_argument("--channel", help="Optional channel or comma-separated channel list override.")
    run.add_argument("--since", help="Optional override for digest since window.")
    run.add_argument("--until", help="Optional override for digest until window.")
    run.add_argument("--auth-mode", choices=["auto", "bot", "user"], help="Optional auth mode override.")
    run.add_argument("--audit-run-id", help=argparse.SUPPRESS)
    run.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "command", None) == "run":
        args.audit_run_id = args.audit_run_id or digest_pipeline.digest_telemetry.build_digest_run_id()
        args.audit_started_at = audit_timestamp()
    with configure_digest_cli_logging():
        return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
