from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactUsageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_usage(
        self,
        *,
        artifact_id: str,
        candidate_id: str | None,
        usage_type: str,
        target_entity_type: str | None,
        target_entity_id: str | None,
        external_target: str | None = None,
        notes: str | None = None,
    ) -> str:
        existing = self._conn.execute(
            """
            SELECT artifact_usage_event_id
            FROM artifact_usage_events
            WHERE artifact_id = ?
              AND COALESCE(candidate_id, '') = COALESCE(?, '')
              AND usage_type = ?
              AND COALESCE(target_entity_type, '') = COALESCE(?, '')
              AND COALESCE(target_entity_id, '') = COALESCE(?, '')
              AND COALESCE(external_target, '') = COALESCE(?, '')
            ORDER BY occurred_at DESC
            LIMIT 1
            """,
            (artifact_id, candidate_id, usage_type, target_entity_type, target_entity_id, external_target),
        ).fetchone()
        if existing is not None:
            return str(existing["artifact_usage_event_id"])

        artifact_usage_event_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO artifact_usage_events(
                artifact_usage_event_id, artifact_id, candidate_id, usage_type,
                target_entity_type, target_entity_id, external_target, occurred_at, notes
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_usage_event_id,
                artifact_id,
                candidate_id,
                usage_type,
                target_entity_type,
                target_entity_id,
                external_target,
                _now(),
                notes,
            ),
        )
        return artifact_usage_event_id
