#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any
from urllib import error, request

import telegram_connector as bridge
import telegram_history_client as history_client


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT") else APP_DIR

DEFAULT_SYSTEM_INSTRUCTIONS = (
    "You are a concise Telegram channel analyst. Return compact, high-signal Russian summaries."
)
DEFAULT_BATCH_DIGEST_TEMPLATE = """Канал: {channel_name}
Сделай краткую промежуточную сводку части сообщений на русском языке.
Используй только факты из входных данных.
Формат ответа:
1. Одна короткая строка с главным выводом.
2. 3-4 коротких пункта с темами и событиями.
3. Блок 'Наиболее популярное' с 1-5 пунктами в формате '<ссылка> - короткий заголовок'.
Учитывай replies и forwards как сигналы популярности и приоритета.
4. Блок 'Незакрытые вопросы/продолжения', если они есть.
Не упоминай слова 'батч' или 'часть сообщений' в самом тексте ответа.
Не используй markdown-таблицы.

Метаданные части сообщений:
- Канал: {channel_name}
- Период UTC: {since} .. {until}
- Порядковый номер части: {batch_index}
- Сообщений в батче: {message_count}

Короткий контекст предыдущего батча:
{previous_batch_summary}

Сырые сообщения:
{message_block}
"""
DEFAULT_FINAL_DIGEST_TEMPLATE = """Канал: {channel_name}
Собери финальный дайджест на русском языке для Telegram.
Используй только факты из промежуточных сводок.
Формат ответа:
1. Одна короткая строка с главным выводом.
2. 3-5 коротких пунктов с основными темами и событиями.
3. Блок 'Наиболее популярное' с 1-10 пунктами в формате '<ссылка> - короткий заголовок'.
Выбирай в него самые заметные сообщения периода с учётом replies и forwards. Если активность низкая, можно меньше 10 пунктов.
4. Блок 'Связки вопрос-ответ/развитие темы' только если он добавляет важный контекст сверх тем и блока популярного.
Если блок нужен, для каждой связки указывай ссылку на самое популярное сообщение этой темы в формате '<ссылка> - короткое пояснение связи'.
Не упоминай слово 'батч' в самом тексте ответа.
Не используй markdown-таблицы.

Метаданные канала:
- Канал: {channel_name}
- Период UTC: {since} .. {until}
- Всего сообщений в анализе: {message_count}
- Число батчей: {batch_count}

Промежуточные сводки по батчам:
{batch_summary_block}
"""


@dataclass
class DigestConfig:
    time: str
    since: str
    until: str
    model: str
    sync_mode: str
    ai_batch_size: int
    use_ocr: bool
    system_instructions: str
    batch_prompt_template: str
    final_prompt_template: str
    openai_api_key: str


@dataclass
class ChannelDigestInput:
    channel_name: str
    message_count: int
    message_block: str


@dataclass
class SyncBatchPlan:
    channel: str
    limit: int


@dataclass
class DigestLimits:
    profile: str
    sync_limit: int
    ai_batch_size: int


@dataclass
class OpenAIUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int


@dataclass
class OpenAIResult:
    text: str
    usage: OpenAIUsage


