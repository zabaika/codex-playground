"""Shared OpenAI usage and prompt-cache primitives."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class OpenAIUsage:
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int | None
    output_tokens: int
    total_tokens: int
    latency_ms: int
    reasoning_tokens: int | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None
    output_chars: int | None = None


@dataclass
class PromptCacheInfo:
    cache_key: str
    cache_retention: str
    prompt_version_hash: str | None
    system_chars: int
    prompt_chars: int
    shared_prefix_chars: int
    shared_prefix_hash: str
    prompt_hash: str


def extract_usage(
    response: dict[str, Any],
    latency_ms: int | None = None,
    *,
    output_chars: int | None = None,
) -> OpenAIUsage:
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    effective_latency_ms = response.get("_latency_ms") if latency_ms is None else latency_ms
    incomplete_details = response.get("incomplete_details") or {}
    raw_reasoning_tokens = output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None
    raw_cache_write_tokens = input_details.get("cache_write_tokens") if isinstance(input_details, dict) else None
    raw_incomplete_reason = incomplete_details.get("reason") if isinstance(incomplete_details, dict) else None
    raw_response_status = response.get("status")
    return OpenAIUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        cached_input_tokens=int(input_details.get("cached_tokens") or 0),
        cache_write_tokens=int(raw_cache_write_tokens) if raw_cache_write_tokens is not None else None,
        output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        latency_ms=max(0, int(effective_latency_ms or 0)),
        reasoning_tokens=int(raw_reasoning_tokens) if raw_reasoning_tokens is not None else None,
        response_status=(str(raw_response_status).strip() or None) if raw_response_status is not None else None,
        incomplete_reason=(str(raw_incomplete_reason).strip() or None) if raw_incomplete_reason is not None else None,
        output_chars=max(0, int(output_chars)) if output_chars is not None else None,
    )


def common_prefix_length(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    idx = 0
    while idx < limit and left[idx] == right[idx]:
        idx += 1
    return idx


def short_hash(text: str, *, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def hash_cache_key(prefix: str, *parts: str, digest_length: int = 24) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:digest_length]
    return f"{prefix}:{digest}"


def build_prompt_cache_info(
    *,
    cache_key: str,
    system_instructions: str,
    prompt_text: str,
    shared_prefix: str,
    prompt_version_text: str | None = None,
    cache_retention: str = "api_default",
) -> PromptCacheInfo:
    return PromptCacheInfo(
        cache_key=cache_key,
        cache_retention=cache_retention,
        prompt_version_hash=short_hash(prompt_version_text) if prompt_version_text else None,
        system_chars=len(system_instructions),
        prompt_chars=len(prompt_text),
        shared_prefix_chars=len(shared_prefix),
        shared_prefix_hash=short_hash(shared_prefix),
        prompt_hash=short_hash(prompt_text),
    )


def log_openai_usage(
    conn: sqlite3.Connection,
    *,
    feature: str,
    created_at: str,
    stage: str,
    channel: str,
    since: str | None,
    until: str | None,
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
    previous = conn.execute(
        """
        SELECT response_id, prompt_hash, prompt_text
        FROM ai_usage_log
        WHERE feature = ?
          AND prompt_cache_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (feature, cache_info.cache_key),
    ).fetchone()
    previous_response_id = previous["response_id"] if previous else None
    previous_prompt_hash = previous["prompt_hash"] if previous else None
    prefix_match_chars_with_previous = 0
    if previous and previous["prompt_text"]:
        prefix_match_chars_with_previous = common_prefix_length(previous["prompt_text"], prompt_text)
    conn.execute(
        """
        INSERT INTO ai_usage_log (
            created_at, feature, stage, channel, since, until, model, response_id,
            prompt_cache_key, prompt_cache_retention, prompt_version_hash, request_index, message_count,
            system_chars, prompt_chars, shared_prefix_chars, shared_prefix_hash,
            prompt_hash, previous_prompt_hash, previous_response_id, prefix_match_chars_with_previous,
            prompt_text, input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, reasoning_tokens,
            output_chars, total_tokens, latency_ms, response_status, incomplete_reason, status, error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            feature,
            stage,
            channel,
            since,
            until,
            model,
            response_id,
            cache_info.cache_key,
            cache_info.cache_retention,
            cache_info.prompt_version_hash,
            request_index,
            message_count,
            cache_info.system_chars,
            cache_info.prompt_chars,
            cache_info.shared_prefix_chars,
            cache_info.shared_prefix_hash,
            cache_info.prompt_hash,
            previous_prompt_hash,
            previous_response_id,
            prefix_match_chars_with_previous,
            prompt_text if store_prompt_text else None,
            usage.input_tokens if usage else None,
            usage.cached_input_tokens if usage else None,
            usage.cache_write_tokens if usage else None,
            usage.output_tokens if usage else None,
            usage.reasoning_tokens if usage else None,
            usage.output_chars if usage else None,
            usage.total_tokens if usage else None,
            usage.latency_ms if usage else None,
            usage.response_status if usage else None,
            usage.incomplete_reason if usage else None,
            status,
            error or None,
        ),
    )
    conn.commit()
