"""Shared OpenAI usage and prompt-cache primitives."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any


AI_USAGE_STATUSES = frozenset({'pending', 'succeeded', 'failed', 'unknown'})

# One current storage contract for every application using the shared writers/readers.
AI_USAGE_COLUMNS = {
    'id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
    'created_at': 'TEXT NOT NULL',
    'feature': 'TEXT NOT NULL',
    'stage': 'TEXT NOT NULL',
    'channel': 'TEXT',
    'since': 'TEXT',
    'until': 'TEXT',
    'model': 'TEXT NOT NULL',
    'response_id': 'TEXT',
    'prompt_cache_key': 'TEXT',
    'prompt_cache_retention': 'TEXT',
    'prompt_version_hash': 'TEXT',
    'request_index': 'INTEGER',
    'message_count': 'INTEGER',
    'system_chars': 'INTEGER',
    'prompt_chars': 'INTEGER',
    'shared_prefix_chars': 'INTEGER',
    'shared_prefix_hash': 'TEXT',
    'prompt_hash': 'TEXT',
    'previous_prompt_hash': 'TEXT',
    'previous_response_id': 'TEXT',
    'prefix_match_chars_with_previous': 'INTEGER',
    'prompt_text': 'TEXT',
    'input_tokens': 'INTEGER',
    'cached_input_tokens': 'INTEGER',
    'cache_write_tokens': 'INTEGER',
    'output_tokens': 'INTEGER',
    'reasoning_tokens': 'INTEGER',
    'output_chars': 'INTEGER',
    'total_tokens': 'INTEGER',
    'latency_ms': 'INTEGER',
    'response_status': 'TEXT',
    'incomplete_reason': 'TEXT',
    'status': 'TEXT NOT NULL',
    'error': 'TEXT',
    'run_id': 'TEXT',
    'attempt_index': 'INTEGER',
    'job_id': 'TEXT',
    'batch_id': 'TEXT',
    'attempt_id': 'TEXT',
    'generation': 'INTEGER',
    'phase_sequence': 'INTEGER',
    'started_at': 'TEXT',
    'backoff_ms': 'INTEGER',
    'backoff_requested_ms': 'INTEGER',
    'cache_write_30m_tokens': 'INTEGER',
    'cache_write_12h_tokens': 'INTEGER',
    'actual_model': 'TEXT',
    'actual_service_tier': 'TEXT',
}
AI_USAGE_SCHEMA_SQL = 'CREATE TABLE IF NOT EXISTS ai_usage_log (\n' + ',\n'.join(
    f'    {name} {declaration}' for name, declaration in AI_USAGE_COLUMNS.items()
) + '\n);'


def require_ai_usage_schema(conn: sqlite3.Connection) -> None:
    """Reject incomplete schemas without changing them or substituting NULL columns."""
    columns = {str(row[1]) for row in conn.execute('PRAGMA table_info(ai_usage_log)')}
    missing = AI_USAGE_COLUMNS.keys() - columns
    if missing:
        raise RuntimeError('Incompatible ai_usage_log schema: missing columns '
                           + ', '.join(sorted(missing)))


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


def insert_openai_usage(
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
    correlation: tuple[str, str, str] | None = None,
    latency_ms: int | None = None,
) -> int:
    if status not in AI_USAGE_STATUSES:
        raise ValueError('Invalid AI usage status')
    if not conn.in_transaction:
        raise RuntimeError('Usage insert requires an explicit transaction')
    previous = conn.execute(
        """
        SELECT response_id, prompt_hash, prompt_text
        FROM ai_usage_log
        WHERE feature = ?
          AND prompt_cache_key = ?
          AND status != 'pending'
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
    inserted = conn.execute(
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
            usage.latency_ms if usage else latency_ms,
            usage.response_status if usage else None,
            usage.incomplete_reason if usage else None,
            status,
            error or None,
        ),
    )
    if correlation is not None:
        conn.execute("UPDATE ai_usage_log SET job_id=?,batch_id=?,attempt_id=? WHERE id=?", (*correlation, inserted.lastrowid))
    return inserted.lastrowid


def begin_openai_attempt(conn, *, run_id, attempt_id, attempt_index, **fields):
    """Persist any feature's intent inside the caller's transaction.

    The application owns run identity and schema migration. This API requires
    the extended ai_usage_log attempt/native-metric columns.
    """
    if not conn.in_transaction:
        raise RuntimeError('Attempt intent requires an explicit transaction')
    row_id = insert_openai_usage(conn, status='pending', **fields)
    conn.execute("""UPDATE ai_usage_log SET run_id=?,attempt_id=?,attempt_index=?,
        started_at=?,backoff_ms=0,backoff_requested_ms=0 WHERE id=?""",
        (run_id, attempt_id, attempt_index, fields['created_at'], row_id))
    return row_id


def write_openai_backoff(conn, *, run_id, attempt_id, elapsed_ms, requested_ms):
    """Join the existing receipt/snapshot transaction, without its own commit."""
    if not conn.in_transaction:
        raise RuntimeError('Backoff requires an explicit transaction')
    conn.execute("""UPDATE ai_usage_log SET backoff_ms=?,backoff_requested_ms=?
        WHERE run_id=? AND attempt_id=?""",
        (elapsed_ms, requested_ms, run_id, attempt_id))


def settle_openai_attempt(conn, row_id, *, response, status, error, latency_ms,
                          output_chars=None):
    """Missing/invalid provider counters remain unknown, including on success."""
    if not conn.in_transaction:
        raise RuntimeError('Attempt receipt requires an explicit transaction')
    if status not in AI_USAGE_STATUSES - {'pending'}:
        raise ValueError('Invalid terminal AI usage status')
    native = response or {}
    raw = native.get('usage')
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get('input_tokens_details')
    details = details if isinstance(details, dict) else {}
    output_details = raw.get('output_tokens_details')
    output_details = output_details if isinstance(output_details, dict) else {}
    writes = details.get('cache_write_tokens_details')
    writes = writes if isinstance(writes, dict) else {}

    def counter(value):
        return value if type(value) is int and value >= 0 else None

    incoming, outgoing = counter(raw.get('input_tokens')), counter(raw.get('output_tokens'))
    cached = counter(details.get('cached_tokens'))
    if incoming is not None and cached is not None and cached > incoming:
        cached = None
    incomplete = native.get('incomplete_details')
    incomplete = incomplete if isinstance(incomplete, dict) else {}

    def native_text(value):
        return value if isinstance(value, str) and value else None

    changed = conn.execute("""UPDATE ai_usage_log SET status=?,error=?,latency_ms=?,
        response_id=?,input_tokens=?,output_tokens=?,total_tokens=?,cached_input_tokens=?,
        cache_write_tokens=?,reasoning_tokens=?,response_status=?,incomplete_reason=?,
        actual_model=?,actual_service_tier=?,cache_write_30m_tokens=?,cache_write_12h_tokens=?,
        output_chars=?
        WHERE id=? AND status='pending'""",
        (status, error, latency_ms, native_text(native.get('id')), incoming, outgoing,
         incoming + outgoing if incoming is not None and outgoing is not None else None,
         cached, counter(details.get('cache_write_tokens')),
         counter(output_details.get('reasoning_tokens')), native_text(native.get('status')),
         native_text(incomplete.get('reason')), native_text(native.get('model')),
         native_text(native.get('service_tier')), counter(writes.get('ttl_30m_tokens')),
         counter(writes.get('ttl_12h_tokens')), counter(output_chars), row_id)).rowcount
    if changed != 1:
        raise ValueError('Attempt receipt is outside pending scope')
