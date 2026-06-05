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


class ApprovalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_approval(
        self,
        *,
        candidate_id: str,
        approval_type: str,
        approval_state: str,
        actor: str,
        artifact_id: str | None,
        target_entity_type: str | None,
        target_entity_id: str | None,
        action_type: str | None,
        platform: str | None,
        external_target: str | None,
        reason: str | None,
        notes: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        existing = self._conn.execute(
            """
            SELECT *
            FROM approval_records
            WHERE candidate_id = ? AND approval_type = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (candidate_id, approval_type, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), True

        approval_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO approval_records(
                approval_id, candidate_id, approval_type, approval_state, actor,
                artifact_id, target_entity_type, target_entity_id, action_type,
                platform, external_target, reason, notes, idempotency_key, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                candidate_id,
                approval_type,
                approval_state,
                actor,
                artifact_id,
                target_entity_type,
                target_entity_id,
                action_type,
                platform,
                external_target,
                reason,
                notes,
                idempotency_key,
                _now(),
            ),
        )
        return self.get_approval(candidate_id=candidate_id, approval_id=approval_id), False

    def get_approval(self, *, candidate_id: str, approval_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM approval_records
            WHERE candidate_id = ? AND approval_id = ?
            LIMIT 1
            """,
            (candidate_id, approval_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_approvals(
        self,
        *,
        candidate_id: str,
        approval_type: str | None = None,
        artifact_id: str | None = None,
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM approval_records WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if approval_type:
            sql += " AND approval_type = ?"
            params.append(approval_type)
        if artifact_id:
            sql += " AND artifact_id = ?"
            params.append(artifact_id)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]
