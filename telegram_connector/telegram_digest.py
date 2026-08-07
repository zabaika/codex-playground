#!/usr/bin/env python3
import argparse
import contextlib
import html
import json
import os
import random
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Callable
from urllib import request

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process
import telegram_bridge as bridge
import telegram_history_client as history_client
from telegram_shared.openai_usage import OpenAIUsage
from telegram_shared.openai_usage import PromptCacheInfo
from telegram_shared.openai_usage import build_prompt_cache_info as shared_build_prompt_cache_info
from telegram_shared.openai_usage import common_prefix_length as shared_common_prefix_length
from telegram_shared.openai_usage import extract_usage as shared_extract_usage
from telegram_shared.openai_usage import hash_cache_key as shared_hash_cache_key
from telegram_shared.openai_usage import log_openai_usage as shared_log_openai_usage
from telegram_shared.openai_api import OpenAIRequestError
from telegram_shared.openai_api import post_responses
from telegram_shared.bot_api import is_retryable_bot_api_error as shared_is_retryable_bot_api_error


APP_DIR = MODULE_DIR
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR
LAUNCHD_LOG_DIR = PROJECT_ROOT / "data" / "launchd"
DIGEST_LAST_ATTEMPT_LOG = LAUNCHD_LOG_DIR / "digest.last_attempt.json"

DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS = 1200
DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS = 120
DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS = 3
DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS = 1.0
VALID_OPENAI_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
VALID_OPENAI_REASONING_SUMMARIES = frozenset({"auto", "concise", "detailed", "none"})
OPENAI_DIGEST_RETRY_ATTEMPTS = DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS
DEFAULT_PROCESS_CONFIG = common_process.load_process_config()
DEFAULT_DIGEST_SYNC_TOTAL_TIMEOUT_SECONDS = 1800
PROMPT_CACHE_BREAKPOINT_PLACEHOLDER = "{cache_breakpoint_marker}"
DIGEST_PROMPT_REQUIRED_KEYS = (
    "system_instructions",
    "shared_prompt_prefix",
    "cache_breakpoint_marker",
    "single_digest_template",
    "batch_digest_template",
    "final_digest_template",
)
MAIN_TOPICS_DAY_HEADING = "Главные темы дня"
MOST_POPULAR_HEADING = "Наиболее популярное"
OPEN_QUESTIONS_HEADING = "Незакрытые вопросы/продолжения"
QUESTION_ANSWER_LINKS_HEADING = "Связки вопрос-ответ/развитие темы"
OUTPUT_TOKEN_LIMIT_EMPTY_RESPONSE_TEXT = (
    "ИИ не сформировал видимый текст до достижения лимита выходных токенов."
)
POPULAR_LINK_LINE_RE = re.compile(
    r"^(?P<prefix>(?:\d+\.\s+|[-•]\s+)?)"
    r"(?P<link><?https?://\S+>?)"
    r"(?P<sep>\s*-\s+)"
    r"(?P<title>\S.*)$",
    re.IGNORECASE,
)


@dataclass
class DigestConfig:
    time: str
    since: str
    until: str
    model: str
    sync_mode: str
    run_total_timeout_seconds: int
    termination_grace_seconds: int
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
    cache_breakpoint_marker: str
    single_prompt_template: str
    batch_prompt_template: str
    final_prompt_template: str
    openai_api_key: str
    openai_reasoning_effort: str
    openai_reasoning_summary: str
    openai_max_output_tokens: int = DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS
    openai_timeout_seconds: int = DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS
    openai_retry_attempts: int = DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS
    openai_retry_backoff_seconds: float = DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS
    store_prompt_text: bool = False


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


OpenAIDigestRequestError = OpenAIRequestError


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
    prompts = {
        key: history_client.get_config_value(data, "digest_prompts", key)
        for key in DIGEST_PROMPT_REQUIRED_KEYS
    }
    for template_key in ("single_digest_template", "batch_digest_template", "final_digest_template"):
        if prompts[template_key].count(PROMPT_CACHE_BREAKPOINT_PLACEHOLDER) != 1:
            raise SystemExit(
                f"{template_key} must contain {PROMPT_CACHE_BREAKPOINT_PLACEHOLDER} exactly once: {prompt_file}"
            )
    return prompts


