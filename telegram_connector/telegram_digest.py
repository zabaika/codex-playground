#!/usr/bin/env python3
import argparse
import contextlib
import html
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any
from urllib import error, request

import telegram_bridge as bridge
import telegram_history_client as history_client
from telegram_shared.openai_usage import OpenAIUsage
from telegram_shared.openai_usage import PromptCacheInfo
from telegram_shared.openai_usage import build_prompt_cache_info as shared_build_prompt_cache_info
from telegram_shared.openai_usage import common_prefix_length as shared_common_prefix_length
from telegram_shared.openai_usage import extract_usage as shared_extract_usage
from telegram_shared.openai_usage import hash_cache_key as shared_hash_cache_key
from telegram_shared.openai_usage import log_openai_usage as shared_log_openai_usage


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
LAUNCHD_LOG_DIR = PROJECT_ROOT / "data" / "launchd"
DIGEST_LAST_ATTEMPT_LOG = LAUNCHD_LOG_DIR / "digest.last_attempt.json"

OPENAI_DIGEST_RETRY_ATTEMPTS = 3
DEFAULT_DIGEST_SYNC_TOTAL_TIMEOUT_SECONDS = 1800
DIGEST_PROMPT_REQUIRED_KEYS = (
    "system_instructions",
    "shared_prompt_prefix",
    "single_digest_template",
    "batch_digest_template",
    "final_digest_template",
)
MAIN_TOPICS_DAY_HEADING = "Главные темы дня"
MOST_POPULAR_HEADING = "Наиболее популярное"
OPEN_QUESTIONS_HEADING = "Незакрытые вопросы/продолжения"
QUESTION_ANSWER_LINKS_HEADING = "Связки вопрос-ответ/развитие темы"


@dataclass
class DigestConfig:
    time: str
    since: str
    until: str
    model: str
    sync_mode: str
    sync_total_timeout_seconds: int
    messages_per_ai_pass: int
    message_text_max_chars: int
    message_ocr_max_chars: int
    message_block_max_chars: int
    min_messages_for_ai: int
    separator_text: str
    mark_read: bool
    use_ocr: bool
    system_instructions: str
    shared_prompt_prefix: str
    single_prompt_template: str
    batch_prompt_template: str
    final_prompt_template: str
    openai_api_key: str


@dataclass
class ChannelDigestInput:
    channel_name: str
    message_count: int
    message_block: str
    hit_char_limit: bool


@dataclass
class SyncBatchPlan:
    channel: str
    limit: int


@dataclass
class DigestLimits:
    profile: str
    sync_limit: int
    messages_per_ai_pass: int
    message_text_max_chars: int
    message_ocr_max_chars: int
    message_block_max_chars: int


@dataclass
class OpenAIResult:
    response_id: str | None
    text: str
    usage: OpenAIUsage


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


def build_digest_run_id() -> str:
    return f"digest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"


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
    LAUNCHD_LOG_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = DIGEST_LAST_ATTEMPT_LOG.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(DIGEST_LAST_ATTEMPT_LOG)


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


def parse_bool(value: str, default: bool = False) -> bool:
    raw = value.strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_digest_prompt_file(config: dict[str, Any], *, base_dir: Path | None = None) -> Path:
    raw_path = history_client.get_config_value(config, "digest_prompts", "file")
    if not raw_path:
        raise SystemExit(
            "Missing digest_prompts.file in runtime config. Point it to the version-controlled digest prompt TOML file."
        )
    prompt_file = Path(raw_path).expanduser()
    if not prompt_file.is_absolute():
        prompt_file = (base_dir or history_client.RUNTIME_LOCAL_FILE.parent) / prompt_file
    return prompt_file


def load_digest_prompts(config: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, str]:
    prompt_file = resolve_digest_prompt_file(config, base_dir=base_dir)
    if not prompt_file.exists():
        raise SystemExit(f"Digest prompt file not found: {prompt_file}")
    with prompt_file.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"Digest prompt file must contain a TOML object: {prompt_file}")
    missing = [key for key in DIGEST_PROMPT_REQUIRED_KEYS if not history_client.get_config_value(data, "digest_prompts", key)]
    if missing:
        missing_keys = ", ".join(f"digest_prompts.{key}" for key in missing)
        raise SystemExit(f"Missing {missing_keys} in digest prompt file: {prompt_file}")
    return {
        key: history_client.get_config_value(data, "digest_prompts", key)
        for key in DIGEST_PROMPT_REQUIRED_KEYS
    }


