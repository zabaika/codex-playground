from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class ArtifactRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def find_reusable_artifact(self, *, candidate_id: str, artifact_type: str, content_hash: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM artifacts
            WHERE candidate_id = ? AND artifact_type = ? AND content_hash = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (candidate_id, artifact_type, content_hash),
        ).fetchone()
        return _row_to_dict(row)

    def list_candidate_artifacts(self, *, candidate_id: str, artifact_type: str | None = None) -> list[dict[str, object]]:
        sql = "SELECT * FROM artifacts WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if artifact_type is not None:
            sql += " AND artifact_type = ?"
            params.append(artifact_type)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def find_derived_artifact(
        self,
        *,
        candidate_id: str,
        artifact_type: str,
        derived_from_artifact_id: str,
        content_hash: str,
    ) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM artifacts
            WHERE candidate_id = ? AND artifact_type = ? AND derived_from_artifact_id = ? AND content_hash = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (candidate_id, artifact_type, derived_from_artifact_id, content_hash),
        ).fetchone()
        return _row_to_dict(row)

    def get_derived_artifact(
        self,
        *,
        candidate_id: str,
        artifact_type: str,
        derived_from_artifact_id: str,
    ) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM artifacts
            WHERE candidate_id = ? AND artifact_type = ? AND derived_from_artifact_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (candidate_id, artifact_type, derived_from_artifact_id),
        ).fetchone()
        return _row_to_dict(row)

    def create_artifact(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        candidate_id: str,
        storage_path: str,
        content_hash: str,
        notes: str | None = None,
        derived_from_artifact_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO artifacts(
                artifact_id, artifact_type, candidate_id, version, derived_from_artifact_id,
                storage_path, content_hash, language, notes, created_at
            ) VALUES(?, ?, ?, 1, ?, ?, ?, NULL, ?, ?)
            """,
            (artifact_id, artifact_type, candidate_id, derived_from_artifact_id, storage_path, content_hash, notes, _now()),
        )

    def get_artifact(self, artifact_id: str) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return _row_to_dict(row)

    def update_artifact_content(
        self,
        *,
        artifact_id: str,
        storage_path: str,
        content_hash: str,
        notes: str | None = None,
        derived_from_artifact_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE artifacts
            SET storage_path = ?, content_hash = ?, notes = ?, derived_from_artifact_id = COALESCE(?, derived_from_artifact_id)
            WHERE artifact_id = ?
            """,
            (storage_path, content_hash, notes, derived_from_artifact_id, artifact_id),
        )

    def register_candidate_source(
        self,
        *,
        candidate_id: str,
        artifact_id: str,
        source_kind: str,
        source_origin: str,
        external_profile_id: str | None,
        notes: str | None,
    ) -> str:
        existing = self._conn.execute(
            "SELECT candidate_source_id FROM candidate_sources WHERE candidate_id = ? AND artifact_id = ?",
            (candidate_id, artifact_id),
        ).fetchone()
        if existing is not None:
            return str(existing["candidate_source_id"])
        candidate_source_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO candidate_sources(
                candidate_source_id, candidate_id, artifact_id, source_kind, source_origin,
                external_profile_id, imported_at, notes
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate_source_id, candidate_id, artifact_id, source_kind, source_origin, external_profile_id, _now(), notes),
        )
        return candidate_source_id

    def list_candidate_sources(self, candidate_id: str) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT cs.*, a.storage_path, a.artifact_type, a.content_hash
            FROM candidate_sources cs
            JOIN artifacts a ON a.artifact_id = cs.artifact_id
            WHERE cs.candidate_id = ?
            ORDER BY cs.imported_at DESC
            """,
            (candidate_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
