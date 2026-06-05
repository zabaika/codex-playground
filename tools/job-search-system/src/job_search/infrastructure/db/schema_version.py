from __future__ import annotations

from pathlib import Path
import sqlite3

from job_search.infrastructure.db.connection import write_tx


def ensure_schema_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_versions(conn: sqlite3.Connection) -> set[str]:
    ensure_schema_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row[0]) for row in rows}


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    applied_now: list[str] = []
    with write_tx(conn, immediate=True):
        ensure_schema_table(conn)
        applied = applied_versions(conn)
        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.stem
            if version in applied:
                continue
            for statement in _sql_statements(path.read_text(encoding="utf-8")):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES(?, datetime('now'))",
                (version,),
            )
            applied.add(version)
            applied_now.append(version)
    return applied_now


def _sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]
