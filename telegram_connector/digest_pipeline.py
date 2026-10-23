"""Bounded digest input packing, provider calls, telemetry and synthesis."""
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_shared.formatting import build_message_link
from telegram_shared.openai_api import OpenAIRequestError, post_responses
from telegram_shared.openai_usage import OpenAIUsage, PromptCacheInfo
from telegram_shared.openai_usage import build_prompt_cache_info as shared_build_prompt_cache_info
from telegram_shared.openai_usage import common_prefix_length as shared_common_prefix_length
from telegram_shared.openai_usage import extract_usage as shared_extract_usage
from telegram_shared.openai_usage import hash_cache_key as shared_hash_cache_key
from telegram_shared.openai_usage import log_openai_usage as shared_log_openai_usage
if __package__:
    from . import digest_rendering, digest_settings, history_messages, history_store
else:
    import digest_rendering
    import digest_settings
    import history_messages
    import history_store


OUTPUT_TOKEN_LIMIT_EMPTY_RESPONSE_TEXT = (
    "ИИ не сформировал видимый текст до достижения лимита выходных токенов."
)


@dataclass
class ChannelDigestInput:
    channel_name: str
    message_count: int
    message_block: str
    hit_char_limit: bool


@dataclass
class OpenAIResult:
    response_id: str | None
    text: str
    usage: OpenAIUsage


OpenAIDigestRequestError = OpenAIRequestError


def batch_overlap_size(batch_size: int) -> int:
    if batch_size <= 3:
        return 1
    return min(10, max(2, batch_size // 10))


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
        created_at=history_store.now_utc(),
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
        error=history_messages.optional_text(error),
        store_prompt_text=store_prompt_text,
    )


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
    max_output_tokens: int = digest_settings.DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS,
    timeout_seconds: int = digest_settings.DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS,
    retry_attempts: int = digest_settings.DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS,
    retry_backoff_seconds: float = digest_settings.DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS,
    urlopen_func: Callable[..., Any] | None = None,
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
        response_id=history_messages.optional_text(response.get("id")),
        text=text,
        usage=usage,
    )


def has_reached_output_token_limit(usage: OpenAIUsage) -> bool:
    return (
        usage.response_status == "incomplete" and usage.incomplete_reason == "max_output_tokens"
    )


def request_digest_with_usage(
    log_conn: Any,
    *,
    api_key: str,
    config: digest_settings.DigestConfig,
    stage: str,
    channel: str,
    since: str,
    until: str,
    request_index: int,
    message_count: int,
    cache_info: PromptCacheInfo,
    prompt: str,
) -> OpenAIResult:
    usage_fields = {
        "stage": stage,
        "channel": channel,
        "since": since,
        "until": until,
        "model": config.model,
        "request_index": request_index,
        "message_count": message_count,
        "cache_info": cache_info,
        "prompt_text": prompt,
        "store_prompt_text": config.store_prompt_text,
    }
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
            log_conn, **usage_fields, status="error", error=openai_usage_error_message(exc),
        )
        raise
    log_openai_usage(
        log_conn, **usage_fields, status="ok", usage=result.usage, response_id=result.response_id,
    )
    return result


def summarize_channel_batches(
    log_conn: Any,
    *,
    api_key: str,
    config: digest_settings.DigestConfig,
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
            result = request_digest_with_usage(
                log_conn,
                api_key=api_key,
                config=config,
                stage="single",
                channel=channel,
                since=since,
                until=until,
                request_index=1,
                message_count=single_pass_input.message_count,
                cache_info=cache_info,
                prompt=prompt,
            )
            output_token_limit_reached = has_reached_output_token_limit(result.usage)
            summary_text = digest_rendering.repair_popular_links_in_summary(result.text, all_messages)
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
        batch_result = request_digest_with_usage(
            log_conn,
            api_key=api_key,
            config=config,
            stage="batch",
            channel=channel,
            since=since,
            until=until,
            request_index=batch_index,
            message_count=batch_input.message_count,
            cache_info=cache_info,
            prompt=prompt,
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
            digest_rendering.repair_popular_links_in_summary(summary_text, all_messages),
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
    final_result = request_digest_with_usage(
        log_conn,
        api_key=api_key,
        config=config,
        stage="final",
        channel=channel,
        since=since,
        until=until,
        request_index=len(batch_summaries) + 1,
        message_count=len(unique_message_ids),
        cache_info=final_cache_info,
        prompt=final_prompt,
    )
    final_output_token_limit_reached = has_reached_output_token_limit(final_result.usage)
    summary_text = digest_rendering.repair_popular_links_in_summary(final_result.text, all_messages)
    if not summary_text and final_output_token_limit_reached:
        summary_text = OUTPUT_TOKEN_LIMIT_EMPTY_RESPONSE_TEXT
    return (
        len(unique_message_ids),
        summary_text,
        char_limit_reached,
        output_token_limit_reached or final_output_token_limit_reached,
    )
