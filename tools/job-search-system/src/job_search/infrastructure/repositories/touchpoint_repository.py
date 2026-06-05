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


class TouchpointRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_touchpoint(
        self,
        *,
        candidate_id: str,
        canonical_vacancy_id: str,
        application_id: str | None,
        message_artifact_id: str | None,
        channel: str,
        direction: str,
        touchpoint_state: str,
        contact_name: str | None,
        occurred_at: str | None,
        notes: str | None,
    ) -> dict[str, object]:
        touchpoint_id = str(uuid.uuid4())
        now = _now()
        occurred = occurred_at or now
        self._conn.execute(
            """
            INSERT INTO touchpoints(
                touchpoint_id, candidate_id, canonical_vacancy_id, application_id, message_artifact_id,
                channel, direction, touchpoint_state, contact_name, occurred_at, replied_at, notes, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                touchpoint_id,
                candidate_id,
                canonical_vacancy_id,
                application_id,
                message_artifact_id,
                channel,
                direction,
                touchpoint_state,
                contact_name,
                occurred,
                notes,
                now,
                now,
            ),
        )
        return _row_to_dict(
            self._conn.execute("SELECT * FROM touchpoints WHERE touchpoint_id = ?", (touchpoint_id,)).fetchone()
        )

    def update_touchpoint_state(
        self,
        *,
        candidate_id: str,
        touchpoint_id: str,
        touchpoint_state: str,
        replied_at: str | None,
    ) -> dict[str, object] | None:
        self._conn.execute(
            """
            UPDATE touchpoints
            SET touchpoint_state = ?, replied_at = COALESCE(?, replied_at), updated_at = ?
            WHERE candidate_id = ? AND touchpoint_id = ?
            """,
            (touchpoint_state, replied_at, _now(), candidate_id, touchpoint_id),
        )
        return self.get_touchpoint(candidate_id, touchpoint_id)

    def get_touchpoint(self, candidate_id: str, touchpoint_id: str) -> dict[str, object] | None:
        return _row_to_dict(
            self._conn.execute(
                "SELECT * FROM touchpoints WHERE candidate_id = ? AND touchpoint_id = ?",
                (candidate_id, touchpoint_id),
            ).fetchone()
        )

    def list_touchpoints(
        self,
        *,
        candidate_id: str,
        canonical_vacancy_id: str | None,
        application_id: str | None,
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM touchpoints WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if canonical_vacancy_id is not None:
            sql += " AND canonical_vacancy_id = ?"
            params.append(canonical_vacancy_id)
        if application_id is not None:
            sql += " AND application_id = ?"
            params.append(application_id)
        sql += " ORDER BY occurred_at DESC, created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def create_follow_up_reminder(
        self,
        *,
        candidate_id: str,
        touchpoint_id: str | None,
        canonical_vacancy_id: str,
        application_id: str | None,
        due_at: str,
        notes: str | None,
    ) -> dict[str, object]:
        reminder_id = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """
            INSERT INTO follow_up_reminders(
                reminder_id, candidate_id, touchpoint_id, canonical_vacancy_id, application_id,
                reminder_type, due_at, reminder_status, notes, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reminder_id,
                candidate_id,
                touchpoint_id,
                canonical_vacancy_id,
                application_id,
                "follow_up",
                due_at,
                "open",
                notes,
                now,
                now,
            ),
        )
        return _row_to_dict(
            self._conn.execute(
                "SELECT * FROM follow_up_reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()
        )

    def resolve_reminder(self, *, candidate_id: str, reminder_id: str) -> dict[str, object] | None:
        self._conn.execute(
            """
            UPDATE follow_up_reminders
            SET reminder_status = 'resolved', updated_at = ?
            WHERE candidate_id = ? AND reminder_id = ?
            """,
            (_now(), candidate_id, reminder_id),
        )
        return _row_to_dict(
            self._conn.execute(
                "SELECT * FROM follow_up_reminders WHERE candidate_id = ? AND reminder_id = ?",
                (candidate_id, reminder_id),
            ).fetchone()
        )
