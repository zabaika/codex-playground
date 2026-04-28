from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import ParsedNote

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  folder TEXT NOT NULL,
  note_type TEXT NOT NULL,
  lead_summary TEXT,
  tags_json TEXT NOT NULL,
  aliases_json TEXT,
  headings_json TEXT,
  links_out_json TEXT,
  mtime INTEGER NOT NULL,
  size_bytes INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  note_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  heading TEXT,
  chunk_role TEXT,
  anchor TEXT,
  text TEXT NOT NULL,
  text_normalized TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  token_estimate INTEGER,
  FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE,
  UNIQUE(note_id, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  path,
  title,
  lead_summary,
  heading,
  tags,
  text,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
  path,
  title,
  lead_summary,
  headings,
  tags,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path);
CREATE INDEX IF NOT EXISTS idx_notes_mtime ON notes(mtime);
CREATE INDEX IF NOT EXISTS idx_chunks_note_id ON chunks(note_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def upsert_note(conn: sqlite3.Connection, note: ParsedNote) -> None:
    indexed_at = int(time.time())
    conn.execute(
        """
        INSERT INTO notes (
          path, title, folder, note_type, lead_summary, tags_json,
          aliases_json, headings_json, links_out_json,
          mtime, size_bytes, content_hash, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          title=excluded.title,
          folder=excluded.folder,
          note_type=excluded.note_type,
          lead_summary=excluded.lead_summary,
          tags_json=excluded.tags_json,
          aliases_json=excluded.aliases_json,
          headings_json=excluded.headings_json,
          links_out_json=excluded.links_out_json,
          mtime=excluded.mtime,
          size_bytes=excluded.size_bytes,
          content_hash=excluded.content_hash,
          indexed_at=excluded.indexed_at
        """,
        (
            str(note.path),
            note.title,
            note.folder,
            note.note_type,
            note.lead_summary,
            json.dumps(note.tags, ensure_ascii=False),
            json.dumps(note.aliases, ensure_ascii=False),
            json.dumps(note.headings, ensure_ascii=False),
            json.dumps(note.links_out, ensure_ascii=False),
            note.mtime,
            note.size_bytes,
            note.content_hash,
            indexed_at,
        ),
    )
    note_id = conn.execute("SELECT id FROM notes WHERE path = ?", (str(note.path),)).fetchone()["id"]
    conn.execute("DELETE FROM note_fts WHERE path = ?", (str(note.path),))
    conn.execute(
        "INSERT INTO note_fts(path, title, lead_summary, headings, tags) VALUES (?, ?, ?, ?, ?)",
        (
            str(note.path),
            note.title,
            note.lead_summary,
            " ".join(note.headings),
            " ".join(note.tags),
        ),
    )
    existing_rows = conn.execute("SELECT id FROM chunks WHERE note_id = ?", (note_id,)).fetchall()
    if existing_rows:
        chunk_ids = [row["id"] for row in existing_rows]
        conn.executemany("DELETE FROM chunk_fts WHERE rowid = ?", [(chunk_id,) for chunk_id in chunk_ids])
    conn.execute("DELETE FROM chunks WHERE note_id = ?", (note_id,))
    for chunk in note.chunks:
        cursor = conn.execute(
            """
            INSERT INTO chunks (
              note_id, chunk_index, heading, chunk_role, anchor, text,
              text_normalized, char_count, token_estimate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                chunk.chunk_index,
                chunk.heading,
                chunk.chunk_role,
                chunk.anchor,
                chunk.text,
                chunk.text_normalized,
                chunk.char_count,
                chunk.token_estimate,
            ),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunk_fts(rowid, path, title, lead_summary, heading, tags, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chunk_id,
                str(note.path),
                note.title,
                note.lead_summary,
                chunk.heading,
                " ".join(note.tags),
                chunk.text_normalized,
            ),
        )


def delete_missing_notes(conn: sqlite3.Connection, existing_paths: set[str]) -> int:
    rows = conn.execute("SELECT id, path FROM notes").fetchall()
    deleted = 0
    for row in rows:
        if row["path"] in existing_paths:
            continue
        conn.execute("DELETE FROM note_fts WHERE path = ?", (row["path"],))
        chunk_rows = conn.execute("SELECT id FROM chunks WHERE note_id = ?", (row["id"],)).fetchall()
        if chunk_rows:
            conn.executemany("DELETE FROM chunk_fts WHERE rowid = ?", [(chunk_row["id"],) for chunk_row in chunk_rows])
        conn.execute("DELETE FROM notes WHERE id = ?", (row["id"],))
        deleted += 1
    return deleted


def get_note_record(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT path, mtime, size_bytes, content_hash FROM notes WHERE path = ?",
        (path,),
    ).fetchone()


def get_status(conn: sqlite3.Connection) -> dict[str, int | str | None]:
    note_count = conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
    chunk_count = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
    latest_indexed = conn.execute("SELECT MAX(indexed_at) AS v FROM notes").fetchone()["v"]
    return {
        "notes": note_count,
        "chunks": chunk_count,
        "last_indexed_at": latest_indexed,
    }