def resolve_digest_config(config: dict[str, Any]) -> DigestConfig:
    prompts = load_digest_prompts(config)
    shared_ai_section = get_nested_section(config, "digest_ai")
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
    raw_run_total_timeout_seconds = (
        history_client.get_config_value(config, "digest", "run_total_timeout_seconds")
        or str(DEFAULT_PROCESS_CONFIG.default_run_total_timeout_seconds)
    )
    raw_termination_grace_seconds = (
        history_client.get_config_value(config, "digest", "termination_grace_seconds")
        or str(DEFAULT_PROCESS_CONFIG.default_termination_grace_seconds)
    )
    raw_openai_max_output_tokens = str(
        shared_ai_section.get("max_output_tokens", DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS)
    ).strip()
    openai_reasoning_effort = str(shared_ai_section.get("reasoning_effort", "")).strip().lower()
    openai_reasoning_summary = str(shared_ai_section.get("reasoning_summary", "")).strip().lower()
    raw_openai_timeout_seconds = str(
        shared_ai_section.get("openai_timeout_seconds", DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS)
    ).strip()
    raw_openai_retry_attempts = str(
        shared_ai_section.get("openai_retry_attempts", DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS)
    ).strip()
    raw_openai_retry_backoff_seconds = str(
        shared_ai_section.get("openai_retry_backoff_seconds", DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS)
    ).strip()
    try:
        min_messages_for_ai = max(0, int(raw_min_messages_for_ai))
        run_total_timeout_seconds = max(1, int(raw_run_total_timeout_seconds))
        termination_grace_seconds = max(1, int(raw_termination_grace_seconds))
        sync_total_timeout_seconds = max(1, int(raw_sync_total_timeout_seconds))
        openai_max_output_tokens = max(1, int(raw_openai_max_output_tokens))
        openai_timeout_seconds = max(1, int(raw_openai_timeout_seconds))
        openai_retry_attempts = min(5, max(1, int(raw_openai_retry_attempts)))
        openai_retry_backoff_seconds = min(60.0, max(0.0, float(raw_openai_retry_backoff_seconds)))
    except ValueError as exc:
        raise SystemExit(
            "Invalid digest.min_messages_for_ai, digest.run_total_timeout_seconds, digest.termination_grace_seconds, "
            "digest.sync_total_timeout_seconds, digest_ai.max_output_tokens, digest_ai.openai_timeout_seconds, "
            "digest_ai.openai_retry_attempts, or digest_ai.openai_retry_backoff_seconds in runtime config."
        ) from exc
    if not openai_reasoning_effort:
        raise SystemExit("Missing digest_ai.reasoning_effort in runtime config.")
    if openai_reasoning_effort not in VALID_OPENAI_REASONING_EFFORTS:
        allowed_efforts = ", ".join(sorted(VALID_OPENAI_REASONING_EFFORTS))
        raise SystemExit(
            f"Invalid digest_ai.reasoning_effort in runtime config. Expected one of: {allowed_efforts}."
        )
    if not openai_reasoning_summary:
        raise SystemExit("Missing digest_ai.reasoning_summary in runtime config.")
    if openai_reasoning_summary not in VALID_OPENAI_REASONING_SUMMARIES:
        allowed_summaries = ", ".join(sorted(VALID_OPENAI_REASONING_SUMMARIES))
        raise SystemExit(
            f"Invalid digest_ai.reasoning_summary in runtime config. Expected one of: {allowed_summaries}."
        )
    return DigestConfig(
        time=history_client.get_config_value(config, "digest", "time") or "08:00",
        since=history_client.get_config_value(config, "digest", "since") or "yesterday",
        until=history_client.get_config_value(config, "digest", "until") or "yesterday",
        model=model,
        sync_mode=history_client.get_config_value(config, "digest", "sync_mode") or "update",
        run_total_timeout_seconds=run_total_timeout_seconds,
        termination_grace_seconds=termination_grace_seconds,
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
        cache_breakpoint_marker=prompts["cache_breakpoint_marker"],
        single_prompt_template=prompts["single_digest_template"],
        batch_prompt_template=prompts["batch_digest_template"],
        final_prompt_template=prompts["final_digest_template"],
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip() or history_client.get_config_value(config, "secrets", "openai_api_key"),
        openai_reasoning_effort=openai_reasoning_effort,
        openai_reasoning_summary=openai_reasoning_summary,
        openai_max_output_tokens=openai_max_output_tokens,
        openai_timeout_seconds=openai_timeout_seconds,
        openai_retry_attempts=openai_retry_attempts,
        openai_retry_backoff_seconds=openai_retry_backoff_seconds,
        store_prompt_text=parse_bool(str(shared_ai_section.get("store_prompt_text", "")).strip(), default=False),
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
        client = await history_client.open_telethon_client(runtime, auth_mode)
        async with client:
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


def is_telegram_message_link(value: str) -> bool:
    normalized = value.strip().strip("<>")
    return bool(re.match(r"^https?://t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+$", normalized, flags=re.IGNORECASE))


def normalize_similarity_tokens(value: str) -> list[str]:
    lowered = value.lower().replace("ё", "е")
    cleaned = re.sub(r"[^0-9a-zа-я]+", " ", lowered, flags=re.IGNORECASE)
    return [token for token in cleaned.split() if len(token) >= 2]


def build_popular_link_candidates(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for message in messages:
        link = build_message_link(message)
        message_text = " ".join(
            part.strip()
            for part in (
                str(message.get("text") or ""),
                str(message.get("ocr_text") or ""),
            )
            if part and part.strip()
        )
        candidates.append(
            {
                "message": message,
                "link": link,
                "message_id": int(message.get("message_id") or 0),
                "text_lower": message_text.lower(),
                "token_set": set(normalize_similarity_tokens(message_text)),
            }
        )
    return candidates


def score_popular_link_candidate(
    title_tokens: list[str],
    candidate: dict[str, Any],
    *,
    claimed_message_id: int | None,
) -> float:
    if not title_tokens:
        return 0.0
    message_tokens = candidate["token_set"]
    if not message_tokens:
        return 0.0
    overlap = set(title_tokens) & message_tokens
    if not overlap:
        return 0.0
    score = len(overlap) / len(set(title_tokens))
    message_id = candidate["message_id"]
    if claimed_message_id is not None and message_id:
        if message_id == claimed_message_id:
            score += 0.5
        elif message_id % 1000 == claimed_message_id % 1000:
            score += 0.25
        elif abs(message_id - claimed_message_id) <= 10:
            score += 0.1
    if len(overlap) >= 3:
        score += 0.1
    return score


def repair_popular_links_in_summary(summary: str, messages: list[dict[str, Any]]) -> str:
    if not summary.strip() or MOST_POPULAR_HEADING not in summary or not messages:
        return summary

    candidates = build_popular_link_candidates(messages)
    valid_links = {
        candidate["link"]: candidate["message"]
        for candidate in candidates
        if candidate["link"]
    }

    repaired_lines: list[str] = []
    in_popular_section = False
    for raw_line in summary.splitlines():
        stripped = raw_line.strip()
        if re.match(r"^Наиболее популярное\b", stripped, flags=re.IGNORECASE):
            in_popular_section = True
            repaired_lines.append(raw_line)
            continue
        if in_popular_section and re.match(r"^(?:Незакрытые вопросы|Связки вопрос-ответ|Главные темы)", stripped, flags=re.IGNORECASE):
            in_popular_section = False
        if not in_popular_section:
            repaired_lines.append(raw_line)
            continue

        match = POPULAR_LINK_LINE_RE.match(stripped)
        if not match:
            repaired_lines.append(raw_line)
            continue

        raw_link = match.group("link").strip()
        normalized_link = raw_link.strip("<>")
        if normalized_link in valid_links:
            repaired_lines.append(raw_line)
            continue

        best_message: dict[str, Any] | None = None
        if not is_telegram_message_link(normalized_link):
            exact_url_matches = [candidate["message"] for candidate in candidates if normalized_link.lower() in candidate["text_lower"]]
            if exact_url_matches:
                best_message = exact_url_matches[0]

        if best_message is None:
            title = match.group("title").strip()
            title_tokens = normalize_similarity_tokens(title)
            claimed_message_id: int | None = None
            claimed_match = re.search(r"/(\d+)$", normalized_link)
            if claimed_match:
                try:
                    claimed_message_id = int(claimed_match.group(1))
                except ValueError:
                    claimed_message_id = None

            best_score = 0.0
            for candidate in candidates:
                if not candidate["link"]:
                    continue
                score = score_popular_link_candidate(title_tokens, candidate, claimed_message_id=claimed_message_id)
                if score > best_score:
                    best_score = score
                    best_message = candidate["message"]
            if best_score < 0.34:
                best_message = None

        repaired_link = build_message_link(best_message) if best_message is not None else None
        if not repaired_link:
            repaired_lines.append(raw_line)
            continue
        prefix = match.group("prefix") or ""
        repaired_lines.append(f"{prefix}{repaired_link}{match.group('sep')}{match.group('title').strip()}")
    return "\n".join(repaired_lines)


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
    *,
    cache_breakpoint_marker: str,
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
        cache_breakpoint_marker=cache_breakpoint_marker,
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
    *,
    cache_breakpoint_marker: str,
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
        cache_breakpoint_marker=cache_breakpoint_marker,
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
    *,
    cache_breakpoint_marker: str,
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
        cache_breakpoint_marker=cache_breakpoint_marker,
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


def extract_usage(response: dict[str, Any], latency_ms: int, *, output_chars: int | None = None) -> OpenAIUsage:
    return shared_extract_usage(response, latency_ms, output_chars=output_chars)


def build_prompt_cache_key(
    *,
    model: str,
    stage: str,
    system_instructions: str,
    shared_prompt_prefix: str,
    stage_template: str,
) -> str:
    return shared_hash_cache_key(
        "digest",
        "v2",
        model.strip().lower() or "unknown-model",
        stage.strip().lower() or "unknown-stage",
        system_instructions,
        shared_prompt_prefix,
        stage_template,
    )


def explicit_prompt_cache_parts(
    model: str,
    prompt: str,
    cache_breakpoint_marker: str,
) -> tuple[str, str] | None:
    if not model.strip().lower().startswith("gpt-5.6"):
        return None
    static_prefix, marker, dynamic_data = prompt.partition(cache_breakpoint_marker)
    if not marker or not dynamic_data:
        return None
    return f"{static_prefix}{marker}", dynamic_data


def build_prompt_cache_info(
    *,
    stage: str,
    model: str,
    display_channel: str,
    since: str,
    until: str,
    system_instructions: str,
    shared_prompt_prefix: str,
    cache_breakpoint_marker: str,
    stage_template: str,
    prompt: str,
) -> PromptCacheInfo:
    cache_parts = explicit_prompt_cache_parts(model, prompt, cache_breakpoint_marker)
    prefix_text = shared_prompt_prefix.format(
        channel_name=display_channel,
        since=since,
        until=until,
    ).strip()
    return shared_build_prompt_cache_info(
        cache_key=build_prompt_cache_key(
            model=model,
            stage=stage,
            system_instructions=system_instructions,
            shared_prompt_prefix=shared_prompt_prefix,
            stage_template=stage_template,
        ),
        system_instructions=system_instructions,
        prompt_text=prompt,
        shared_prefix=cache_parts[0] if cache_parts else prefix_text,
        prompt_version_text="\n".join((system_instructions, shared_prompt_prefix, stage_template)),
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
    store_prompt_text: bool = False,
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
        store_prompt_text=store_prompt_text,
    )


def is_fatal_openai_error(exc: OpenAIDigestRequestError) -> bool:
    return exc.status_code == 401


def openai_usage_error_message(exc: Exception) -> str:
    if isinstance(exc, OpenAIDigestRequestError):
        return exc.telemetry_message()
    return str(exc) or exc.__class__.__name__


def run_openai_digest(
    api_key: str,
    model: str,
    system_instructions: str,
    prompt: str,
    *,
    prompt_cache_key: str,
    cache_breakpoint_marker: str,
    reasoning_effort: str,
    reasoning_summary: str,
    max_output_tokens: int = DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS,
    timeout_seconds: int = DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS,
    retry_attempts: int = DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS,
    urlopen_func: Callable[..., Any] = request.urlopen,
    sleep_func: Callable[[float], None] = time.sleep,
    random_func: Callable[[], float] = random.random,
) -> OpenAIResult:
    reasoning: dict[str, str] = {"effort": reasoning_effort}
    if reasoning_summary != "none":
        reasoning["summary"] = reasoning_summary
    payload = {
        "model": model,
        "instructions": system_instructions,
        "input": prompt,
        "prompt_cache_key": prompt_cache_key,
        "reasoning": reasoning,
        "max_output_tokens": max_output_tokens,
    }
    cache_parts = explicit_prompt_cache_parts(model, prompt, cache_breakpoint_marker)
    if cache_parts:
        static_prefix, dynamic_data = cache_parts
        payload["input"] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": static_prefix,
                        "prompt_cache_breakpoint": {"mode": "explicit"},
                    },
                    {"type": "input_text", "text": dynamic_data},
                ],
            }
        ]
        payload["prompt_cache_options"] = {"mode": "explicit"}
    openai_response = post_responses(
        payload,
        api_key,
        timeout_seconds=timeout_seconds,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        urlopen_func=urlopen_func,
        sleep_func=sleep_func,
        random_func=random_func,
    )
    response = openai_response.response
    text = extract_response_text(response)
    usage = extract_usage(response, openai_response.latency_ms, output_chars=len(text))
    if not text and not has_reached_output_token_limit(usage):
        raise OpenAIDigestRequestError(
            "OpenAI API returned an empty digest response.",
            error_type="invalid_response",
        )
    return OpenAIResult(
        response_id=history_client.optional_text(response.get("id")),
        text=text,
        usage=usage,
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
        return bool(POPULAR_LINK_LINE_RE.match(value))

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
    output_token_limit_reached: bool = False,
    output_token_limit: int | None = None,
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
    if output_token_limit_reached and output_token_limit is not None:
        warnings.append(f"ответ ИИ достиг лимита выходных токенов ({output_token_limit})")
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


def has_reached_output_token_limit(usage: OpenAIUsage) -> bool:
    return (
        usage.response_status == "incomplete" and usage.incomplete_reason == "max_output_tokens"
    )


def format_digest_delivery_error(channel_name: str, exc: BaseException) -> str:
    return f"{channel_name}: delivery failed: {str(exc) or exc.__class__.__name__}"


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
) -> tuple[int, str, bool, bool]:
    batch_summaries: list[str] = []
    unique_message_ids: set[int] = set()
    previous_batch_summary = ""
    batch_index = 0
    batch_size = max(1, config.messages_per_ai_pass)
    all_messages = list(messages)
    batch_source = all_messages
    char_limit_reached = False
    output_token_limit_reached = False
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
                cache_breakpoint_marker=config.cache_breakpoint_marker,
            )
            cache_info = build_prompt_cache_info(
                stage="single",
                model=config.model,
                display_channel=channel_name or channel,
                since=since,
                until=until,
                system_instructions=config.system_instructions,
                shared_prompt_prefix=config.shared_prompt_prefix,
                cache_breakpoint_marker=config.cache_breakpoint_marker,
                stage_template=config.single_prompt_template,
                prompt=prompt,
            )
            try:
                result = run_openai_digest(
                    api_key,
                    config.model,
                    config.system_instructions,
                    prompt,
                    prompt_cache_key=cache_info.cache_key,
                    cache_breakpoint_marker=config.cache_breakpoint_marker,
                    reasoning_effort=config.openai_reasoning_effort,
                    reasoning_summary=config.openai_reasoning_summary,
                    max_output_tokens=config.openai_max_output_tokens,
                    timeout_seconds=config.openai_timeout_seconds,
                    retry_attempts=config.openai_retry_attempts,
                    retry_backoff_seconds=config.openai_retry_backoff_seconds,
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
                    error=openai_usage_error_message(exc),
                    store_prompt_text=config.store_prompt_text,
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
                store_prompt_text=config.store_prompt_text,
            )
            output_token_limit_reached = has_reached_output_token_limit(result.usage)
            summary_text = repair_popular_links_in_summary(result.text, all_messages)
            if not summary_text and output_token_limit_reached:
                summary_text = OUTPUT_TOKEN_LIMIT_EMPTY_RESPONSE_TEXT
            return (
                len(unique_message_ids),
                summary_text,
                False,
                output_token_limit_reached,
            )
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
            cache_breakpoint_marker=config.cache_breakpoint_marker,
        )
        cache_info = build_prompt_cache_info(
            stage="batch",
            model=config.model,
            display_channel=channel_name or channel,
            since=since,
            until=until,
            system_instructions=config.system_instructions,
            shared_prompt_prefix=config.shared_prompt_prefix,
            cache_breakpoint_marker=config.cache_breakpoint_marker,
            stage_template=config.batch_prompt_template,
            prompt=prompt,
        )
        try:
            batch_result = run_openai_digest(
                api_key,
                config.model,
                config.system_instructions,
                prompt,
                prompt_cache_key=cache_info.cache_key,
                cache_breakpoint_marker=config.cache_breakpoint_marker,
                reasoning_effort=config.openai_reasoning_effort,
                reasoning_summary=config.openai_reasoning_summary,
                max_output_tokens=config.openai_max_output_tokens,
                timeout_seconds=config.openai_timeout_seconds,
                retry_attempts=config.openai_retry_attempts,
                retry_backoff_seconds=config.openai_retry_backoff_seconds,
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
                error=openai_usage_error_message(exc),
                store_prompt_text=config.store_prompt_text,
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
            store_prompt_text=config.store_prompt_text,
        )
        output_token_limit_reached = output_token_limit_reached or has_reached_output_token_limit(batch_result.usage)
        if batch_result.text:
            batch_summaries.append(f"Батч {batch_index}\n{batch_result.text}")
            previous_batch_summary = batch_result.text[:2000]

    if not batch_summaries:
        if output_token_limit_reached:
            return (
                len(unique_message_ids),
                OUTPUT_TOKEN_LIMIT_EMPTY_RESPONSE_TEXT,
                char_limit_reached,
                True,
            )
        return 0, "Новых сообщений в выбранном периоде нет.", False, False
    if len(batch_summaries) == 1:
        summary_text = batch_summaries[0].split("\n", 1)[1] if "\n" in batch_summaries[0] else batch_summaries[0]
        return (
            len(unique_message_ids),
            repair_popular_links_in_summary(summary_text, all_messages),
            char_limit_reached,
            output_token_limit_reached,
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
        cache_breakpoint_marker=config.cache_breakpoint_marker,
    )
    final_cache_info = build_prompt_cache_info(
        stage="final",
        model=config.model,
        display_channel=channel_name or channel,
        since=since,
        until=until,
        system_instructions=config.system_instructions,
        shared_prompt_prefix=config.shared_prompt_prefix,
        cache_breakpoint_marker=config.cache_breakpoint_marker,
        stage_template=config.final_prompt_template,
        prompt=final_prompt,
    )
    try:
        final_result = run_openai_digest(
            api_key,
            config.model,
            config.system_instructions,
            final_prompt,
            prompt_cache_key=final_cache_info.cache_key,
            cache_breakpoint_marker=config.cache_breakpoint_marker,
            reasoning_effort=config.openai_reasoning_effort,
            reasoning_summary=config.openai_reasoning_summary,
            max_output_tokens=config.openai_max_output_tokens,
            timeout_seconds=config.openai_timeout_seconds,
            retry_attempts=config.openai_retry_attempts,
            retry_backoff_seconds=config.openai_retry_backoff_seconds,
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
            error=openai_usage_error_message(exc),
            store_prompt_text=config.store_prompt_text,
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
        store_prompt_text=config.store_prompt_text,
    )
    final_output_token_limit_reached = has_reached_output_token_limit(final_result.usage)
    summary_text = repair_popular_links_in_summary(final_result.text, all_messages)
    if not summary_text and final_output_token_limit_reached:
        summary_text = OUTPUT_TOKEN_LIMIT_EMPTY_RESPONSE_TEXT
    return (
        len(unique_message_ids),
        summary_text,
        char_limit_reached,
        output_token_limit_reached or final_output_token_limit_reached,
    )


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
    analysis_errors: list[dict[str, Any]] = []
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
                errors.append(format_digest_delivery_error(channel_name, exc))
                persist_attempt(current_channel=channel, errors=errors, sent_channel_messages=sent_channel_messages)
                return False
            sent_channel_messages += 1
            persist_attempt(current_channel=channel, sent_channel_messages=sent_channel_messages)
            return True

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
                send_channel_digest(
                    channel_name,
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
                continue
            if total_message_count < digest_config.min_messages_for_ai:
                send_channel_digest(
                    channel_name,
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
                continue
            if api_key is None:
                api_key = require_openai_api_key(digest_config)
            try:
                message_count, summary, char_limit_reached, output_token_limit_reached = summarize_channel_batches(
                    log_conn,
                    api_key=api_key,
                    config=DigestConfig(
                        time=digest_config.time,
                        since=digest_config.since,
                        until=digest_config.until,
                        model=digest_config.model,
                        sync_mode=digest_config.sync_mode,
                        run_total_timeout_seconds=digest_config.run_total_timeout_seconds,
                        termination_grace_seconds=digest_config.termination_grace_seconds,
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
                        cache_breakpoint_marker=digest_config.cache_breakpoint_marker,
                        single_prompt_template=digest_config.single_prompt_template,
                        batch_prompt_template=digest_config.batch_prompt_template,
                        final_prompt_template=digest_config.final_prompt_template,
                        openai_api_key=digest_config.openai_api_key,
                        openai_reasoning_effort=digest_config.openai_reasoning_effort,
                        openai_reasoning_summary=digest_config.openai_reasoning_summary,
                        openai_max_output_tokens=digest_config.openai_max_output_tokens,
                        openai_timeout_seconds=digest_config.openai_timeout_seconds,
                        openai_retry_attempts=digest_config.openai_retry_attempts,
                        openai_retry_backoff_seconds=digest_config.openai_retry_backoff_seconds,
                        store_prompt_text=digest_config.store_prompt_text,
                    ),
                    channel=channel,
                    channel_name=channel_name,
                    since=since,
                    until=until,
                    total_message_count=total_message_count,
                    messages=message_rows,
                )
            except OpenAIDigestRequestError as exc:
                error_details = {
                    "channel": channel,
                    "channel_name": channel_name,
                    "error": exc.diagnostic(),
                }
                analysis_errors.append(error_details)
                errors.append(f"{channel_name}: analysis failed: {exc.operator_summary()}")
                persist_attempt(
                    current_channel=channel,
                    errors=errors,
                    analysis_errors=analysis_errors,
                )
                if is_fatal_openai_error(exc):
                    raise
                continue
            except Exception as exc:
                errors.append(f"{channel_name}: analysis failed: {str(exc) or exc.__class__.__name__}")
                persist_attempt(current_channel=channel, errors=errors)
                continue
            send_channel_digest(
                channel_name,
                build_channel_digest_message(
                    channel_name,
                    since=since,
                    until=until,
                    message_count=message_count,
                    summary=summary,
                    char_limit_reached=char_limit_reached,
                    output_token_limit_reached=output_token_limit_reached,
                    output_token_limit=digest_config.openai_max_output_tokens,
                    sync_limit_reached=sync_limit_reached,
                    separator_text=digest_config.separator_text,
                ),
            )

        if errors:
            persist_attempt(phase="sending_error_summary", errors=errors, sent_channel_messages=sent_channel_messages)
            try:
                send_digest_message(
                    token,
                    chat_id,
                    build_digest_error_message(since=since, until=until, errors=errors, separator_text=digest_config.separator_text),
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
            analysis_errors=analysis_errors,
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