def parse_bool(value: str, default: bool = False) -> bool:
    raw = value.strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_digest_config(config: dict[str, Any]) -> DigestConfig:
    model = history_client.get_config_value(config, "processing", "model")
    if not model:
        raise SystemExit(
            "Missing processing.model in runtime config. Put the default AI model into [processing].model."
        )
    return DigestConfig(
        time=history_client.get_config_value(config, "digest", "time") or "08:00",
        since=history_client.get_config_value(config, "digest", "since") or "yesterday",
        until=history_client.get_config_value(config, "digest", "until") or "yesterday",
        model=model,
        sync_mode=history_client.get_config_value(config, "digest", "sync_mode") or "update",
        ai_batch_size=0,
        use_ocr=parse_bool(
            history_client.get_config_value(config, "processing", "ocr"),
            default=True,
        ),
        system_instructions=history_client.get_config_value(config, "digest_prompts", "system_instructions") or DEFAULT_SYSTEM_INSTRUCTIONS,
        batch_prompt_template=history_client.get_config_value(config, "digest_prompts", "batch_digest_template")
        or DEFAULT_BATCH_DIGEST_TEMPLATE,
        final_prompt_template=history_client.get_config_value(config, "digest_prompts", "final_digest_template") or DEFAULT_FINAL_DIGEST_TEMPLATE,
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
    section = get_nested_section(config, "digest_limits", profile)
    raw_sync_limit = str(section.get("sync_limit", "")).strip()
    raw_ai_batch_size = str(section.get("ai_batch_size", "")).strip()
    if not raw_sync_limit:
        raise SystemExit(f"Missing digest_limits.{profile}.sync_limit in runtime config.")
    if not raw_ai_batch_size:
        raise SystemExit(f"Missing digest_limits.{profile}.ai_batch_size in runtime config.")
    try:
        sync_limit = int(raw_sync_limit)
        ai_batch_size = int(raw_ai_batch_size)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer in digest_limits.{profile}.sync_limit or ai_batch_size.") from exc
    return DigestLimits(profile=profile, sync_limit=max(1, sync_limit), ai_batch_size=max(1, ai_batch_size))


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
                mark_read=False,
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
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def render_message_block(messages: Any, *, max_chars: int) -> ChannelDigestInput:
    chunks: list[str] = []
    total = 0
    message_count = 0
    channel_name = ""
    for item in messages:
        if not channel_name:
            channel_name = item.get("title") or item.get("username") or ""
        message_count += 1
        parts = [
            f"id={item['message_id']}",
            f"date={item['date_utc']}",
            f"sender={render_sender_label(item)}",
            f"link={build_message_link(item) or '<no link>'}",
            f"forwards={item.get('forwards') if item.get('forwards') is not None else 0}",
            f"replies={item.get('replies') if item.get('replies') is not None else 0}",
            f"text={truncate_text(item['text'] or '<no text>', 400)}",
        ]
        if item.get("ocr_text"):
            parts.append(f"ocr={truncate_text(item['ocr_text'], 220)}")
        block = "\n".join(parts)
        if total and total + len(block) + 2 > max_chars:
            break
        chunks.append(block)
        total += len(block) + 2
    return ChannelDigestInput(
        channel_name=channel_name,
        message_count=message_count,
        message_block="\n\n".join(chunks),
    )


def build_batch_digest_prompt(
    template: str,
    channel_name: str,
    since: str,
    until: str,
    batch_index: int,
    message_count: int,
    message_block: str,
    previous_batch_summary: str,
) -> str:
    return template.format(
        channel_name=channel_name,
        since=since,
        until=until,
        batch_index=batch_index,
        message_count=message_count,
        message_block=message_block,
        previous_batch_summary=previous_batch_summary or "<no previous batch>",
    )


def build_final_digest_prompt(
    template: str,
    channel_name: str,
    since: str,
    until: str,
    message_count: int,
    batch_count: int,
    batch_summary_block: str,
) -> str:
    return template.format(
        channel_name=channel_name,
        since=since,
        until=until,
        message_count=message_count,
        batch_count=batch_count,
        batch_summary_block=batch_summary_block,
    )


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
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    return OpenAIUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        cached_input_tokens=int(input_details.get("cached_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        latency_ms=max(0, int(latency_ms)),
    )


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
    usage: OpenAIUsage | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ai_usage_log (
            created_at, feature, stage, channel, since, until, model, request_index,
            message_count, input_tokens, cached_input_tokens, output_tokens, total_tokens,
            latency_ms, status, error
        )
        VALUES (?, 'digest', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            history_client.now_utc(),
            stage,
            channel,
            since,
            until,
            model,
            request_index,
            message_count,
            usage.input_tokens if usage else None,
            usage.cached_input_tokens if usage else None,
            usage.output_tokens if usage else None,
            usage.total_tokens if usage else None,
            usage.latency_ms if usage else None,
            status,
            history_client.optional_text(error),
        ),
    )
    conn.commit()


def run_openai_digest(api_key: str, model: str, system_instructions: str, prompt: str) -> OpenAIResult:
    payload = {
        "model": model,
        "instructions": system_instructions,
        "input": prompt,
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
    started_at = time.perf_counter()
    try:
        with request.urlopen(req, timeout=120) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"OpenAI API HTTP {exc.code} while creating digest.") from exc
    except error.URLError as exc:
        raise SystemExit("OpenAI API request failed while creating digest.") from exc
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    text = extract_response_text(response)
    if not text:
        raise SystemExit("OpenAI API returned an empty digest response.")
    return OpenAIResult(text=text, usage=extract_usage(response, latency_ms))


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


def build_channel_digest_message(channel_name: str, *, since: str, until: str, message_count: int, summary: str) -> str:
    return "\n\n".join(
        [
            channel_name,
            f"Период UTC: {since} .. {until}",
            f"Сообщений в анализе: {message_count}",
            summary,
        ]
    )


def build_digest_error_message(*, since: str, until: str, errors: list[str]) -> str:
    header = f"Digest completed with errors\nПериод UTC: {since} .. {until}"
    return "\n\n".join([header, *errors])


def summarize_channel_batches(
    log_conn: Any,
    *,
    api_key: str,
    config: DigestConfig,
    channel: str,
    channel_name: str,
    since: str,
    until: str,
    messages: Any,
) -> tuple[int, str]:
    batch_summaries: list[str] = []
    unique_message_ids: set[int] = set()
    previous_batch_summary = ""
    batch_index = 0
    batch_size = config.ai_batch_size
    for batch in iter_message_batches(messages, batch_size):
        batch_index += 1
        batch_input = render_message_block(batch, max_chars=max(6000, min(50000, batch_size * 450)))
        unique_message_ids.update(int(item["message_id"]) for item in batch if item.get("message_id") is not None)
        prompt = build_batch_digest_prompt(
            config.batch_prompt_template,
            channel_name or channel,
            since,
            until,
            batch_index,
            batch_input.message_count,
            batch_input.message_block,
            previous_batch_summary,
        )
        try:
            batch_result = run_openai_digest(api_key, config.model, config.system_instructions, prompt)
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
            usage=batch_result.usage,
        )
        batch_summaries.append(f"Батч {batch_index}\n{batch_result.text}")
        previous_batch_summary = batch_result.text[:2000]

    if not batch_summaries:
        return 0, "Новых сообщений в выбранном периоде нет."
    if len(batch_summaries) == 1:
        return len(unique_message_ids), batch_summaries[0].split("\n", 1)[1] if "\n" in batch_summaries[0] else batch_summaries[0]

    final_prompt = build_final_digest_prompt(
        config.final_prompt_template,
        channel_name or channel,
        since,
        until,
        len(unique_message_ids),
        len(batch_summaries),
        "\n\n".join(batch_summaries),
    )
    try:
        final_result = run_openai_digest(api_key, config.model, config.system_instructions, final_prompt)
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
        usage=final_result.usage,
    )
    return len(unique_message_ids), final_result.text