def resolve_digest_config(config: dict[str, Any]) -> DigestConfig:
    prompts = load_digest_prompts(config)
    model = history_client.get_config_value(config, "processing", "model")
    if not model:
        raise SystemExit(
            "Missing processing.model in runtime config. Put the default AI model into [processing].model."
        )
    raw_min_messages_for_ai = history_client.get_config_value(config, "digest", "min_messages_for_ai") or "1"
    raw_sync_total_timeout_seconds = (
        history_client.get_config_value(config, "digest", "sync_total_timeout_seconds")
        or str(DEFAULT_DIGEST_SYNC_TOTAL_TIMEOUT_SECONDS)
    )
    try:
        min_messages_for_ai = max(0, int(raw_min_messages_for_ai))
        sync_total_timeout_seconds = max(1, int(raw_sync_total_timeout_seconds))
    except ValueError as exc:
        raise SystemExit(
            "Invalid digest.min_messages_for_ai or digest.sync_total_timeout_seconds in runtime config."
        ) from exc
    return DigestConfig(
        time=history_client.get_config_value(config, "digest", "time") or "08:00",
        since=history_client.get_config_value(config, "digest", "since") or "yesterday",
        until=history_client.get_config_value(config, "digest", "until") or "yesterday",
        model=model,
        sync_mode=history_client.get_config_value(config, "digest", "sync_mode") or "update",
        sync_total_timeout_seconds=sync_total_timeout_seconds,
        messages_per_ai_pass=0,
        message_text_max_chars=0,
        message_ocr_max_chars=0,
        message_block_max_chars=0,
        min_messages_for_ai=min_messages_for_ai,
        separator_text=(history_client.get_config_value(config, "digest", "separator_text") or "").strip(),
        mark_read=parse_bool(
            history_client.get_config_value(config, "digest", "mark_read"),
            default=True,
        ),
        use_ocr=parse_bool(
            history_client.get_config_value(config, "processing", "ocr"),
            default=True,
        ),
        system_instructions=prompts["system_instructions"],
        shared_prompt_prefix=prompts["shared_prompt_prefix"],
        single_prompt_template=prompts["single_digest_template"],
        batch_prompt_template=prompts["batch_digest_template"],
        final_prompt_template=prompts["final_digest_template"],
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip() or history_client.get_config_value(config, "secrets", "openai_api_key"),
    )


def get_nested_section(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}


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


