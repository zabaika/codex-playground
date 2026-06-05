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


class InterviewRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_round(
        self,
        *,
        candidate_id: str,
        application_id: str,
        canonical_vacancy_id: str,
        round_type: str,
        round_state: str,
        scheduled_at: str | None,
        interviewer_name: str | None,
        notes: str | None,
        idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        existing = self._conn.execute(
            """
            SELECT *
            FROM interview_rounds
            WHERE candidate_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (candidate_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _row_to_dict(existing), True

        interview_round_id = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """
            INSERT INTO interview_rounds(
                interview_round_id, candidate_id, application_id, canonical_vacancy_id,
                round_type, round_state, scheduled_at, completed_at, interviewer_name,
                notes, idempotency_key, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                interview_round_id,
                candidate_id,
                application_id,
                canonical_vacancy_id,
                round_type,
                round_state,
                scheduled_at,
                interviewer_name,
                notes,
                idempotency_key,
                now,
                now,
            ),
        )
        return self.get_round(candidate_id=candidate_id, interview_round_id=interview_round_id), False

    def update_round_state(
        self,
        *,
        candidate_id: str,
        interview_round_id: str,
        round_state: str,
        completed_at: str | None,
        notes: str | None,
    ) -> dict[str, object] | None:
        self._conn.execute(
            """
            UPDATE interview_rounds
            SET round_state = ?,
                completed_at = COALESCE(?, completed_at),
                notes = COALESCE(?, notes),
                updated_at = ?
            WHERE candidate_id = ? AND interview_round_id = ?
            """,
            (round_state, completed_at, notes, _now(), candidate_id, interview_round_id),
        )
        return self.get_round(candidate_id=candidate_id, interview_round_id=interview_round_id)

    def get_round(self, *, candidate_id: str, interview_round_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT ir.*, cv.role_title, cv.company_name
            FROM interview_rounds ir
            JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = ir.canonical_vacancy_id
            WHERE ir.candidate_id = ? AND ir.interview_round_id = ?
            LIMIT 1
            """,
            (candidate_id, interview_round_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_rounds(
        self,
        *,
        candidate_id: str,
        application_id: str | None = None,
        canonical_vacancy_id: str | None = None,
    ) -> list[dict[str, object]]:
        sql = """
            SELECT ir.*, cv.role_title, cv.company_name
            FROM interview_rounds ir
            JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = ir.canonical_vacancy_id
            WHERE ir.candidate_id = ?
        """
        params: list[object] = [candidate_id]
        if application_id:
            sql += " AND ir.application_id = ?"
            params.append(application_id)
        if canonical_vacancy_id:
            sql += " AND ir.canonical_vacancy_id = ?"
            params.append(canonical_vacancy_id)
        sql += " ORDER BY COALESCE(ir.scheduled_at, ir.updated_at) ASC, ir.created_at ASC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]
