"""Shared bounded stats queries for ai_usage_log."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Hashable

from .openai_usage import require_ai_usage_schema


def fetch_ai_usage_summary(
    db_path: Path,
    *,
    feature: str,
    row_limit: int,
    filter_channel: str | None = None,
    recent_rows_limit: int = 3,
    operation_key: Callable[[dict[str, Any]], Hashable | None] | None = None,
) -> dict[str, Any] | None:
    """The application defines operation identity; None means unavailable.

    The callback receives bounded metadata, never prompt or response bodies.
    Without a callback this reader does not infer logical operations.
    """
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        require_ai_usage_schema(conn)
        attempt_counts_sql = """SUM(CASE WHEN attempt_id IS NOT NULL THEN 1 ELSE 0 END) AS physical_attempts,
            NULL AS observed_logical_operations,
            SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END) AS unknown_requests,
            SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_requests,
            SUM(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL
                THEN 1 ELSE 0 END) AS unknown_usage_requests,"""
        usage_row_count_sql = "SUM(CASE WHEN input_tokens IS NOT NULL THEN 1 ELSE 0 END)"
        cache_write_tokens_sum_sql = (
            f"CASE WHEN ({usage_row_count_sql}) = 0 "
            f"OR COUNT(cache_write_tokens) < ({usage_row_count_sql}) THEN NULL "
            "ELSE SUM(cache_write_tokens) END"
        )
        prompt_versions_sql = (
            "CASE WHEN COUNT(prompt_version_hash) < COUNT(*) THEN NULL "
            "ELSE COUNT(DISTINCT NULLIF(prompt_version_hash, '')) END"
        )
        reasoning_tokens_sql = (
            f"CASE WHEN ({usage_row_count_sql}) = 0 "
            f"OR COUNT(reasoning_tokens) < ({usage_row_count_sql}) THEN NULL "
            "ELSE SUM(reasoning_tokens) END"
        )
        output_chars_sql = (
            f"CASE WHEN ({usage_row_count_sql}) = 0 "
            f"OR COUNT(output_chars) < ({usage_row_count_sql}) THEN NULL "
            "ELSE SUM(output_chars) END"
        )
        incomplete_responses_sql = (
            f"CASE WHEN ({usage_row_count_sql}) = 0 "
            f"OR COUNT(response_status) < ({usage_row_count_sql}) THEN NULL "
            "ELSE SUM(CASE WHEN response_status = 'incomplete' THEN 1 ELSE 0 END) END"
        )
        global_row = conn.execute(
            f"""
            WITH recent AS (
                SELECT *
                FROM ai_usage_log
                WHERE feature = ?
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT
                COUNT(*) AS total_requests,
                {attempt_counts_sql}
                SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS ok_requests,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS error_requests,
                SUM(CASE WHEN stage = 'single' THEN 1 ELSE 0 END) AS single_requests,
                SUM(CASE WHEN COALESCE(cached_input_tokens, 0) > 0 THEN 1 ELSE 0 END) AS cached_requests,
                COUNT(DISTINCT prompt_cache_key) AS cache_keys,
                MIN(created_at) AS first_request_at,
                MAX(created_at) AS last_request_at,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                {cache_write_tokens_sum_sql} AS cache_write_tokens,
                {prompt_versions_sql} AS prompt_versions,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                {reasoning_tokens_sql} AS reasoning_tokens,
                {output_chars_sql} AS output_chars,
                {incomplete_responses_sql} AS incomplete_responses
            FROM recent
            """,
            (feature, row_limit),
        ).fetchone()
        filtered_row = None
        if filter_channel:
            filtered_row = conn.execute(
                f"""
                WITH recent AS (
                    SELECT *
                    FROM ai_usage_log
                    WHERE feature = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                SELECT
                    COUNT(*) AS total_requests,
                    {attempt_counts_sql}
                    SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS ok_requests,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS error_requests,
                    SUM(CASE WHEN stage = 'single' THEN 1 ELSE 0 END) AS single_requests,
                    SUM(CASE WHEN COALESCE(cached_input_tokens, 0) > 0 THEN 1 ELSE 0 END) AS cached_requests,
                    MIN(created_at) AS first_request_at,
                    MAX(created_at) AS last_request_at,
                    SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                    SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                    {cache_write_tokens_sum_sql} AS cache_write_tokens,
                    {prompt_versions_sql} AS prompt_versions,
                    SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                    {reasoning_tokens_sql} AS reasoning_tokens,
                    {output_chars_sql} AS output_chars,
                    {incomplete_responses_sql} AS incomplete_responses
                FROM recent
                WHERE channel = ?
                """,
                (feature, row_limit, filter_channel),
            ).fetchone()
        recent_rows = conn.execute(
            """
            WITH recent AS (
                SELECT *
                FROM ai_usage_log
                WHERE feature = ?
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT created_at, stage, channel, status, input_tokens, cached_input_tokens,
                   cache_write_tokens, output_tokens,
                   reasoning_tokens, output_chars,
                   response_status, incomplete_reason,
                   prompt_cache_key
            FROM recent
            ORDER BY id DESC
            LIMIT ?
            """,
            (feature, row_limit, recent_rows_limit),
        ).fetchall()
        global_row = dict(global_row) if global_row is not None else None
        filtered_row = dict(filtered_row) if filtered_row is not None else None
        if operation_key is not None and global_row is not None:
            metadata = ('run_id', 'job_id', 'batch_id', 'generation',
                        'channel', 'stage', 'request_index')
            # Identifiers come only from the fixed metadata allowlist above.
            observations = conn.execute(f"""SELECT {','.join(metadata)} FROM ai_usage_log
                WHERE feature=? ORDER BY id DESC LIMIT ?""", (feature, row_limit)).fetchall()
            identities = [(dict(row), operation_key(dict(row))) for row in observations]

            def count_operations(items):
                keys = {key for _, key in items if key is not None}
                return len(keys) if keys or not items else None

            global_row['observed_logical_operations'] = count_operations(identities)
            if filtered_row is not None:
                filtered_row['observed_logical_operations'] = count_operations([
                    item for item in identities if item[0].get('channel') == filter_channel])
    finally:
        conn.close()
    if global_row is None or int(global_row["total_requests"] or 0) == 0:
        return None
    return {
        "global": dict(global_row),
        "filtered": dict(filtered_row) if filtered_row is not None else None,
        "recent_rows": [dict(row) for row in recent_rows],
        "row_limit": row_limit,
    }


def format_ai_usage_summary(
    summary: dict[str, Any],
    *,
    title: str,
    subject_label: str | None = None,
    subject_value: str | None = None,
    hide_unavailable: bool = False,
) -> str:
    def optional_metric(label: str, value: Any) -> str | None:
        if value is None:
            return None if hide_unavailable else f"- {label}: unavailable"
        return f"- {label}: {int(value)}"

    global_stats = summary["global"]
    filtered_stats = summary.get("filtered") or {}
    recent_rows = summary.get("recent_rows") or []
    row_limit = int(summary.get("row_limit") or 0)
    global_input = int(global_stats.get("input_tokens") or 0)
    global_cached = int(global_stats.get("cached_input_tokens") or 0)
    global_single = int(global_stats.get("single_requests") or 0)
    global_saved_pct = round((global_cached / global_input) * 100, 1) if global_input > 0 else 0.0
    global_single_pct = round((global_single / int(global_stats.get("total_requests") or 1)) * 100, 1) if int(global_stats.get("total_requests") or 0) > 0 else 0.0
    lines = [
        f"{title}:",
        f"- analysis window: latest {row_limit} requests",
        f"- all requests: {int(global_stats.get('total_requests') or 0)}",
        f"- ok: {int(global_stats.get('ok_requests') or 0)}",
        f"- errors: {int(global_stats.get('error_requests') or 0)}",
        f"- single-pass requests: {global_single} ({global_single_pct}%)",
        f"- requests with cached input: {int(global_stats.get('cached_requests') or 0)}",
        f"- cache keys: {int(global_stats.get('cache_keys') or 0)}",
        f"- input tokens: {global_input}",
        f"- cached input tokens: {global_cached}",
        f"- output tokens: {int(global_stats.get('output_tokens') or 0)}",
        f"- cached share of input tokens: {global_saved_pct}%",
    ]
    for key, label in (
        ('physical_attempts', 'identified physical attempts'),
        ('observed_logical_operations', 'observed logical operations'),
        ('unknown_requests', 'unknown outcomes'),
        ('pending_requests', 'unfinished attempts'),
        ('unknown_usage_requests', 'unknown usage requests'),
    ):
        if global_stats.get(key) is not None:
            lines.append(f'- {label}: {int(global_stats[key])}')
    if global_stats.get('unknown_usage_requests'):
        lines.append('- token totals cover known counters only; unknown usage is not zero')
    for label, value in (
        ("prompt versions", global_stats.get("prompt_versions")),
        ("cache write tokens", global_stats.get("cache_write_tokens")),
        ("reasoning tokens", global_stats.get("reasoning_tokens")),
        ("visible output chars", global_stats.get("output_chars")),
        ("incomplete responses", global_stats.get("incomplete_responses")),
    ):
        line = optional_metric(label, value)
        if line:
            lines.append(line)
    first_request_at = str(global_stats.get("first_request_at") or "").strip()
    last_request_at = str(global_stats.get("last_request_at") or "").strip()
    if first_request_at:
        lines.append(f"- first request: {first_request_at}")
    if last_request_at:
        lines.append(f"- last request: {last_request_at}")
    filtered_total = int(filtered_stats.get("total_requests") or 0)
    if subject_label and subject_value and filtered_total > 0:
        filtered_input = int(filtered_stats.get("input_tokens") or 0)
        filtered_cached = int(filtered_stats.get("cached_input_tokens") or 0)
        filtered_single = int(filtered_stats.get("single_requests") or 0)
        filtered_saved_pct = round((filtered_cached / filtered_input) * 100, 1) if filtered_input > 0 else 0.0
        filtered_single_pct = round((filtered_single / filtered_total) * 100, 1) if filtered_total > 0 else 0.0
        lines.extend(
            [
                "",
                f"{subject_label} ({subject_value}):",
                f"- requests: {filtered_total}",
                f"- single-pass requests: {filtered_single} ({filtered_single_pct}%)",
                f"- cached input tokens: {filtered_cached}",
                f"- cached share of input tokens: {filtered_saved_pct}%",
            ]
        )
        for label, value in (
            ("prompt versions", filtered_stats.get("prompt_versions")),
            ("cache write tokens", filtered_stats.get("cache_write_tokens")),
            ("reasoning tokens", filtered_stats.get("reasoning_tokens")),
            ("visible output chars", filtered_stats.get("output_chars")),
            ("incomplete responses", filtered_stats.get("incomplete_responses")),
        ):
            line = optional_metric(label, value)
            if line:
                lines.append(line)
    if recent_rows:
        lines.append("")
        lines.append("Latest rounds:")
        for row in recent_rows:
            input_tokens = int(row.get("input_tokens") or 0)
            cached_tokens = int(row.get("cached_input_tokens") or 0)
            cached_pct = round((cached_tokens / input_tokens) * 100, 1) if input_tokens > 0 else 0.0
            cache_writes = row.get("cache_write_tokens")
            line = f"- {row.get('stage')}: {row.get('status')}, input={input_tokens}, cached={cached_tokens} ({cached_pct}%)"
            if cache_writes is not None:
                line += f", cache-writes={int(cache_writes)}"
            elif not hide_unavailable:
                line += ", cache-writes=unavailable"
            line += f", output={int(row.get('output_tokens') or 0)}"
            telemetry: list[str] = []
            for label, value in (
                ("reasoning", row.get("reasoning_tokens")),
                ("visible-chars", row.get("output_chars")),
            ):
                if value is not None:
                    telemetry.append(f"{label}={int(value)}")
                elif not hide_unavailable:
                    telemetry.append(f"{label}=unavailable")
            response_status = row.get("response_status")
            if response_status is not None:
                telemetry.append(f"response={response_status}")
            elif not hide_unavailable:
                telemetry.append("response=unavailable")
            incomplete_reason = str(row.get("incomplete_reason") or "").strip()
            if incomplete_reason:
                telemetry.append(f"incomplete={incomplete_reason}")
            suffix = f", {', '.join(telemetry)}" if telemetry else ""
            lines.append(f"{line}{suffix}")
    return "\n".join(lines)
