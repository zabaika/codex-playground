from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import sqlite3
import tomllib


COMMON_ROOT = Path(__file__).resolve().parent
DEFAULT_SQLITE_CONFIG_PATH = COMMON_ROOT / "config" / "sqlite.toml"
VALID_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
VALID_SYNCHRONOUS_MODES = {"OFF", "NORMAL", "FULL", "EXTRA"}


@dataclass(frozen=True, slots=True)
class SqliteConfig:
    busy_timeout_ms: int
    journal_mode: str
    synchronous: str
    foreign_keys: bool
    autocommit: bool


def load_sqlite_config(config_path: Path | None = None) -> SqliteConfig:
    path = config_path or DEFAULT_SQLITE_CONFIG_PATH
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    section = raw.get("sqlite")
    if not isinstance(section, dict):
        raise KeyError(f"Missing [sqlite] config section in {path}")

    busy_timeout_ms = max(0, int(section.get("busy_timeout_ms", 0)))
    journal_mode = str(section.get("journal_mode", "WAL")).strip().upper()
    synchronous = str(section.get("synchronous", "NORMAL")).strip().upper()
    foreign_keys = bool(section.get("foreign_keys", True))
    autocommit = bool(section.get("autocommit", True))

    if journal_mode not in VALID_JOURNAL_MODES:
        raise ValueError(f"Unsupported sqlite journal_mode: {journal_mode}")
    if synchronous not in VALID_SYNCHRONOUS_MODES:
        raise ValueError(f"Unsupported sqlite synchronous mode: {synchronous}")

    return SqliteConfig(
        busy_timeout_ms=busy_timeout_ms,
        journal_mode=journal_mode,
        synchronous=synchronous,
        foreign_keys=foreign_keys,
        autocommit=autocommit,
    )


def _normalize_db_target(db_target: Path | str) -> tuple[str, bool]:
    if isinstance(db_target, Path):
        db_target.parent.mkdir(parents=True, exist_ok=True)
        return str(db_target), False
    raw = str(db_target)
    if raw == ":memory:":
        return raw, False
    if raw.startswith("file:"):
        return raw, True
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path), False


def connect_sqlite(
    db_target: Path | str,
    *,
    config: SqliteConfig | None = None,
    row_factory: object | None = sqlite3.Row,
) -> sqlite3.Connection:
    effective_config = config or load_sqlite_config()
    normalized_target, uri = _normalize_db_target(db_target)
    conn = sqlite3.connect(
        normalized_target,
        timeout=effective_config.busy_timeout_ms / 1000,
        isolation_level=None if effective_config.autocommit else "",
        uri=uri,
    )
    if row_factory is not None:
        conn.row_factory = row_factory
    configure_connection(conn, config=effective_config)
    return conn


def configure_connection(conn: sqlite3.Connection, *, config: SqliteConfig | None = None) -> sqlite3.Connection:
    effective_config = config or load_sqlite_config()
    conn.execute(f"PRAGMA journal_mode = {effective_config.journal_mode}")
    conn.execute(f"PRAGMA synchronous = {effective_config.synchronous}")
    conn.execute(f"PRAGMA foreign_keys = {'ON' if effective_config.foreign_keys else 'OFF'}")
    conn.execute(f"PRAGMA busy_timeout = {effective_config.busy_timeout_ms}")
    return conn


@contextmanager
def write_tx(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        rollback_quietly(conn)
        raise
    else:
        conn.commit()


def rollback_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.rollback()
    except sqlite3.Error:
        return


def close_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except sqlite3.Error:
        return
