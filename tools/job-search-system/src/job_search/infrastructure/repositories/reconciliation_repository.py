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


class ReconciliationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_item(
        self,
        *,
        candidate_id: str,
        board_action_id: str | None,
        canonical_vacancy_id: str | None,
        application_id: str | None,
        platform: str | None,
        external_target: str | None,
        drift_type: str,
        outcome: str,
        review_status: str,
        reason: str,
        recommended_action: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        existing = self._conn.execute(
            """
            SELECT *
            FROM reconciliation_items
            WHERE candidate_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (candidate_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), True

        reconciliation_item_id = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """
            INSERT INTO reconciliation_items(
                reconciliation_item_id, candidate_id, board_action_id, canonical_vacancy_id,
                application_id, platform, external_target, drift_type, outcome, review_status,
                reason, recommended_action, idempotency_key, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reconciliation_item_id,
                candidate_id,
                board_action_id,
                canonical_vacancy_id,
                application_id,
                platform,
                external_target,
                drift_type,
                outcome,
                review_status,
                reason,
                recommended_action,
                idempotency_key,
                now,
                now,
            ),
        )
        return self.get_item(candidate_id=candidate_id, reconciliation_item_id=reconciliation_item_id), False

    def get_item(self, *, candidate_id: str, reconciliation_item_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM reconciliation_items
            WHERE candidate_id = ? AND reconciliation_item_id = ?
            LIMIT 1
            """,
            (candidate_id, reconciliation_item_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_items(
        self,
        *,
        candidate_id: str,
        review_status: str | None = None,
        outcome: str | None = None,
        platform: str | None = None,
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM reconciliation_items WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if review_status:
            sql += " AND review_status = ?"
            params.append(review_status)
        if outcome:
            sql += " AND outcome = ?"
            params.append(outcome)
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        sql += " ORDER BY updated_at DESC, created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def resolve_item(
        self,
        *,
        candidate_id: str,
        reconciliation_item_id: str,
        review_status: str,
        resolution_notes: str | None,
    ) -> dict[str, object]:
        now = _now()
        self._conn.execute(
            """
            UPDATE reconciliation_items
            SET review_status = ?, resolution_notes = ?, updated_at = ?, resolved_at = ?
            WHERE candidate_id = ? AND reconciliation_item_id = ?
            """,
            (review_status, resolution_notes, now, now, candidate_id, reconciliation_item_id),
        )
        item = self.get_item(candidate_id=candidate_id, reconciliation_item_id=reconciliation_item_id)
        if item is None:
            raise KeyError(f"Unknown reconciliation_item_id: {reconciliation_item_id}")
        return item
