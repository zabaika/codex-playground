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


class VacancyUrlEnrichmentRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_seed(
        self,
        *,
        candidate_id: str,
        platform: str,
        source_url: str,
        source_origin: str,
        notes: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        existing = self._conn.execute(
            """
            SELECT *
            FROM vacancy_url_enrichment_seeds
            WHERE candidate_id = ? AND (source_url = ? OR idempotency_key = ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (candidate_id, source_url, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), True

        url_seed_id = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """
            INSERT INTO vacancy_url_enrichment_seeds(
                url_seed_id, candidate_id, platform, source_url, source_origin, seed_status,
                notes, idempotency_key, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (url_seed_id, candidate_id, platform, source_url, source_origin, notes, idempotency_key, now, now),
        )
        return self.get_seed(candidate_id=candidate_id, url_seed_id=url_seed_id), False

    def get_seed(self, *, candidate_id: str, url_seed_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM vacancy_url_enrichment_seeds
            WHERE candidate_id = ? AND url_seed_id = ?
            LIMIT 1
            """,
            (candidate_id, url_seed_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_seeds(
        self,
        *,
        candidate_id: str,
        seed_status: str | None = None,
        platform: str | None = None,
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM vacancy_url_enrichment_seeds WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if seed_status:
            sql += " AND seed_status = ?"
            params.append(seed_status)
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        sql += " ORDER BY updated_at DESC, created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def update_preview(
        self,
        *,
        candidate_id: str,
        url_seed_id: str,
        latest_preview_json: str,
    ) -> dict[str, object]:
        self._conn.execute(
            """
            UPDATE vacancy_url_enrichment_seeds
            SET seed_status = 'previewed', latest_preview_json = ?, updated_at = ?
            WHERE candidate_id = ? AND url_seed_id = ?
            """,
            (latest_preview_json, _now(), candidate_id, url_seed_id),
        )
        seed = self.get_seed(candidate_id=candidate_id, url_seed_id=url_seed_id)
        if seed is None:
            raise KeyError(f"Unknown url_seed_id: {url_seed_id}")
        return seed

    def mark_imported(
        self,
        *,
        candidate_id: str,
        url_seed_id: str,
        canonical_vacancy_id: str,
        source_occurrence_id: str,
    ) -> dict[str, object]:
        now = _now()
        self._conn.execute(
            """
            UPDATE vacancy_url_enrichment_seeds
            SET seed_status = 'imported',
                imported_canonical_vacancy_id = ?,
                imported_source_occurrence_id = ?,
                updated_at = ?,
                imported_at = ?
            WHERE candidate_id = ? AND url_seed_id = ?
            """,
            (canonical_vacancy_id, source_occurrence_id, now, now, candidate_id, url_seed_id),
        )
        seed = self.get_seed(candidate_id=candidate_id, url_seed_id=url_seed_id)
        if seed is None:
            raise KeyError(f"Unknown url_seed_id: {url_seed_id}")
        return seed

    def reject_seed(
        self,
        *,
        candidate_id: str,
        url_seed_id: str,
        rejection_reason: str | None,
    ) -> dict[str, object]:
        now = _now()
        self._conn.execute(
            """
            UPDATE vacancy_url_enrichment_seeds
            SET seed_status = 'rejected', rejection_reason = ?, updated_at = ?, rejected_at = ?
            WHERE candidate_id = ? AND url_seed_id = ?
            """,
            (rejection_reason, now, now, candidate_id, url_seed_id),
        )
        seed = self.get_seed(candidate_id=candidate_id, url_seed_id=url_seed_id)
        if seed is None:
            raise KeyError(f"Unknown url_seed_id: {url_seed_id}")
        return seed
