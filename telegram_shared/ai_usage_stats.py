"""Shared bounded stats queries for ai_usage_log."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def fetch_ai_usage_summary(
    db_path: Path,
    *,
    feature: str,
    row_limit: int,
    filter_channel: str | None = None,
    recent_rows_limit: int = 3,
) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        global_row = conn.execute(
            """
            WITH recent AS (
                SELECT *
                FROM ai_usage_log
                WHERE feature = ?
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT
                COUNT(*) AS total_requests,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_requests,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_requests,
                SUM(CASE WHEN COALESCE(cached_input_tokens, 0) > 0 THEN 1 ELSE 0 END) AS cached_requests,
                COUNT(DISTINCT prompt_cache_key) AS cache_keys,
                MIN(created_at) AS first_request_at,
                MAX(created_at) AS last_request_at,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens
            FROM recent
            """,
            (feature, row_limit),
        ).fetchone()
        filtered_row = None
        if filter_channel:
            filtered_row = conn.execute(
                """
                WITH recent AS (
                    SELECT *
                    FROM ai_usage_log
                    WHERE feature = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                SELECT
                    COUNT(*) AS total_requests,
                    SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_requests,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_requests,
                    SUM(CASE WHEN COALESCE(cached_input_tokens, 0) > 0 THEN 1 ELSE 0 END) AS cached_requests,
                    MIN(created_at) AS first_request_at,
                    MAX(created_at) AS last_request_at,
                    SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                    SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                    SUM(COALESCE(output_tokens, 0)) AS output_tokens
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
                   output_tokens, prompt_cache_key
            FROM recent
            ORDER BY id DESC
            LIMIT ?
            """,
            (feature, row_limit, recent_rows_limit),
        ).fetchall()
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
) -> str:
    global_stats = summary["global"]
    filtered_stats = summary.get("filtered") or {}
    recent_rows = summary.get("recent_rows") or []
    row_limit = int(summary.get("row_limit") or 0)
    global_input = int(global_stats.get("input_tokens") or 0)
    global_cached = int(global_stats.get("cached_input_tokens") or 0)
    global_saved_pct = round((global_cached / global_input) * 100, 1) if global_input > 0 else 0.0
    lines = [
        f"{title}:",
        f"- analysis window: latest {row_limit} requests",
        f"- all requests: {int(global_stats.get('total_requests') or 0)}",
        f"- ok: {int(global_stats.get('ok_requests') or 0)}",
        f"- errors: {int(global_stats.get('error_requests') or 0)}",
        f"- requests with cached input: {int(global_stats.get('cached_requests') or 0)}",
        f"- cache keys: {int(global_stats.get('cache_keys') or 0)}",
        f"- input tokens: {global_input}",
        f"- cached input tokens: {global_cached}",
        f"- output tokens: {int(global_stats.get('output_tokens') or 0)}",
        f"- cached share of input tokens: {global_saved_pct}%",
    ]
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
        filtered_saved_pct = round((filtered_cached / filtered_input) * 100, 1) if filtered_input > 0 else 0.0
        lines.extend(
            [
                "",
                f"{subject_label} ({subject_value}):",
                f"- requests: {filtered_total}",
                f"- cached input tokens: {filtered_cached}",
                f"- cached share of input tokens: {filtered_saved_pct}%",
            ]
        )
    if recent_rows:
        lines.append("")
        lines.append("Latest rounds:")
        for row in recent_rows:
            input_tokens = int(row.get("input_tokens") or 0)
            cached_tokens = int(row.get("cached_input_tokens") or 0)
            cached_pct = round((cached_tokens / input_tokens) * 100, 1) if input_tokens > 0 else 0.0
            lines.append(
                f"- {row.get('stage')}: {row.get('status')}, input={input_tokens}, cached={cached_tokens} ({cached_pct}%), output={int(row.get('output_tokens') or 0)}"
            )
    return "\n".join(lines)