def resolve_digest_window(config: DigestConfig, *, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    today = current.date()
    since_value = config.since or "yesterday"
    until_value = config.until or "yesterday"
    return resolve_relative_date_token(since_value, today), resolve_relative_date_token(until_value, today)


def normalize_digest_window_values(since: str, until: str, *, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    today = current.date()
    return resolve_relative_date_token(since, today), resolve_relative_date_token(until, today)


def digest_profile_name(since: str, until: str) -> str:
    since_dt = history_client.parse_filter_datetime_value(since)
    until_dt = history_client.parse_filter_datetime_value(until, end_of_day=True)
    if since_dt is None or until_dt is None:
        return "day"
    span_days = max(1, int((until_dt.date() - since_dt.date()).days) + 1)
    if span_days <= 1:
        return "day"
    if span_days <= 7:
        return "week"
    return "month"


def resolve_digest_limits(config: dict[str, Any], since: str, until: str) -> DigestLimits:
    profile = digest_profile_name(since, until)
    shared_ai_section = get_nested_section(config, "digest_ai")
    section = get_nested_section(config, "digest_limits", profile)
    raw_sync_limit = str(section.get("sync_limit", "")).strip()
    raw_messages_per_ai_pass = str(shared_ai_section.get("messages_per_ai_pass", "")).strip()
    raw_message_text_max_chars = str(shared_ai_section.get("message_text_max_chars", "")).strip()
    raw_message_ocr_max_chars = str(shared_ai_section.get("message_ocr_max_chars", "")).strip()
    raw_message_block_max_chars = str(shared_ai_section.get("message_block_max_chars", "")).strip()
    if not raw_sync_limit:
        raise SystemExit(f"Missing digest_limits.{profile}.sync_limit in runtime config.")
    if not raw_messages_per_ai_pass:
        raise SystemExit("Missing digest_ai.messages_per_ai_pass in runtime config.")
    if not raw_message_text_max_chars:
        raise SystemExit("Missing digest_ai.message_text_max_chars in runtime config.")
    if not raw_message_ocr_max_chars:
        raise SystemExit("Missing digest_ai.message_ocr_max_chars in runtime config.")
    if not raw_message_block_max_chars:
        raise SystemExit("Missing digest_ai.message_block_max_chars in runtime config.")
    try:
        sync_limit = int(raw_sync_limit)
        messages_per_ai_pass = int(raw_messages_per_ai_pass)
        message_text_max_chars = int(raw_message_text_max_chars)
        message_ocr_max_chars = int(raw_message_ocr_max_chars)
        message_block_max_chars = int(raw_message_block_max_chars)
    except ValueError as exc:
        raise SystemExit(
            "Invalid integer in digest_ai.messages_per_ai_pass, digest_ai.message_text_max_chars, "
            "digest_ai.message_ocr_max_chars, digest_ai.message_block_max_chars, or "
            f"digest_limits.{profile}.sync_limit."
        ) from exc
    return DigestLimits(
        profile=profile,
        sync_limit=max(1, sync_limit),
        messages_per_ai_pass=max(1, messages_per_ai_pass),
        message_text_max_chars=max(0, message_text_max_chars),
        message_ocr_max_chars=max(0, message_ocr_max_chars),
        message_block_max_chars=max(1, message_block_max_chars),
    )


def require_openai_api_key(config: DigestConfig) -> str:
    raw_value = config.openai_api_key.strip()
    if not raw_value:
        raise SystemExit(
            "Missing OpenAI API key. Put it into telegram_connector/config/runtime.local.toml under [secrets].openai_api_key."
        )
    return history_client.resolve_secret_value(raw_value, "OpenAI API key")


def batch_overlap_size(batch_size: int) -> int:
    if batch_size <= 3:
        return 1
    return min(10, max(2, batch_size // 10))


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
            try:
                results.append(await history_client.sync_one_channel(conn, runtime, args, mode, plan.channel))
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
        sql = (
            "SELECT * FROM ("
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


def render_sender_label(message: dict[str, Any]) -> str:
    display_name = (message.get("sender_display_name") or "").strip()
    username = (message.get("sender_username") or "").strip()
    if display_name and username:
        return f"{display_name} (@{username.lstrip('@')})"
    if display_name:
        return display_name
    if username:
        return f"@{username.lstrip('@')}"
    return "<unknown sender>"


def build_message_link(message: dict[str, Any]) -> str | None:
    username = (message.get("username") or "").strip().lstrip("@")
    message_id = message.get("message_id")
    if not message_id:
        return None
    if username:
        return f"https://t.me/{username}/{message_id}"
    channel_id = message.get("channel_id")
    if channel_id is None:
        return None
    return f"https://t.me/c/{channel_id}/{message_id}"


def truncate_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def render_message_entry(
    message: dict[str, Any],
    *,
    message_text_max_chars: int,
    message_ocr_max_chars: int,
) -> str:
    base_parts = [
        f"id={message['message_id']}",
        f"date={message['date_utc']}",
        f"sender={render_sender_label(message)}",
        f"link={build_message_link(message) or '<no link>'}",
        f"forwards={message.get('forwards') if message.get('forwards') is not None else 0}",
        f"replies={message.get('replies') if message.get('replies') is not None else 0}",
    ]
    text_value = str(message.get("text") or "<no text>")
    ocr_text = str(message.get("ocr_text") or "").strip()
    parts = [*base_parts, f"text={truncate_text(text_value, message_text_max_chars)}"]
    if ocr_text and message_ocr_max_chars > 0:
        parts.append(f"ocr={truncate_text(ocr_text, message_ocr_max_chars)}")
    return "\n".join(parts)


def render_message_block(
    messages: Any,
    *,
    max_chars: int,
    message_text_max_chars: int,
    message_ocr_max_chars: int,
) -> ChannelDigestInput:
    chunks: list[str] = []
    total = 0
    message_count = 0
    channel_name = ""
    hit_char_limit = False
    for item in messages:
        if not channel_name:
            channel_name = item.get("title") or item.get("username") or ""
        block = render_message_entry(
            item,
            message_text_max_chars=message_text_max_chars,
            message_ocr_max_chars=message_ocr_max_chars,
        )
        if total and total + len(block) + 2 > max_chars:
            hit_char_limit = True
            break
        chunks.append(block)
        total += len(block) + 2
        message_count += 1
    return ChannelDigestInput(
        channel_name=channel_name,
        message_count=message_count,
        message_block="\n\n".join(chunks),
        hit_char_limit=hit_char_limit,
    )


def iter_rendered_message_batches(
    messages: list[dict[str, Any]],
    *,
    batch_size: int,
    max_chars: int,
    message_text_max_chars: int,
    message_ocr_max_chars: int,
) -> Any:
    index = 0
    while index < len(messages):
        window = messages[index : index + batch_size]
        rendered = render_message_block(
            window,
            max_chars=max_chars,
            message_text_max_chars=message_text_max_chars,
            message_ocr_max_chars=message_ocr_max_chars,
        )
        actual_count = rendered.message_count
        if actual_count <= 0:
            actual_count = 1
            rendered = render_message_block(
                window[:1],
                max_chars=max_chars,
                message_text_max_chars=message_text_max_chars,
                message_ocr_max_chars=message_ocr_max_chars,
            )
        batch = window[:actual_count]
        yield batch, rendered
        if index + actual_count >= len(messages):
            break
        overlap = batch_overlap_size(min(batch_size, actual_count))
        index += max(1, actual_count - overlap)


def build_batch_digest_prompt(
    shared_prefix: str,
    template: str,
    channel_name: str,
    since: str,
    until: str,
    batch_index: int,
    message_count: int,
    message_block: str,
    previous_batch_summary: str,
) -> str:
    prefix = shared_prefix.format(
        channel_name=channel_name,
        since=since,
        until=until,
    ).strip()
    body = template.format(
        channel_name=channel_name,
        since=since,
        until=until,
        batch_index=batch_index,
        message_count=message_count,
        message_block=message_block,
        previous_batch_summary=previous_batch_summary or "<no previous batch>",
    ).strip()
    return f"{prefix}\n\n{body}"


def build_single_digest_prompt(
    shared_prefix: str,
    template: str,
    channel_name: str,
    since: str,
    until: str,
    message_count: int,
    message_block: str,
) -> str:
    prefix = shared_prefix.format(
        channel_name=channel_name,
        since=since,
        until=until,
    ).strip()
    body = template.format(
        channel_name=channel_name,
        since=since,
        until=until,
        message_count=message_count,
        message_block=message_block,
    ).strip()
    return f"{prefix}\n\n{body}"


def build_final_digest_prompt(
    shared_prefix: str,
    template: str,
    channel_name: str,
    since: str,
    until: str,
    message_count: int,
    batch_count: int,
    batch_summary_block: str,
) -> str:
    prefix = shared_prefix.format(
        channel_name=channel_name,
        since=since,
        until=until,
    ).strip()
    body = template.format(
        channel_name=channel_name,
        since=since,
        until=until,
        message_count=message_count,
        batch_count=batch_count,
        batch_summary_block=batch_summary_block,
    ).strip()
    return f"{prefix}\n\n{body}"


def iter_message_batches(messages: Any, batch_size: int) -> Any:
    overlap = batch_overlap_size(batch_size)
    buffer: list[dict[str, Any]] = []
    yielded = False
    for item in messages:
        buffer.append(item)
        if len(buffer) >= batch_size:
            yielded = True
            yield list(buffer)
            buffer = list(buffer[-overlap:])
    if buffer and (not yielded or len(buffer) > overlap):
        yield list(buffer)


def extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    chunks: list[str] = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def extract_usage(response: dict[str, Any], latency_ms: int) -> OpenAIUsage:
    return shared_extract_usage(response, latency_ms)


def build_prompt_cache_key(*, model: str, channel: str, profile: str, stage: str) -> str:
    return shared_hash_cache_key(
        "digest",
        model.strip().lower() or "unknown-model",
        channel.strip().lstrip("@").lower() or "unknown-channel",
        profile.strip().lower() or "day",
        stage.strip().lower() or "unknown-stage",
    )


def build_prompt_cache_info(
    *,
    stage: str,
    model: str,
    cache_channel: str,
    display_channel: str,
    since: str,
    until: str,
    system_instructions: str,
    shared_prompt_prefix: str,
    prompt: str,
) -> PromptCacheInfo:
    profile = digest_profile_name(since, until)
    prefix_text = shared_prompt_prefix.format(
        channel_name=display_channel,
        since=since,
        until=until,
    ).strip()
    return shared_build_prompt_cache_info(
        cache_key=build_prompt_cache_key(model=model, channel=cache_channel, profile=profile, stage=stage),
        system_instructions=system_instructions,
        prompt_text=prompt,
        shared_prefix=prefix_text,
    )


def common_prefix_length(left: str, right: str) -> int:
    return shared_common_prefix_length(left, right)


def log_openai_usage(
    conn: Any,
    *,
    stage: str,
    channel: str,
    since: str,
    until: str,
    model: str,
    request_index: int,
    message_count: int,
    status: str,
    cache_info: PromptCacheInfo,
    prompt_text: str,
    usage: OpenAIUsage | None = None,
    response_id: str | None = None,
    error: str | None = None,
) -> None:
    shared_log_openai_usage(
        conn,
        feature="digest",
        created_at=history_client.now_utc(),
        stage=stage,
        channel=channel,
        since=since,
        until=until,
        model=model,
        request_index=request_index,
        message_count=message_count,
        status=status,
        cache_info=cache_info,
        prompt_text=prompt_text,
        usage=usage,
        response_id=response_id,
        error=history_client.optional_text(error),
    )


def run_openai_digest(
    api_key: str,
    model: str,
    system_instructions: str,
    prompt: str,
    *,
    prompt_cache_key: str,
) -> OpenAIResult:
    payload = {
        "model": model,
        "instructions": system_instructions,
        "input": prompt,
        "prompt_cache_key": prompt_cache_key,
        "max_output_tokens": 1200,
    }
    req = request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_network_error: BaseException | None = None
    response: dict[str, Any] | None = None
    latency_ms = 0
    for attempt in range(1, OPENAI_DIGEST_RETRY_ATTEMPTS + 1):
        started_at = time.perf_counter()
        try:
            with request.urlopen(req, timeout=120) as resp:
                response = json.loads(resp.read().decode("utf-8"))
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            break
        except error.HTTPError as exc:
            raise SystemExit(f"OpenAI API HTTP {exc.code} while creating digest.") from exc
        except (TimeoutError, error.URLError, OSError) as exc:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            error_text = str(exc).lower()
            is_retryable = isinstance(exc, (TimeoutError, error.URLError)) or "timed out" in error_text or "connection reset" in error_text
            if not is_retryable or attempt >= OPENAI_DIGEST_RETRY_ATTEMPTS:
                last_network_error = exc
                break
            time.sleep(attempt)
            last_network_error = exc
    if response is None:
        if isinstance(last_network_error, error.URLError):
            raise SystemExit(
                f"OpenAI API request failed while creating digest after {OPENAI_DIGEST_RETRY_ATTEMPTS} attempts."
            ) from last_network_error
        if isinstance(last_network_error, (TimeoutError, OSError)):
            raise SystemExit(
                f"OpenAI API request timed out while creating digest after {OPENAI_DIGEST_RETRY_ATTEMPTS} attempts."
            ) from last_network_error
        raise SystemExit("OpenAI API request failed while creating digest.")
    text = extract_response_text(response)
    if not text:
        raise SystemExit("OpenAI API returned an empty digest response.")
    return OpenAIResult(
        response_id=history_client.optional_text(response.get("id")),
        text=text,
        usage=extract_usage(response, latency_ms),
    )


def build_digest_message(
    channel_summaries: list[tuple[str, int, str]],
    *,
    since: str,
    until: str,
) -> str:
    header = f"Утренний дайджест\nПериод UTC: {since} .. {until}"
    if not channel_summaries:
        return f"{header}\n\nНовых сообщений для анализа нет."
    sections = [header]
    for channel_name, count, summary in channel_summaries:
        sections.append(f"{channel_name} ({count} сообщений)\n{summary}")
    return "\n\n".join(sections)


def format_digest_summary_for_telegram(summary: str) -> str:
    lines = [line.strip() for line in summary.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    compact_lines = [line for line in lines if line]
    if not compact_lines:
        return ""
    formatted: list[str] = []
    current_section = ""
    previous_line_kind = ""

    def escape_line(value: str) -> str:
        escaped = html.escape(value, quote=False)
        escaped = escaped.replace("&lt;https://", "https://").replace("&lt;http://", "http://")
        escaped = escaped.replace("&gt;", "")
        return escaped

    def format_main_topics_line(value: str) -> str:
        match = re.match(r"^((?:[-•]\s+|\d+\.\s+)?)([^:]{2,200}):(.*)$", value)
        if not match:
            return escape_line(value)
        marker, lead, tail = match.groups()
        return f"{escape_line(marker)}<b>{escape_line(lead)}:</b>\n{escape_line(tail).lstrip()}"

    def normalize_lead_line(value: str) -> str:
        normalized = re.sub(r"^(?:Главная тема дня|Главные темы дня|Главные темы)\s*[:\-—]\s*", "", value, flags=re.IGNORECASE).strip()
        if normalized != value.strip():
            return normalized or value.strip()
        return value.strip()

    def extract_lead_heading(value: str) -> str:
        if re.match(r"^(?:Главная тема дня|Главные темы дня|Главные темы)\s*[:\-—]\s*", value, flags=re.IGNORECASE):
            return MAIN_TOPICS_DAY_HEADING
        return MAIN_TOPICS_DAY_HEADING

    def is_popular_link_line(value: str) -> bool:
        return bool(re.match(r"^<?https?://\S+>?\s*-\s+\S", value, flags=re.IGNORECASE))

    def resolve_heading(value: str) -> tuple[str, str] | None:
        heading_patterns = (
            (MAIN_TOPICS_DAY_HEADING, r"^(?:Главная тема дня|Главные темы дня|Главные темы)\b"),
            (MOST_POPULAR_HEADING, r"^Наиболее популярное\b"),
            (OPEN_QUESTIONS_HEADING, r"^Незакрытые вопросы"),
            (QUESTION_ANSWER_LINKS_HEADING, r"^Связки вопрос-ответ"),
        )
        for canonical_heading, pattern in heading_patterns:
            match = re.match(pattern, value, flags=re.IGNORECASE)
            if not match:
                continue
            body_match = re.match(rf"^{pattern[1:]}\s*[:\-—]\s*(.*)$", value, flags=re.IGNORECASE)
            body = body_match.group(1).strip() if body_match else ""
            return canonical_heading, body
        return None

    first_line = compact_lines[0]
    if (
        len(compact_lines) > 1
        and not (first_line.startswith("- ") or first_line.startswith("• ") or first_line.startswith("<http"))
    ):
        current_section = "main_topics"
        formatted.extend(
            [
                f"<b>{escape_line(extract_lead_heading(first_line))}</b>",
                escape_line(normalize_lead_line(first_line)),
                "",
            ]
        )
        compact_lines = compact_lines[1:]
        previous_line_kind = "text"

    for line in compact_lines:
        heading = resolve_heading(line)
        is_heading = heading is not None
        is_list_item = line.startswith("- ") or line.startswith("• ") or is_popular_link_line(line)

        if is_heading:
            heading_line, body_line = heading
            if heading_line == MAIN_TOPICS_DAY_HEADING:
                current_section = "main_topics"
            elif heading_line == MOST_POPULAR_HEADING:
                current_section = "popular"
            else:
                current_section = "regular"
            if formatted and formatted[-1] != "":
                formatted.append("")
            formatted.append(f"<b>{escape_line(heading_line)}</b>")
            if body_line:
                if current_section == "main_topics":
                    formatted.append(format_main_topics_line(body_line))
                else:
                    formatted.append(escape_line(body_line))
                previous_line_kind = "text"
            else:
                previous_line_kind = "heading"
            continue

        if is_list_item:
            is_dense_section = current_section == "popular"
            if (
                formatted
                and not is_dense_section
                and formatted[-1] != ""
                and previous_line_kind in {"text", "list"}
            ):
                formatted.append("")
            if current_section == "main_topics":
                formatted.append(format_main_topics_line(line))
            else:
                formatted.append(escape_line(line))
            previous_line_kind = "list"
            continue

        if formatted and formatted[-1] != "":
            formatted.append("")
        if current_section == "main_topics":
            formatted.append(format_main_topics_line(line))
        else:
            formatted.append(escape_line(line))
        previous_line_kind = "text"

    while formatted and formatted[-1] == "":
        formatted.pop()
    return "\n".join(formatted)


def build_channel_digest_message(
    channel_name: str,
    *,
    since: str,
    until: str,
    message_count: int,
    summary: str,
    char_limit_reached: bool = False,
    sync_limit_reached: bool = False,
    separator_text: str = "",
) -> str:
    formatted_summary = format_digest_summary_for_telegram(summary)
    header = "\n\n".join(
        [
            f"<b>{html.escape(channel_name, quote=False)}</b>",
            "\n".join(
                [
                    f"Период UTC: {html.escape(since, quote=False)} .. {html.escape(until, quote=False)}",
                    f"Сообщений в анализе: {message_count}",
                ]
            ),
        ]
    )
    if not formatted_summary:
        body = header
    else:
        body = f"{header}\n\n{formatted_summary}"
    warnings: list[str] = []
    if char_limit_reached:
        warnings.append(
            "<i>Предупреждение: по этому чату был достигнут лимит "
            "message_block_max_chars, поэтому часть локального контекста была разбита на дополнительные AI-батчи.</i>"
        )
    if sync_limit_reached:
        warnings.append(
            "<i>Предупреждение: по этому чату был достигнут sync_limit, поэтому Telegram-история для выбранного периода могла быть загружена не полностью.</i>"
        )
    if warnings:
        body = f"{body}\n\n" + "\n\n".join(warnings)
    if separator_text:
        escaped_separator = html.escape(separator_text, quote=False)
        return f"{body}\n\n{escaped_separator}\n{escaped_separator}"
    return body


def build_channel_digest_skip_message(
    channel_name: str,
    *,
    since: str,
    until: str,
    message_count: int,
    min_messages_for_ai: int,
    sync_limit_reached: bool = False,
    separator_text: str = "",
) -> str:
    return build_channel_digest_message(
        channel_name,
        since=since,
        until=until,
        message_count=message_count,
        summary=(
            f"Сообщений меньше порога для AI-обработки ({min_messages_for_ai}). "
            "Сообщения загружены, но digest отправлен без анализа."
        ),
        sync_limit_reached=sync_limit_reached,
        separator_text=separator_text,
    )


def build_digest_error_message(*, since: str, until: str, errors: list[str], separator_text: str = "") -> str:
    header = f"Digest completed with errors\nПериод UTC: {since} .. {until}"
    body = "\n\n".join([header, *errors])
    if separator_text:
        escaped_separator = html.escape(separator_text, quote=False)
        return f"{body}\n\n{escaped_separator}\n{escaped_separator}"
    return body


def send_digest_message(token: str, chat_id: str | int, text: str) -> None:
    bridge.send_text_chunks(token, chat_id, text, parse_mode="HTML")


def summarize_channel_batches(
    log_conn: Any,
    *,
    api_key: str,
    config: DigestConfig,
    channel: str,
    channel_name: str,
    since: str,
    until: str,
    total_message_count: int,
    messages: Any,
) -> tuple[int, str, bool]:
    batch_summaries: list[str] = []
    unique_message_ids: set[int] = set()
    previous_batch_summary = ""
    batch_index = 0
    batch_size = max(1, config.messages_per_ai_pass)
    all_messages = list(messages)
    batch_source = all_messages
    char_limit_reached = False
    if total_message_count <= batch_size:
        single_pass_input = render_message_block(
            all_messages,
            max_chars=config.message_block_max_chars,
            message_text_max_chars=config.message_text_max_chars,
            message_ocr_max_chars=config.message_ocr_max_chars,
        )
        if single_pass_input.message_count == total_message_count:
            unique_message_ids.update(int(item["message_id"]) for item in all_messages if item.get("message_id") is not None)
            prompt = build_single_digest_prompt(
                config.shared_prompt_prefix,
                config.single_prompt_template,
                channel_name or channel,
                since,
                until,
                single_pass_input.message_count,
                single_pass_input.message_block,
            )
            cache_info = build_prompt_cache_info(
                stage="single",
                model=config.model,
                cache_channel=channel,
                display_channel=channel_name or channel,
                since=since,
                until=until,
                system_instructions=config.system_instructions,
                shared_prompt_prefix=config.shared_prompt_prefix,
                prompt=prompt,
            )
            try:
                result = run_openai_digest(
                    api_key,
                    config.model,
                    config.system_instructions,
                    prompt,
                    prompt_cache_key=cache_info.cache_key,
                )
            except Exception as exc:
                log_openai_usage(
                    log_conn,
                    stage="single",
                    channel=channel,
                    since=since,
                    until=until,
                    model=config.model,
                    request_index=1,
                    message_count=single_pass_input.message_count,
                    status="error",
                    cache_info=cache_info,
                    prompt_text=prompt,
                    error=str(exc) or exc.__class__.__name__,
                )
                raise
            log_openai_usage(
                log_conn,
                stage="single",
                channel=channel,
                since=since,
                until=until,
                model=config.model,
                request_index=1,
                message_count=single_pass_input.message_count,
                status="ok",
                cache_info=cache_info,
                prompt_text=prompt,
                usage=result.usage,
                response_id=result.response_id,
            )
            return len(unique_message_ids), result.text, False
        char_limit_reached = True
    for batch, batch_input in iter_rendered_message_batches(
        batch_source,
        batch_size=batch_size,
        max_chars=config.message_block_max_chars,
        message_text_max_chars=config.message_text_max_chars,
        message_ocr_max_chars=config.message_ocr_max_chars,
    ):
        batch_index += 1
        char_limit_reached = char_limit_reached or batch_input.hit_char_limit
        unique_message_ids.update(int(item["message_id"]) for item in batch if item.get("message_id") is not None)
        prompt = build_batch_digest_prompt(
            config.shared_prompt_prefix,
            config.batch_prompt_template,
            channel_name or channel,
            since,
            until,
            batch_index,
            batch_input.message_count,
            batch_input.message_block,
            previous_batch_summary,
        )
        cache_info = build_prompt_cache_info(
            stage="batch",
            model=config.model,
            cache_channel=channel,
            display_channel=channel_name or channel,
            since=since,
            until=until,
            system_instructions=config.system_instructions,
            shared_prompt_prefix=config.shared_prompt_prefix,
            prompt=prompt,
        )
        try:
            batch_result = run_openai_digest(
                api_key,
                config.model,
                config.system_instructions,
                prompt,
                prompt_cache_key=cache_info.cache_key,
            )
        except Exception as exc:
            log_openai_usage(
                log_conn,
                stage="batch",
                channel=channel,
                since=since,
                until=until,
                model=config.model,
                request_index=batch_index,
                message_count=batch_input.message_count,
                status="error",
                cache_info=cache_info,
                prompt_text=prompt,
                error=str(exc) or exc.__class__.__name__,
            )
            raise
        log_openai_usage(
            log_conn,
            stage="batch",
            channel=channel,
            since=since,
            until=until,
            model=config.model,
            request_index=batch_index,
            message_count=batch_input.message_count,
            status="ok",
            cache_info=cache_info,
            prompt_text=prompt,
            usage=batch_result.usage,
            response_id=batch_result.response_id,
        )
        batch_summaries.append(f"Батч {batch_index}\n{batch_result.text}")
        previous_batch_summary = batch_result.text[:2000]

    if not batch_summaries:
        return 0, "Новых сообщений в выбранном периоде нет.", False
    if len(batch_summaries) == 1:
        return (
            len(unique_message_ids),
            batch_summaries[0].split("\n", 1)[1] if "\n" in batch_summaries[0] else batch_summaries[0],
            char_limit_reached,
        )

    final_prompt = build_final_digest_prompt(
        config.shared_prompt_prefix,
        config.final_prompt_template,
        channel_name or channel,
        since,
        until,
        len(unique_message_ids),
        len(batch_summaries),
        "\n\n".join(batch_summaries),
    )
    final_cache_info = build_prompt_cache_info(
        stage="final",
        model=config.model,
        cache_channel=channel,
        display_channel=channel_name or channel,
        since=since,
        until=until,
        system_instructions=config.system_instructions,
        shared_prompt_prefix=config.shared_prompt_prefix,
        prompt=final_prompt,
    )
    try:
        final_result = run_openai_digest(
            api_key,
            config.model,
            config.system_instructions,
            final_prompt,
            prompt_cache_key=final_cache_info.cache_key,
        )
    except Exception as exc:
        log_openai_usage(
            log_conn,
            stage="final",
            channel=channel,
            since=since,
            until=until,
            model=config.model,
            request_index=len(batch_summaries) + 1,
            message_count=len(unique_message_ids),
            status="error",
            cache_info=final_cache_info,
            prompt_text=final_prompt,
            error=str(exc) or exc.__class__.__name__,
        )
        raise
    log_openai_usage(
        log_conn,
        stage="final",
        channel=channel,
        since=since,
        until=until,
        model=config.model,
        request_index=len(batch_summaries) + 1,
        message_count=len(unique_message_ids),
        status="ok",
        cache_info=final_cache_info,
        prompt_text=final_prompt,
        usage=final_result.usage,
        response_id=final_result.response_id,
    )
    return len(unique_message_ids), final_result.text, char_limit_reached


def cmd_run(args: argparse.Namespace) -> int:
    run_id = getattr(args, "audit_run_id", None) or build_digest_run_id()
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
    write_digest_last_attempt(audit_payload)

    def persist_attempt(**changes: Any) -> None:
        nonlocal audit_payload
        audit_payload = {
            **audit_payload,
            **changes,
            "updated_at": audit_timestamp(),
        }
        write_digest_last_attempt(audit_payload)

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
    conn = None
    log_conn = None
    token = None
    chat_id = None
    try:
        import asyncio

        runtime = history_client.resolve_runtime()
        config = history_client.load_runtime_config()
        digest_config = resolve_digest_config(config)
        if digest_config.sync_mode not in {"backfill", "tail", "update"}:
            raise SystemExit("digest.sync_mode must be one of 'backfill', 'tail', or 'update'.")
        default_since, default_until = resolve_digest_window(digest_config)
        since = args.since or default_since
        until = args.until or default_until
        since, until = normalize_digest_window_values(since, until)
        auth_mode = args.auth_mode or runtime.default_auth_mode
        limits = resolve_digest_limits(config, since, until)
        channels = history_client.resolve_channels_argument(runtime, args.channel)
        sync_timeout_seconds = digest_config.sync_total_timeout_seconds
        persist_attempt(
            phase="sync_pending",
            since=since,
            until=until,
            auth_mode=auth_mode,
            sync_mode=digest_config.sync_mode,
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
        log_conn = history_client.connect_db(runtime)
        history_client.init_db(log_conn)

        sync_result_by_channel = {item.get("channel"): item for item in sync_results}
        api_key: str | None = None
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
            message_rows = iter_channel_messages(
                conn,
                channel=channel,
                since=since,
                until=until,
                max_messages=None,
            )
            preview_row = next(
                iter_channel_messages(
                    conn,
                    channel=channel,
                    since=since,
                    until=until,
                    max_messages=1,
                ),
                None,
            )
            total_message_count = count_channel_messages(
                conn,
                channel=channel,
                since=since,
                until=until,
            )
            preview_channel_name = ""
            if isinstance(preview_row, dict):
                preview_channel_name = str(preview_row.get("title") or preview_row.get("username") or "").strip()
            channel_name = resolve_display_channel_name(conn, channel, preview_channel_name)
            sync_limit_reached = bool(sync_result.get("sync_limit_reached"))
            if not total_message_count:
                send_digest_message(
                    token,
                    chat_id,
                    build_channel_digest_message(
                        channel_name,
                        since=since,
                        until=until,
                        message_count=0,
                        summary="Новых сообщений в выбранном периоде нет.",
                        sync_limit_reached=sync_limit_reached,
                        separator_text=digest_config.separator_text,
                    ),
                )
                sent_channel_messages += 1
                persist_attempt(current_channel=channel, sent_channel_messages=sent_channel_messages)
                continue
            if total_message_count < digest_config.min_messages_for_ai:
                send_digest_message(
                    token,
                    chat_id,
                    build_channel_digest_skip_message(
                        channel_name,
                        since=since,
                        until=until,
                        message_count=total_message_count,
                        min_messages_for_ai=digest_config.min_messages_for_ai,
                        sync_limit_reached=sync_limit_reached,
                        separator_text=digest_config.separator_text,
                    ),
                )
                sent_channel_messages += 1
                persist_attempt(current_channel=channel, sent_channel_messages=sent_channel_messages)
                continue
            if api_key is None:
                api_key = require_openai_api_key(digest_config)
            try:
                message_count, summary, char_limit_reached = summarize_channel_batches(
                    log_conn,
                    api_key=api_key,
                    config=DigestConfig(
                        time=digest_config.time,
                        since=digest_config.since,
                        until=digest_config.until,
                        model=digest_config.model,
                        sync_mode=digest_config.sync_mode,
                        sync_total_timeout_seconds=digest_config.sync_total_timeout_seconds,
                        messages_per_ai_pass=limits.messages_per_ai_pass,
                        message_text_max_chars=limits.message_text_max_chars,
                        message_ocr_max_chars=limits.message_ocr_max_chars,
                        message_block_max_chars=limits.message_block_max_chars,
                        min_messages_for_ai=digest_config.min_messages_for_ai,
                        separator_text=digest_config.separator_text,
                        mark_read=digest_config.mark_read,
                        use_ocr=digest_config.use_ocr,
                        system_instructions=digest_config.system_instructions,
                        shared_prompt_prefix=digest_config.shared_prompt_prefix,
                        single_prompt_template=digest_config.single_prompt_template,
                        batch_prompt_template=digest_config.batch_prompt_template,
                        final_prompt_template=digest_config.final_prompt_template,
                        openai_api_key=digest_config.openai_api_key,
                    ),
                    channel=channel,
                    channel_name=channel_name,
                    since=since,
                    until=until,
                    total_message_count=total_message_count,
                    messages=message_rows,
                )
            except Exception as exc:
                errors.append(f"{channel_name}: analysis failed: {str(exc) or exc.__class__.__name__}")
                persist_attempt(current_channel=channel, errors=errors)
                continue
            send_digest_message(
                token,
                chat_id,
                build_channel_digest_message(
                    channel_name,
                    since=since,
                    until=until,
                    message_count=message_count,
                    summary=summary,
                    char_limit_reached=char_limit_reached,
                    sync_limit_reached=sync_limit_reached,
                    separator_text=digest_config.separator_text,
                ),
            )
            sent_channel_messages += 1
            persist_attempt(current_channel=channel, sent_channel_messages=sent_channel_messages)

        if errors:
            persist_attempt(phase="sending_error_summary", errors=errors, sent_channel_messages=sent_channel_messages)
            send_digest_message(
                token,
                chat_id,
                build_digest_error_message(since=since, until=until, errors=errors, separator_text=digest_config.separator_text),
            )
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
            "auth_mode": auth_mode,
            "sync_results": sync_results,
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        persist_attempt(
            current_channel=None,
            **payload,
            phase="completed",
            finished_at=audit_timestamp(),
        )
        return 0
    except BaseException as exc:
        persist_attempt(
            finished_at=audit_timestamp(),
            status="failed",
            since=since,
            until=until,
            auth_mode=auth_mode,
            sync_results=sync_results,
            errors=errors,
            error=str(exc) or exc.__class__.__name__,
        )
        raise
    finally:
        if conn is not None:
            conn.close()
        if log_conn is not None:
            log_conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Morning Telegram digest runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run sync + AI digest + Telegram delivery using config defaults.")
    run.add_argument("--channel", help="Optional channel or comma-separated channel list override.")
    run.add_argument("--since", help="Optional override for digest since window.")
    run.add_argument("--until", help="Optional override for digest until window.")
    run.add_argument("--auth-mode", choices=["auto", "bot", "user"], help="Optional auth mode override.")
    run.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "command", None) == "run":
        args.audit_run_id = build_digest_run_id()
        args.audit_started_at = audit_timestamp()
    with configure_digest_cli_logging():
        return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
