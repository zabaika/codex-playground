from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid

from job_search.application.dto.candidate_profile_draft import CandidateProfileDraftDTO


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class CandidateDraftRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_draft(self, candidate_id: str, draft_artifact_id: str | None, draft: CandidateProfileDraftDTO) -> str:
        draft_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO candidate_profile_drafts(
                candidate_profile_draft_id, candidate_id, draft_artifact_id, source_set_id,
                draft_payload_json, field_conflicts_json, field_evidence_json, missing_fields_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                candidate_id,
                draft_artifact_id,
                draft.source_set_id,
                json.dumps(draft.draft_payload, ensure_ascii=False),
                json.dumps(draft.field_conflicts, ensure_ascii=False),
                json.dumps(draft.field_evidence, ensure_ascii=False),
                json.dumps(draft.missing_fields, ensure_ascii=False),
                _now(),
            ),
        )
        return draft_id

    def get_draft(self, draft_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT * FROM candidate_profile_drafts WHERE candidate_profile_draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            return None
        raw = _row_to_dict(row)
        raw["draft_payload"] = json.loads(str(raw.pop("draft_payload_json")))
        raw["field_conflicts"] = json.loads(str(raw.pop("field_conflicts_json")))
        raw["field_evidence"] = json.loads(str(raw.pop("field_evidence_json")))
        raw["missing_fields"] = json.loads(str(raw.pop("missing_fields_json")))
        return raw

    def get_latest_draft(self, candidate_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM candidate_profile_drafts
            WHERE candidate_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return self.get_draft(str(row["candidate_profile_draft_id"]))

    def create_snapshot(self, candidate_id: str, source_draft_id: str, snapshot_payload: dict[str, object]) -> str:
        snapshot_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO candidate_profile_snapshots(
                candidate_profile_snapshot_id, candidate_id, source_draft_id, snapshot_payload_json, created_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (snapshot_id, candidate_id, source_draft_id, json.dumps(snapshot_payload, ensure_ascii=False), _now()),
        )
        return snapshot_id
