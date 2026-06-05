from __future__ import annotations

import json
import sqlite3


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


class ObservabilityRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def summary(self, *, candidate_id: str | None = None, limit: int = 20) -> dict[str, object]:
        bounded_limit = max(1, min(limit, 100))
        return {
            "counts": self._counts(candidate_id=candidate_id),
            "quality_gate_counts": self._quality_gate_counts(candidate_id=candidate_id),
            "recent_audit_events": self._recent_audit_events(candidate_id=candidate_id, limit=bounded_limit),
            "recent_quality_gate_issues": self._recent_quality_gate_issues(candidate_id=candidate_id, limit=bounded_limit),
            "recent_artifact_usage_events": self._recent_artifact_usage_events(candidate_id=candidate_id, limit=bounded_limit),
            "recent_board_action_idempotency_keys": self._recent_board_action_idempotency_keys(
                candidate_id=candidate_id,
                limit=bounded_limit,
            ),
        }

    def strategy_report(self, *, candidate_id: str | None = None, limit: int = 20) -> dict[str, object]:
        bounded_limit = max(1, min(limit, 100))
        return {
            "summary": self._strategy_summary(candidate_id=candidate_id),
            "funnel": self._funnel(candidate_id=candidate_id),
            "by_role": self._conversion_by_vacancy_field(
                candidate_id=candidate_id,
                field="role_title",
                limit=bounded_limit,
            ),
            "by_company": self._conversion_by_vacancy_field(
                candidate_id=candidate_id,
                field="company_name",
                limit=bounded_limit,
            ),
            "by_source_kind": self._source_kind_breakdown(candidate_id=candidate_id, limit=bounded_limit),
            "follow_up": self._follow_up_metrics(candidate_id=candidate_id),
            "quality": self._quality_metrics(candidate_id=candidate_id, limit=bounded_limit),
            "board_actions": self._board_action_metrics(candidate_id=candidate_id),
            "resume_effectiveness": self._resume_effectiveness(candidate_id=candidate_id, limit=bounded_limit),
            "position_effectiveness": self._position_effectiveness(candidate_id=candidate_id, limit=bounded_limit),
            "limitations": [
                "Metrics are deterministic projections over stored local records.",
                "Resume effectiveness is attributed only when application_resume_attached usage exists.",
                "Position effectiveness uses resume target_role when present, otherwise vacancy role_title.",
                "No AI interpretation, external board scraping, or unrecorded manual outcomes are included.",
            ],
        }

    def _counts(self, *, candidate_id: str | None) -> dict[str, int]:
        return {
            "audit_events": self._count("audit_events", candidate_id=candidate_id),
            "quality_gate_runs": self._count("quality_gate_runs", candidate_id=candidate_id),
            "artifact_usage_events": self._count("artifact_usage_events", candidate_id=candidate_id),
            "manual_board_actions": self._count("manual_board_actions", candidate_id=candidate_id),
        }

    def _count(self, table: str, *, candidate_id: str | None) -> int:
        if candidate_id is None:
            sql_map = {
                "audit_events": "SELECT COUNT(*) FROM audit_events",
                "quality_gate_runs": "SELECT COUNT(*) FROM quality_gate_runs",
                "artifact_usage_events": "SELECT COUNT(*) FROM artifact_usage_events",
                "manual_board_actions": "SELECT COUNT(*) FROM manual_board_actions",
            }
            return int(self._conn.execute(sql_map[table]).fetchone()[0])
        if table == "audit_events":
            sql = """
                SELECT COUNT(*)
                FROM audit_events
                WHERE previous_state_json LIKE ? OR new_state_json LIKE ? OR entity_id = ?
            """
            needle = f"%{candidate_id}%"
            return int(self._conn.execute(sql, (needle, needle, candidate_id)).fetchone()[0])
        scoped_sql_map = {
            "quality_gate_runs": "SELECT COUNT(*) FROM quality_gate_runs WHERE candidate_id = ?",
            "artifact_usage_events": "SELECT COUNT(*) FROM artifact_usage_events WHERE candidate_id = ?",
            "manual_board_actions": "SELECT COUNT(*) FROM manual_board_actions WHERE candidate_id = ?",
        }
        return int(self._conn.execute(scoped_sql_map[table], (candidate_id,)).fetchone()[0])

    def _strategy_summary(self, *, candidate_id: str | None) -> dict[str, int]:
        return {
            "vacancies": self._table_count("canonical_vacancies", candidate_id=candidate_id),
            "applications": self._table_count("applications", candidate_id=candidate_id),
            "submitted_actions": self._manual_action_count(
                candidate_id=candidate_id,
                action_type="application_submitted",
                action_state="completed",
            ),
            "touchpoint_responses": self._touchpoint_response_count(candidate_id=candidate_id),
            "interview_rounds": self._table_count("interview_rounds", candidate_id=candidate_id),
            "completed_interview_rounds": self._interview_count(candidate_id=candidate_id, round_state="completed"),
        }

    def _funnel(self, *, candidate_id: str | None) -> dict[str, int]:
        vacancy_stage_counts = self._group_count(
            table="canonical_vacancies",
            group_field="workflow_stage",
            candidate_id=candidate_id,
        )
        application_state_counts = self._group_count(
            table="applications",
            group_field="application_state",
            candidate_id=candidate_id,
        )
        return {
            "vacancy_total": self._table_count("canonical_vacancies", candidate_id=candidate_id),
            "vacancy_new": vacancy_stage_counts.get("new", 0),
            "vacancy_shortlisted": vacancy_stage_counts.get("shortlisted", 0),
            "vacancy_rejected": vacancy_stage_counts.get("rejected", 0),
            "vacancy_closed": vacancy_stage_counts.get("closed", 0),
            "applications_total": self._table_count("applications", candidate_id=candidate_id),
            "applications_drafted": application_state_counts.get("drafted", 0),
            "applications_interviewing": application_state_counts.get("interviewing", 0),
            "applications_submitted_state": application_state_counts.get("submitted", 0),
            "manual_submitted_actions": self._manual_action_count(
                candidate_id=candidate_id,
                action_type="application_submitted",
                action_state="completed",
            ),
            "touchpoint_responses": self._touchpoint_response_count(candidate_id=candidate_id),
            "interview_rounds": self._table_count("interview_rounds", candidate_id=candidate_id),
            "completed_interview_rounds": self._interview_count(candidate_id=candidate_id, round_state="completed"),
        }

    def _conversion_by_vacancy_field(
        self,
        *,
        candidate_id: str | None,
        field: str,
        limit: int,
    ) -> list[dict[str, object]]:
        label_columns = {"role_title": "cv.role_title", "company_name": "cv.company_name"}
        if field not in label_columns:
            raise ValueError("Unsupported vacancy breakdown field")
        label_column = label_columns[field]
        sql = "SELECT __LABEL_COLUMN__ AS label, COUNT(DISTINCT cv.canonical_vacancy_id) AS vacancies, COUNT(DISTINCT a.application_id) AS applications, COUNT(DISTINCT CASE WHEN mba.action_type = 'application_submitted' AND mba.action_state = 'completed' THEN mba.board_action_id END) AS submitted_actions, COUNT(DISTINCT CASE WHEN t.direction = 'incoming' OR t.touchpoint_state IN ('received', 'replied') THEN t.touchpoint_id END) AS touchpoint_responses, COUNT(DISTINCT ir.interview_round_id) AS interview_rounds, COUNT(DISTINCT CASE WHEN ir.round_state = 'completed' THEN ir.interview_round_id END) AS completed_interview_rounds FROM canonical_vacancies cv LEFT JOIN applications a ON a.canonical_vacancy_id = cv.canonical_vacancy_id LEFT JOIN manual_board_actions mba ON mba.canonical_vacancy_id = cv.canonical_vacancy_id LEFT JOIN touchpoints t ON t.canonical_vacancy_id = cv.canonical_vacancy_id LEFT JOIN interview_rounds ir ON ir.canonical_vacancy_id = cv.canonical_vacancy_id".replace("__LABEL_COLUMN__", label_column)  # nosec B608
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE cv.candidate_id = ?"
            params.append(candidate_id)
        sql += " GROUP BY __LABEL_COLUMN__ ORDER BY submitted_actions DESC, completed_interview_rounds DESC, applications DESC, vacancies DESC LIMIT ?".replace(
            "__LABEL_COLUMN__", label_column
        )
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._conversion_row(row) for row in rows]

    def _source_kind_breakdown(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        sql = """
            SELECT so.source_kind,
                   COUNT(DISTINCT so.canonical_vacancy_id) AS vacancies,
                   COUNT(DISTINCT a.application_id) AS applications,
                   COUNT(DISTINCT CASE
                       WHEN mba.action_type = 'application_submitted' AND mba.action_state = 'completed'
                       THEN mba.board_action_id END
                   ) AS submitted_actions,
                   COUNT(DISTINCT ir.interview_round_id) AS interview_rounds
            FROM source_occurrences so
            LEFT JOIN applications a ON a.canonical_vacancy_id = so.canonical_vacancy_id
            LEFT JOIN manual_board_actions mba ON mba.canonical_vacancy_id = so.canonical_vacancy_id
            LEFT JOIN interview_rounds ir ON ir.canonical_vacancy_id = so.canonical_vacancy_id
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE so.candidate_id = ?"
            params.append(candidate_id)
        sql += " GROUP BY so.source_kind ORDER BY submitted_actions DESC, applications DESC, vacancies DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _follow_up_metrics(self, *, candidate_id: str | None) -> dict[str, int]:
        return {
            "open_reminders": self._reminder_count(candidate_id=candidate_id, reminder_status="open"),
            "resolved_reminders": self._reminder_count(candidate_id=candidate_id, reminder_status="resolved"),
            "touchpoints": self._table_count("touchpoints", candidate_id=candidate_id),
            "touchpoint_responses": self._touchpoint_response_count(candidate_id=candidate_id),
        }

    def _quality_metrics(self, *, candidate_id: str | None, limit: int) -> dict[str, object]:
        return {
            "counts": self._quality_gate_counts(candidate_id=candidate_id),
            "by_gate": self._quality_by_gate(candidate_id=candidate_id),
            "recent_issues": self._recent_quality_gate_issues(candidate_id=candidate_id, limit=limit),
        }

    def _board_action_metrics(self, *, candidate_id: str | None) -> dict[str, object]:
        return {
            "counts_by_action": self._group_count(
                table="manual_board_actions",
                group_field="action_type",
                candidate_id=candidate_id,
            ),
            "completed_submissions": self._manual_action_count(
                candidate_id=candidate_id,
                action_type="application_submitted",
                action_state="completed",
            ),
        }

    def _resume_effectiveness(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        sql = """
            SELECT aue.artifact_id AS resume_artifact_id,
                   resume.artifact_type AS resume_artifact_type,
                   resume.storage_path AS resume_storage_path,
                   resume.notes AS resume_notes,
                   final.artifact_id AS final_resume_artifact_id,
                   final.artifact_type AS final_resume_artifact_type,
                   final.storage_path AS final_resume_storage_path,
                   COUNT(DISTINCT app.application_id) AS applications,
                   COUNT(DISTINCT CASE
                       WHEN mba.action_type = 'application_submitted' AND mba.action_state = 'completed'
                       THEN mba.board_action_id END
                   ) AS submitted_actions,
                   COUNT(DISTINCT CASE
                       WHEN t.direction = 'incoming' OR t.touchpoint_state IN ('received', 'replied')
                       THEN t.touchpoint_id END
                   ) AS touchpoint_responses,
                   COUNT(DISTINCT ir.interview_round_id) AS interview_rounds,
                   COUNT(DISTINCT CASE
                       WHEN ir.round_state = 'completed' THEN ir.interview_round_id END
                   ) AS completed_interview_rounds
            FROM artifact_usage_events aue
            JOIN artifacts resume ON resume.artifact_id = aue.artifact_id
            JOIN applications app ON app.application_id = aue.target_entity_id
            JOIN canonical_vacancies cv ON cv.canonical_vacancy_id = app.canonical_vacancy_id
            LEFT JOIN artifacts final ON final.derived_from_artifact_id = resume.artifact_id
                AND final.artifact_type IN ('resume_markdown_final', 'resume_vacancy_final')
            LEFT JOIN manual_board_actions mba ON mba.application_id = app.application_id
            LEFT JOIN touchpoints t ON t.application_id = app.application_id
            LEFT JOIN interview_rounds ir ON ir.application_id = app.application_id
            WHERE aue.usage_type = 'application_resume_attached'
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " AND aue.candidate_id = ?"
            params.append(candidate_id)
        sql += """
            GROUP BY aue.artifact_id, resume.artifact_type, resume.storage_path, resume.notes,
                     final.artifact_id, final.artifact_type, final.storage_path
            ORDER BY submitted_actions DESC, completed_interview_rounds DESC, touchpoint_responses DESC, applications DESC
            LIMIT ?
        """
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = _row_to_dict(row)
            notes = self._json_object(item.pop("resume_notes", None))
            item["target_role"] = notes.get("target_role")
            item["language"] = notes.get("language")
            item["effective_resume_artifact_id"] = item.get("final_resume_artifact_id") or item["resume_artifact_id"]
            item["effective_resume_artifact_type"] = item.get("final_resume_artifact_type") or item["resume_artifact_type"]
            item["effective_resume_storage_path"] = item.get("final_resume_storage_path") or item["resume_storage_path"]
            result.append(item)
        return result

    def _position_effectiveness(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        resume_items = self._resume_effectiveness(candidate_id=candidate_id, limit=100)
        by_position: dict[str, dict[str, object]] = {}
        for item in resume_items:
            label = str(item.get("target_role") or "unknown")
            current = by_position.setdefault(
                label,
                {
                    "position": label,
                    "applications": 0,
                    "submitted_actions": 0,
                    "touchpoint_responses": 0,
                    "interview_rounds": 0,
                    "completed_interview_rounds": 0,
                    "resume_artifacts": 0,
                },
            )
            current["applications"] = int(current["applications"]) + int(item["applications"])
            current["submitted_actions"] = int(current["submitted_actions"]) + int(item["submitted_actions"])
            current["touchpoint_responses"] = int(current["touchpoint_responses"]) + int(item["touchpoint_responses"])
            current["interview_rounds"] = int(current["interview_rounds"]) + int(item["interview_rounds"])
            current["completed_interview_rounds"] = int(current["completed_interview_rounds"]) + int(item["completed_interview_rounds"])
            current["resume_artifacts"] = int(current["resume_artifacts"]) + 1
        return sorted(
            by_position.values(),
            key=lambda item: (
                int(item["submitted_actions"]),
                int(item["completed_interview_rounds"]),
                int(item["touchpoint_responses"]),
                int(item["applications"]),
            ),
            reverse=True,
        )[:limit]

    def _table_count(self, table: str, *, candidate_id: str | None) -> int:
        # table is supplied only by internal fixed call sites.
        sql = f"SELECT COUNT(*) FROM {table}"  # nosec B608
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE candidate_id = ?"
            params.append(candidate_id)
        return int(self._conn.execute(sql, tuple(params)).fetchone()[0])

    def _group_count(self, *, table: str, group_field: str, candidate_id: str | None) -> dict[str, int]:
        allowed = {
            "canonical_vacancies": {"workflow_stage"},
            "applications": {"application_state"},
            "manual_board_actions": {"action_type"},
        }
        if group_field not in allowed.get(table, set()):
            raise ValueError("Unsupported group count field")
        # group_field/table pair is allowlisted above.
        sql = f"SELECT {group_field} AS label, COUNT(*) AS count FROM {table}"  # nosec B608
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE candidate_id = ?"
            params.append(candidate_id)
        sql += f" GROUP BY {group_field}"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return {str(row["label"]): int(row["count"]) for row in rows}

    def _manual_action_count(self, *, candidate_id: str | None, action_type: str, action_state: str) -> int:
        sql = "SELECT COUNT(*) FROM manual_board_actions WHERE action_type = ? AND action_state = ?"
        params: list[object] = [action_type, action_state]
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        return int(self._conn.execute(sql, tuple(params)).fetchone()[0])

    def _touchpoint_response_count(self, *, candidate_id: str | None) -> int:
        sql = "SELECT COUNT(*) FROM touchpoints WHERE (direction = 'incoming' OR touchpoint_state IN ('received', 'replied'))"
        params: list[object] = []
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        return int(self._conn.execute(sql, tuple(params)).fetchone()[0])

    def _interview_count(self, *, candidate_id: str | None, round_state: str) -> int:
        sql = "SELECT COUNT(*) FROM interview_rounds WHERE round_state = ?"
        params: list[object] = [round_state]
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        return int(self._conn.execute(sql, tuple(params)).fetchone()[0])

    def _reminder_count(self, *, candidate_id: str | None, reminder_status: str) -> int:
        sql = "SELECT COUNT(*) FROM follow_up_reminders WHERE reminder_status = ?"
        params: list[object] = [reminder_status]
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        return int(self._conn.execute(sql, tuple(params)).fetchone()[0])

    def _quality_by_gate(self, *, candidate_id: str | None) -> list[dict[str, object]]:
        sql = """
            SELECT gate_name, status, COUNT(*) AS count
            FROM quality_gate_runs
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE candidate_id = ?"
            params.append(candidate_id)
        sql += " GROUP BY gate_name, status ORDER BY gate_name, status"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _conversion_row(self, row: sqlite3.Row) -> dict[str, object]:
        item = _row_to_dict(row)
        applications = int(item["applications"])
        submitted = int(item["submitted_actions"])
        interviews = int(item["interview_rounds"])
        item["submit_rate"] = round(submitted / applications, 3) if applications else 0
        item["interview_rate"] = round(interviews / applications, 3) if applications else 0
        return item

    def _json_object(self, value: object) -> dict[str, object]:
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _quality_gate_counts(self, *, candidate_id: str | None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS count FROM quality_gate_runs"
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE candidate_id = ?"
            params.append(candidate_id)
        sql += " GROUP BY status"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _recent_audit_events(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        sql = """
            SELECT audit_event_id, command_name, actor, entity_type, entity_id, reason, source, created_at
            FROM audit_events
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE previous_state_json LIKE ? OR new_state_json LIKE ? OR entity_id = ?"
            needle = f"%{candidate_id}%"
            params.extend([needle, needle, candidate_id])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _recent_quality_gate_issues(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        sql = """
            SELECT quality_gate_run_id, gate_name, subject_type, subject_id, candidate_id, status, issues_json, created_at
            FROM quality_gate_runs
            WHERE status IN ('warn', 'fail')
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._quality_gate_row(row) for row in rows]

    def _recent_artifact_usage_events(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        sql = """
            SELECT artifact_usage_event_id, artifact_id, candidate_id, usage_type, target_entity_type,
                   target_entity_id, external_target, occurred_at, notes
            FROM artifact_usage_events
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE candidate_id = ?"
            params.append(candidate_id)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _recent_board_action_idempotency_keys(self, *, candidate_id: str | None, limit: int) -> list[dict[str, object]]:
        sql = """
            SELECT candidate_id, platform, action_type, action_state, canonical_vacancy_id,
                   application_id, artifact_id, external_target, idempotency_key, COUNT(*) AS stored_count,
                   MAX(updated_at) AS last_seen_at
            FROM manual_board_actions
        """
        params: list[object] = []
        if candidate_id is not None:
            sql += " WHERE candidate_id = ?"
            params.append(candidate_id)
        sql += " GROUP BY candidate_id, idempotency_key ORDER BY last_seen_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _quality_gate_row(self, row: sqlite3.Row) -> dict[str, object]:
        item = _row_to_dict(row)
        item["issues"] = json.loads(str(item.pop("issues_json") or "[]"))
        return item
