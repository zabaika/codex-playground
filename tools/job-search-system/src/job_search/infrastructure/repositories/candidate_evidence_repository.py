from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CandidateEvidenceRepository:
    _EMPTY_GROUP_DELETE_SQL = {
        "candidate_experience_entries": "DELETE FROM candidate_experience_entries WHERE candidate_id = ?",
        "candidate_achievement_evidence": "DELETE FROM candidate_achievement_evidence WHERE candidate_id = ?",
        "candidate_education_entries": "DELETE FROM candidate_education_entries WHERE candidate_id = ?",
        "candidate_skill_signals": "DELETE FROM candidate_skill_signals WHERE candidate_id = ?",
        "candidate_recommendations": "DELETE FROM candidate_recommendations WHERE candidate_id = ?",
        "candidate_certifications": "DELETE FROM candidate_certifications WHERE candidate_id = ?",
        "candidate_publications": "DELETE FROM candidate_publications WHERE candidate_id = ?",
        "candidate_awards": "DELETE FROM candidate_awards WHERE candidate_id = ?",
    }

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_experience_entries(self, candidate_id: str, entries: list[dict[str, object]]) -> dict[str, str]:
        self._conn.execute("DELETE FROM candidate_experience_entries WHERE candidate_id = ?", (candidate_id,))
        created_map: dict[str, str] = {}
        for entry in entries:
            experience_entry_id = str(uuid.uuid4())
            temp_key = str(entry.get("_temp_experience_key") or experience_entry_id)
            created_map[temp_key] = experience_entry_id
            self._conn.execute(
                """
                INSERT INTO candidate_experience_entries(
                    experience_entry_id, candidate_id, company_name, role_title, start_date, end_date,
                    is_current, location, company_context_text, company_industry, org_scale_hint,
                    domain_context_json, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experience_entry_id,
                    candidate_id,
                    entry.get("company_name"),
                    entry.get("role_title"),
                    entry.get("start_date"),
                    entry.get("end_date"),
                    int(bool(entry.get("is_current", False))),
                    entry.get("location"),
                    entry.get("company_context_text"),
                    entry.get("company_industry"),
                    entry.get("org_scale_hint"),
                    json.dumps(entry.get("domain_context_json", []), ensure_ascii=False),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )
        return created_map

    def replace_achievement_evidence(
        self,
        candidate_id: str,
        entries: list[dict[str, object]],
        experience_map: dict[str, str],
    ) -> None:
        self._conn.execute("DELETE FROM candidate_achievement_evidence WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_achievement_evidence(
                    achievement_evidence_id, candidate_id, experience_entry_id, source_artifact_id,
                    achievement_text, metric_name, metric_value, metric_unit, metric_period, confidence_status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    experience_map.get(str(entry.get("experience_ref"))),
                    entry.get("source_artifact_id"),
                    entry.get("achievement_text"),
                    entry.get("metric_name"),
                    entry.get("metric_value"),
                    entry.get("metric_unit"),
                    entry.get("metric_period"),
                    entry.get("confidence_status"),
                    _now(),
                ),
            )

    def replace_education_entries(self, candidate_id: str, entries: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_education_entries WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_education_entries(
                    education_entry_id, candidate_id, institution_name, degree, faculty, specialization,
                    start_year, end_year, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    entry.get("institution_name"),
                    entry.get("degree"),
                    entry.get("faculty"),
                    entry.get("specialization"),
                    entry.get("start_year"),
                    entry.get("end_year"),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )

    def replace_skill_signals(self, candidate_id: str, entries: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_skill_signals WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_skill_signals(
                    skill_signal_id, candidate_id, skill_name, skill_group, context, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    entry.get("skill_name"),
                    entry.get("skill_group"),
                    entry.get("context"),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )

    def replace_recommendations(self, candidate_id: str, entries: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_recommendations WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_recommendations(
                    recommendation_id, candidate_id, recommender_name, recommender_role,
                    recommender_company, contact_hint, recommendation_text, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    entry.get("recommender_name"),
                    entry.get("recommender_role"),
                    entry.get("recommender_company"),
                    entry.get("contact_hint"),
                    entry.get("recommendation_text"),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )

    def replace_certifications(self, candidate_id: str, entries: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_certifications WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_certifications(
                    certification_id, candidate_id, certification_name, issuer,
                    issued_at, expires_at, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    entry.get("certification_name"),
                    entry.get("issuer"),
                    entry.get("issued_at"),
                    entry.get("expires_at"),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )

    def replace_publications(self, candidate_id: str, entries: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_publications WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_publications(
                    publication_id, candidate_id, title, publication_type,
                    publication_url, published_at, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    entry.get("title"),
                    entry.get("publication_type"),
                    entry.get("publication_url"),
                    entry.get("published_at"),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )

    def replace_awards(self, candidate_id: str, entries: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_awards WHERE candidate_id = ?", (candidate_id,))
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO candidate_awards(
                    award_id, candidate_id, award_name, awarder,
                    awarded_at, source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    entry.get("award_name"),
                    entry.get("awarder"),
                    entry.get("awarded_at"),
                    entry.get("source_artifact_id"),
                    _now(),
                ),
            )

    def replace_empty_group(self, table_name: str, candidate_id: str) -> None:
        sql = self._EMPTY_GROUP_DELETE_SQL.get(table_name)
        if sql is None:
            raise ValueError(f"Unsupported evidence table: {table_name}")
        self._conn.execute(sql, (candidate_id,))

    def get_resume_evidence(self, candidate_id: str) -> dict[str, list[dict[str, object]]]:
        return {
            "experience_entries": self._list_rows(
                """
                SELECT * FROM candidate_experience_entries
                WHERE candidate_id = ?
                ORDER BY COALESCE(start_date, ''), created_at
                """,
                candidate_id,
            ),
            "achievement_evidence": self._list_rows(
                """
                SELECT * FROM candidate_achievement_evidence
                WHERE candidate_id = ?
                ORDER BY created_at
                """,
                candidate_id,
            ),
            "education_entries": self._list_rows(
                """
                SELECT * FROM candidate_education_entries
                WHERE candidate_id = ?
                ORDER BY COALESCE(end_year, 0), created_at
                """,
                candidate_id,
            ),
            "skill_signals": self._list_rows(
                """
                SELECT * FROM candidate_skill_signals
                WHERE candidate_id = ?
                ORDER BY created_at
                """,
                candidate_id,
            ),
            "recommendations": self._list_rows(
                """
                SELECT * FROM candidate_recommendations
                WHERE candidate_id = ?
                ORDER BY created_at
                """,
                candidate_id,
            ),
            "certifications": self._list_rows(
                """
                SELECT * FROM candidate_certifications
                WHERE candidate_id = ?
                ORDER BY created_at
                """,
                candidate_id,
            ),
            "publications": self._list_rows(
                """
                SELECT * FROM candidate_publications
                WHERE candidate_id = ?
                ORDER BY created_at
                """,
                candidate_id,
            ),
            "awards": self._list_rows(
                """
                SELECT * FROM candidate_awards
                WHERE candidate_id = ?
                ORDER BY created_at
                """,
                candidate_id,
            ),
        }

    def _list_rows(self, sql: str, candidate_id: str) -> list[dict[str, object]]:
        rows = self._conn.execute(sql, (candidate_id,)).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]
