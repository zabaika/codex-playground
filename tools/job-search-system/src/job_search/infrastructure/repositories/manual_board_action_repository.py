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


class ManualBoardActionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_action(
        self,
        *,
        candidate_id: str,
        platform: str,
        action_type: str,
        action_state: str,
        canonical_vacancy_id: str | None,
        application_id: str | None,
        artifact_id: str | None,
        external_target: str | None,
        occurred_at: str,
        notes: str | None,
        idempotency_key: str,
        external_action_approval_id: str | None = None,
    ) -> tuple[dict[str, object], bool]:
        existing = self._conn.execute(
            """
            SELECT *
            FROM manual_board_actions
            WHERE candidate_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (candidate_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), True

        board_action_id = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """
            INSERT INTO manual_board_actions(
                board_action_id, candidate_id, platform, action_type, action_state,
                canonical_vacancy_id, application_id, artifact_id, external_target,
                occurred_at, notes, idempotency_key, created_at, updated_at,
                external_action_approval_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                board_action_id,
                candidate_id,
                platform,
                action_type,
                action_state,
                canonical_vacancy_id,
                application_id,
                artifact_id,
                external_target,
                occurred_at,
                notes,
                idempotency_key,
                now,
                now,
                external_action_approval_id,
            ),
        )
        return self.get_action(candidate_id=candidate_id, board_action_id=board_action_id), False

    def get_action(self, *, candidate_id: str, board_action_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM manual_board_actions
            WHERE candidate_id = ? AND board_action_id = ?
            LIMIT 1
            """,
            (candidate_id, board_action_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_actions(
        self,
        *,
        candidate_id: str,
        platform: str | None,
        canonical_vacancy_id: str | None,
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM manual_board_actions WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        if canonical_vacancy_id:
            sql += " AND canonical_vacancy_id = ?"
            params.append(canonical_vacancy_id)
        sql += " ORDER BY occurred_at DESC, created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]
