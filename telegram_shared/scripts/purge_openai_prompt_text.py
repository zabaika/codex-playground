#!/usr/bin/env python3
"""Clear persisted full OpenAI prompt text from Telegram SQLite usage logs."""

from __future__ import annotations

import argparse
import sqlite3
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONNECTOR_RUNTIME_FILE = REPO_ROOT / "telegram_connector" / "config" / "runtime.local.toml"
CONNECTOR_DEFAULT_DB = REPO_ROOT / "telegram_connector" / "data" / "telegram_history.sqlite3"
AGENT_DEFAULT_DB = REPO_ROOT / "telegram_agent_bot" / "data" / "telegram_agent.sqlite3"


def resolve_connector_db() -> Path:
    if not CONNECTOR_RUNTIME_FILE.exists():
        return CONNECTOR_DEFAULT_DB
    with CONNECTOR_RUNTIME_FILE.open("rb") as fh:
        config = tomllib.load(fh)
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        return CONNECTOR_DEFAULT_DB
    raw_path = str(paths.get("history_db") or "").strip()
    if not raw_path:
        return CONNECTOR_DEFAULT_DB
    return Path(raw_path).expanduser()


def default_db_paths() -> list[Path]:
    paths = [resolve_connector_db(), AGENT_DEFAULT_DB]
    unique_paths: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique_paths.append(path)
    return unique_paths


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return column_name in {str(row[1]) for row in rows}


def purge_db(db_path: Path) -> tuple[int, int]:
    if not db_path.exists():
        print(f"{db_path}: skipped, database does not exist")
        return 0, 0
    conn = sqlite3.connect(db_path)
    try:
        if not table_exists(conn, "ai_usage_log"):
            print(f"{db_path}: skipped, ai_usage_log table does not exist")
            return 0, 0
        if not has_column(conn, "ai_usage_log", "prompt_text"):
            print(f"{db_path}: skipped, ai_usage_log.prompt_text column does not exist")
            return 0, 0
        before = int(conn.execute("SELECT COUNT(*) FROM ai_usage_log WHERE prompt_text IS NOT NULL").fetchone()[0])
        conn.execute("UPDATE ai_usage_log SET prompt_text = NULL WHERE prompt_text IS NOT NULL")
        conn.commit()
        after = int(conn.execute("SELECT COUNT(*) FROM ai_usage_log WHERE prompt_text IS NOT NULL").fetchone()[0])
        print(f"{db_path}: cleared={before - after}, remaining={after}")
        return before - after, after
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        help="SQLite database path to purge. Can be passed more than once. Defaults to Telegram connector and agent bot DBs.",
    )
    args = parser.parse_args()

    db_paths = [Path(raw_path).expanduser() for raw_path in args.db] if args.db else default_db_paths()
    total_cleared = 0
    total_remaining = 0
    for db_path in db_paths:
        cleared, remaining = purge_db(db_path)
        total_cleared += cleared
        total_remaining += remaining
    print(f"total: cleared={total_cleared}, remaining={total_remaining}")
    return 0 if total_remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
