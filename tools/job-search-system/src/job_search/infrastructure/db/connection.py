from __future__ import annotations

from pathlib import Path
import sqlite3

from common.sqlite import SqliteConfig, close_quietly, connect_sqlite, load_sqlite_config, write_tx


def load_connection(db_path: Path, sqlite_config_path: Path) -> sqlite3.Connection:
    config: SqliteConfig = load_sqlite_config(sqlite_config_path)
    return connect_sqlite(db_path, config=config)


__all__ = ["close_quietly", "load_connection", "write_tx"]