def cmd_run(args: argparse.Namespace) -> int:
    runtime = history_client.resolve_runtime()
    config = history_client.load_runtime_config()
    digest_config = resolve_digest_config(config)
    api_key = require_openai_api_key(digest_config)
    if digest_config.sync_mode not in {"backfill", "tail", "update"}:
        raise SystemExit("digest.sync_mode must be one of 'backfill', 'tail', or 'update'.")
    default_since, default_until = resolve_digest_window(digest_config)
    since = args.since or default_since
    until = args.until or default_until
    since, until = normalize_digest_window_values(since, until)
    auth_mode = args.auth_mode or runtime.default_auth_mode
    limits = resolve_digest_limits(config, since, until)

    import asyncio

    sync_results = asyncio.run(
        run_sync(
            runtime,
            channel=args.channel,
            since=since,
            until=until,
            total_limit=limits.sync_limit,
            use_ocr=digest_config.use_ocr,
            mode=digest_config.sync_mode,
            auth_mode=auth_mode,
        )
    )
    token = bridge.require_token()
    chat_id = history_client.get_config_value(config, "telegram", "default_chat_id")
    if not chat_id:
        raise SystemExit("Missing telegram.default_chat_id for digest delivery.")
    conn = history_client.connect_db(runtime)
    log_conn = history_client.connect_db(runtime)
    history_client.init_db(log_conn)
    try:
        channels = history_client.resolve_channels_argument(runtime, args.channel)
        sync_result_by_channel = {item.get("channel"): item for item in sync_results}
        sent_channel_messages = 0
        errors: list[str] = []
        for channel in channels:
            sync_result = sync_result_by_channel.get(channel)
            if sync_result is None:
                errors.append(f"{channel}: not processed because the shared digest sync_limit budget was exhausted before this channel.")
                continue
            if sync_result.get("status") == "error":
                errors.append(f"{channel}: sync failed: {sync_result.get('error', 'unknown error')}")
                continue
            message_rows = iter_channel_messages(
                conn,
                channel=channel,
                since=since,
                until=until,
                max_messages=None,
            )
            preview = render_message_block(
                iter_channel_messages(
                    conn,
                    channel=channel,
                    since=since,
                    until=until,
                    max_messages=1,
                ),
                max_chars=1000,
            )
            if not preview.message_count:
                bridge.send_text_chunks(
                    token,
                    chat_id,
                    build_channel_digest_message(
                        channel,
                        since=since,
                        until=until,
                        message_count=0,
                        summary="Новых сообщений в выбранном периоде нет.",
                    ),
                )
                sent_channel_messages += 1
                continue
            channel_name = preview.channel_name or channel
            try:
                message_count, summary = summarize_channel_batches(
                    log_conn,
                    api_key=api_key,
                    config=DigestConfig(
                        time=digest_config.time,
                        since=digest_config.since,
                        until=digest_config.until,
                        model=digest_config.model,
                        sync_mode=digest_config.sync_mode,
                        ai_batch_size=limits.ai_batch_size,
                        use_ocr=digest_config.use_ocr,
                        system_instructions=digest_config.system_instructions,
                        batch_prompt_template=digest_config.batch_prompt_template,
                        final_prompt_template=digest_config.final_prompt_template,
                        openai_api_key=digest_config.openai_api_key,
                    ),
                    channel=channel,
                    channel_name=channel_name,
                    since=since,
                    until=until,
                    messages=message_rows,
                )
            except Exception as exc:
                errors.append(f"{channel_name}: analysis failed: {str(exc) or exc.__class__.__name__}")
                continue
            bridge.send_text_chunks(
                token,
                chat_id,
                build_channel_digest_message(
                    channel_name,
                    since=since,
                    until=until,
                    message_count=message_count,
                    summary=summary,
                ),
            )
            sent_channel_messages += 1
    finally:
        conn.close()
        log_conn.close()

    if errors:
        bridge.send_text_chunks(token, chat_id, build_digest_error_message(since=since, until=until, errors=errors))
    print(
        json.dumps(
            {
                "status": "partial" if errors else "sent",
                "channels": len(channels),
                "sent_channel_messages": sent_channel_messages,
                "since": since,
                "until": until,
                "limit_profile": limits.profile,
                "sync_limit": limits.sync_limit,
                "ai_batch_size": limits.ai_batch_size,
                "sync_mode": digest_config.sync_mode,
                "auth_mode": auth_mode,
                "sync_results": sync_results,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_cron_line(args: argparse.Namespace) -> int:
    config = history_client.load_runtime_config()
    digest_config = resolve_digest_config(config)
    hour, minute = digest_config.time.split(":", 1)
    python_bin = sys.executable
    line = (
        f"{int(minute)} {int(hour)} * * * "
        f"cd {PROJECT_ROOT} && "
        f"TELEGRAM_CONNECTOR_PROJECT_ROOT={PROJECT_ROOT} "
        f"{python_bin} telegram_digest.py run "
        f">> data/launchd/digest.cron.log 2>&1 "
        f"# telegram_connector_daily_digest"
    )
    print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Morning Telegram digest runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run sync + AI digest + Telegram delivery using config defaults.")
    run.add_argument("--channel", help="Optional channel or comma-separated channel list override.")
    run.add_argument("--since", help="Optional override for digest since window.")
    run.add_argument("--until", help="Optional override for digest until window.")
    run.add_argument("--auth-mode", choices=["auto", "bot", "user"], help="Optional auth mode override.")
    run.set_defaults(func=cmd_run)

    cron_line = subparsers.add_parser("cron-line", help="Print the crontab line for the configured digest schedule.")
    cron_line.set_defaults(func=cmd_cron_line)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
