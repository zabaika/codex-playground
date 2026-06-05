from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid

from job_search.domain.enums import VacancyWorkflowStage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class VacancyRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def import_occurrence(
        self,
        *,
        candidate_id: str,
        source_kind: str,
        normalized_item: dict[str, object],
    ) -> dict[str, object]:
        canonical = self._get_by_dedupe_key(candidate_id, str(normalized_item["dedupe_key"]))
        if canonical is None:
            canonical_vacancy_id = str(uuid.uuid4())
            now = _now()
            self._conn.execute(
                """
                INSERT INTO canonical_vacancies(
                    canonical_vacancy_id, candidate_id, company_name, role_title, location_text,
                    normalized_company_name, normalized_role_title, normalized_location_text, dedupe_key,
                    workflow_stage, processed, hidden, blacklisted, material_change_detected, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)
                """,
                (
                    canonical_vacancy_id,
                    candidate_id,
                    normalized_item["company_name"],
                    normalized_item["title"],
                    normalized_item["location_text"],
                    normalized_item["normalized_company_name"],
                    normalized_item["normalized_title"],
                    normalized_item["normalized_location_text"],
                    normalized_item["dedupe_key"],
                    VacancyWorkflowStage.NEW.value,
                    now,
                    now,
                ),
            )
            canonical = self.get_vacancy(candidate_id, canonical_vacancy_id)
        else:
            canonical_vacancy_id = str(canonical["canonical_vacancy_id"])
            self._touch_existing_vacancy(canonical_vacancy_id, normalized_item)
            canonical = self.get_vacancy(candidate_id, canonical_vacancy_id)

        existing_occurrence = self._conn.execute(
            """
            SELECT source_occurrence_id, content_hash
            FROM source_occurrences
            WHERE canonical_vacancy_id = ? AND source_kind = ? AND COALESCE(source_url, '') = COALESCE(?, '')
            ORDER BY imported_at DESC
            LIMIT 1
            """,
            (canonical_vacancy_id, source_kind, normalized_item.get("source_url")),
        ).fetchone()
        if existing_occurrence is not None and existing_occurrence["content_hash"] == normalized_item["content_hash"]:
            self._conn.execute(
                "UPDATE source_occurrences SET last_seen_at = ? WHERE source_occurrence_id = ?",
                (_now(), existing_occurrence["source_occurrence_id"]),
            )
            source_occurrence_id = str(existing_occurrence["source_occurrence_id"])
        else:
            source_occurrence_id = str(uuid.uuid4())
            now = _now()
            self._conn.execute(
                """
                INSERT INTO source_occurrences(
                    source_occurrence_id, candidate_id, canonical_vacancy_id, source_kind, source_url, external_vacancy_id,
                    source_title, source_company_name, source_location_text, content_hash, raw_payload_json,
                    imported_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_occurrence_id,
                    candidate_id,
                    canonical_vacancy_id,
                    source_kind,
                    normalized_item.get("source_url"),
                    normalized_item.get("external_vacancy_id"),
                    normalized_item["title"],
                    normalized_item["company_name"],
                    normalized_item["location_text"],
                    normalized_item["content_hash"],
                    json.dumps(normalized_item["raw_payload"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            if canonical and bool(canonical["processed"]):
                self._conn.execute(
                    """
                    UPDATE canonical_vacancies
                    SET material_change_detected = 1, updated_at = ?
                    WHERE canonical_vacancy_id = ?
                    """,
                    (_now(), canonical_vacancy_id),
                )

        return {
            "canonical_vacancy_id": canonical_vacancy_id,
            "source_occurrence_id": source_occurrence_id,
        }

    def list_vacancies(self, *, processed: bool | None, workflow_stage: str | None) -> list[dict[str, object]]:
        raise NotImplementedError

    def list_vacancies_for_candidate(
        self,
        *,
        candidate_id: str,
        processed: bool | None,
        workflow_stage: str | None,
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM canonical_vacancies WHERE candidate_id = ?"
        params: list[object] = [candidate_id]
        if processed is not None:
            sql += " AND processed = ?"
            params.append(int(processed))
        if workflow_stage is not None:
            sql += " AND workflow_stage = ?"
            params.append(workflow_stage)
        sql += " ORDER BY updated_at DESC, created_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_vacancy_ranking_inputs(
        self,
        *,
        candidate_id: str,
        processed: bool | None,
    ) -> list[dict[str, object]]:
        sql = """
        SELECT cv.*,
               so.raw_payload_json AS latest_raw_payload_json
        FROM canonical_vacancies cv
        LEFT JOIN source_occurrences so
          ON so.source_occurrence_id = (
              SELECT source_occurrence_id
              FROM source_occurrences
              WHERE canonical_vacancy_id = cv.canonical_vacancy_id
              ORDER BY imported_at DESC
              LIMIT 1
          )
        WHERE cv.candidate_id = ?
        """
        params: list[object] = [candidate_id]
        if processed is not None:
            sql += " AND cv.processed = ?"
            params.append(int(processed))
        sql += " ORDER BY cv.updated_at DESC"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            item = _row_to_dict(row)
            raw_payload = json.loads(str(item.pop("latest_raw_payload_json") or "{}"))
            item["latest_raw_text"] = raw_payload.get("raw_text") or ""
            results.append(item)
        return results

    def get_vacancy(self, candidate_id: str, canonical_vacancy_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT * FROM canonical_vacancies WHERE candidate_id = ? AND canonical_vacancy_id = ?",
            (candidate_id, canonical_vacancy_id),
        ).fetchone()
        return _row_to_dict(row)

    def mark_processed(self, candidate_id: str, canonical_vacancy_id: str) -> None:
        self._conn.execute(
            """
            UPDATE canonical_vacancies
            SET processed = 1, material_change_detected = 0, updated_at = ?
            WHERE candidate_id = ? AND canonical_vacancy_id = ?
            """,
            (_now(), candidate_id, canonical_vacancy_id),
        )

    def update_workflow_stage(self, candidate_id: str, canonical_vacancy_id: str, workflow_stage: str) -> None:
        current = self.get_vacancy(candidate_id, canonical_vacancy_id)
        if current is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {canonical_vacancy_id}")
        if bool(current["processed"]) and workflow_stage == VacancyWorkflowStage.NEW.value:
            raise ValueError("Processed vacancy cannot be moved back to new")
        self._conn.execute(
            "UPDATE canonical_vacancies SET workflow_stage = ?, updated_at = ? WHERE candidate_id = ? AND canonical_vacancy_id = ?",
            (workflow_stage, _now(), candidate_id, canonical_vacancy_id),
        )

    def get_or_create_application(self, candidate_id: str, canonical_vacancy_id: str) -> dict[str, object]:
        existing = self.get_application(candidate_id, canonical_vacancy_id)
        if existing is not None:
            return existing
        application_id = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """
            INSERT INTO applications(
                application_id, candidate_id, canonical_vacancy_id, application_state, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (application_id, candidate_id, canonical_vacancy_id, "drafted", now, now),
        )
        return _row_to_dict(
            self._conn.execute("SELECT * FROM applications WHERE application_id = ?", (application_id,)).fetchone()
        )

    def get_application(self, candidate_id: str, canonical_vacancy_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM applications
            WHERE candidate_id = ? AND canonical_vacancy_id = ?
            LIMIT 1
            """,
            (candidate_id, canonical_vacancy_id),
        ).fetchone()
        return _row_to_dict(row)

    def get_application_by_id(self, candidate_id: str, application_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM applications
            WHERE candidate_id = ? AND application_id = ?
            LIMIT 1
            """,
            (candidate_id, application_id),
        ).fetchone()
        return _row_to_dict(row)

    def update_application_state(self, *, candidate_id: str, application_id: str, application_state: str) -> dict[str, object]:
        self._conn.execute(
            """
            UPDATE applications
            SET application_state = ?, updated_at = ?
            WHERE candidate_id = ? AND application_id = ?
            """,
            (application_state, _now(), candidate_id, application_id),
        )
        return _row_to_dict(
            self._conn.execute(
                "SELECT * FROM applications WHERE candidate_id = ? AND application_id = ?",
                (candidate_id, application_id),
            ).fetchone()
        )

    def list_applications_for_candidate(self, candidate_id: str) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT a.*, cv.role_title, cv.company_name
            FROM applications a
            JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = a.canonical_vacancy_id
            WHERE a.candidate_id = ?
            ORDER BY a.updated_at DESC
            """,
            (candidate_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def attach_application_message_artifact(
        self,
        *,
        candidate_id: str,
        canonical_vacancy_id: str,
        message_artifact_id: str,
    ) -> dict[str, object]:
        application = self.get_or_create_application(candidate_id, canonical_vacancy_id)
        self._conn.execute(
            """
            UPDATE applications
            SET application_state = ?, message_artifact_id = ?, updated_at = ?
            WHERE application_id = ?
            """,
            ("drafted", message_artifact_id, _now(), application["application_id"]),
        )
        return _row_to_dict(
            self._conn.execute(
                "SELECT * FROM applications WHERE application_id = ?",
                (application["application_id"],),
            ).fetchone()
        )

    def list_daily_action_items(self, candidate_id: str) -> list[dict[str, object]]:
        actions: list[dict[str, object]] = []
        new_vacancies = self._conn.execute(
            """
            SELECT canonical_vacancy_id, role_title, company_name, updated_at
            FROM canonical_vacancies
            WHERE candidate_id = ? AND processed = 0 AND hidden = 0 AND blacklisted = 0 AND workflow_stage = ?
            ORDER BY updated_at DESC
            """,
            (candidate_id, VacancyWorkflowStage.NEW.value),
        ).fetchall()
        for row in new_vacancies:
            actions.append(
                {
                    "action_type": "review_new_vacancy",
                    "action_group": "vacancy_review",
                    "priority": 100,
                    "canonical_vacancy_id": row["canonical_vacancy_id"],
                    "label": f"{row['role_title']} — {row['company_name']}",
                    "updated_at": row["updated_at"],
                }
            )

        shortlisted = self._conn.execute(
            """
            SELECT canonical_vacancy_id, role_title, company_name, updated_at
            FROM canonical_vacancies
            WHERE candidate_id = ? AND processed = 0 AND hidden = 0 AND blacklisted = 0 AND workflow_stage = ?
            ORDER BY updated_at DESC
            """,
            (candidate_id, VacancyWorkflowStage.SHORTLISTED.value),
        ).fetchall()
        for row in shortlisted:
            actions.append(
                {
                    "action_type": "prepare_application",
                    "action_group": "application",
                    "priority": 90,
                    "canonical_vacancy_id": row["canonical_vacancy_id"],
                    "label": f"{row['role_title']} — {row['company_name']}",
                    "updated_at": row["updated_at"],
                }
            )

        changed = self._conn.execute(
            """
            SELECT canonical_vacancy_id, role_title, company_name, updated_at
            FROM canonical_vacancies
            WHERE candidate_id = ? AND processed = 1 AND material_change_detected = 1
            ORDER BY updated_at DESC
            """,
            (candidate_id,),
        ).fetchall()
        for row in changed:
            actions.append(
                {
                    "action_type": "review_material_change",
                    "action_group": "vacancy_review",
                    "priority": 95,
                    "canonical_vacancy_id": row["canonical_vacancy_id"],
                    "label": f"{row['role_title']} — {row['company_name']}",
                    "updated_at": row["updated_at"],
                }
            )

        application_drafts = self._conn.execute(
            """
            SELECT a.application_id, a.canonical_vacancy_id, cv.role_title, cv.company_name, a.updated_at
            FROM applications a
            JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = a.canonical_vacancy_id
            WHERE a.candidate_id = ? AND a.application_state = 'drafted' AND a.message_artifact_id IS NOT NULL
            ORDER BY a.updated_at DESC
            """,
            (candidate_id,),
        ).fetchall()
        for row in application_drafts:
            actions.append(
                {
                    "action_type": "review_application_draft",
                    "action_group": "application",
                    "priority": 85,
                    "application_id": row["application_id"],
                    "canonical_vacancy_id": row["canonical_vacancy_id"],
                    "label": f"{row['role_title']} — {row['company_name']}",
                    "updated_at": row["updated_at"],
                }
            )

        reminders = self._conn.execute(
            """
            SELECT r.reminder_id, r.touchpoint_id, r.canonical_vacancy_id, cv.role_title, cv.company_name, r.due_at
            FROM follow_up_reminders r
            LEFT JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = r.canonical_vacancy_id
            WHERE r.candidate_id = ? AND r.reminder_status = 'open'
            ORDER BY r.due_at ASC
            """,
            (candidate_id,),
        ).fetchall()
        for row in reminders:
            actions.append(
                {
                    "action_type": "follow_up_due",
                    "action_group": "follow_up",
                    "priority": 88,
                    "reminder_id": row["reminder_id"],
                    "touchpoint_id": row["touchpoint_id"],
                    "canonical_vacancy_id": row["canonical_vacancy_id"],
                    "label": f"{row['role_title']} — {row['company_name']}" if row["role_title"] else "Follow-up due",
                    "due_at": row["due_at"],
                    "updated_at": row["due_at"],
                }
            )

        interviews = self._conn.execute(
            """
            SELECT ir.interview_round_id, ir.application_id, ir.canonical_vacancy_id,
                   ir.round_type, ir.round_state, ir.scheduled_at, ir.updated_at,
                   cv.role_title, cv.company_name
            FROM interview_rounds ir
            JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = ir.canonical_vacancy_id
            WHERE ir.candidate_id = ? AND ir.round_state IN ('planned', 'scheduled')
            ORDER BY COALESCE(ir.scheduled_at, ir.updated_at) ASC
            """,
            (candidate_id,),
        ).fetchall()
        for row in interviews:
            actions.append(
                {
                    "action_type": "interview_round_due",
                    "action_group": "interview",
                    "priority": 92,
                    "interview_round_id": row["interview_round_id"],
                    "application_id": row["application_id"],
                    "canonical_vacancy_id": row["canonical_vacancy_id"],
                    "round_type": row["round_type"],
                    "round_state": row["round_state"],
                    "label": f"{row['round_type']} — {row['role_title']} — {row['company_name']}",
                    "due_at": row["scheduled_at"],
                    "updated_at": row["scheduled_at"] or row["updated_at"],
                }
            )

        actions.sort(key=lambda item: (int(item["priority"]), str(item.get("updated_at") or "")), reverse=True)
        return actions

    def _get_by_dedupe_key(self, candidate_id: str, dedupe_key: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT * FROM canonical_vacancies WHERE candidate_id = ? AND dedupe_key = ?",
            (candidate_id, dedupe_key),
        ).fetchone()
        return _row_to_dict(row)

    def _touch_existing_vacancy(self, canonical_vacancy_id: str, normalized_item: dict[str, object]) -> None:
        self._conn.execute(
            """
            UPDATE canonical_vacancies
            SET company_name = ?, role_title = ?, location_text = ?, updated_at = ?
            WHERE canonical_vacancy_id = ?
            """,
            (
                normalized_item["company_name"],
                normalized_item["title"],
                normalized_item["location_text"],
                _now(),
                canonical_vacancy_id,
            ),
        )
