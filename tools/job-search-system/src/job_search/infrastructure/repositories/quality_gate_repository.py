from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QualityGateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_run(
        self,
        *,
        gate_name: str,
        subject_type: str,
        subject_id: str,
        candidate_id: str | None,
        status: str,
        issues: list[dict[str, object]],
    ) -> str:
        quality_gate_run_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO quality_gate_runs(
                quality_gate_run_id, gate_name, subject_type, subject_id,
                candidate_id, status, issues_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quality_gate_run_id,
                gate_name,
                subject_type,
                subject_id,
                candidate_id,
                status,
                json.dumps(issues, ensure_ascii=False),
                _now(),
            ),
        )
        return quality_gate_run_id
