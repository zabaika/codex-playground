from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_event(
        self,
        *,
        command_name: str,
        actor: str,
        entity_type: str,
        entity_id: str,
        previous_state: dict[str, object] | None,
        new_state: dict[str, object] | None,
        reason: str | None = None,
        source: str | None = None,
    ) -> str:
        audit_event_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO audit_events(
                audit_event_id, command_name, actor, entity_type, entity_id,
                previous_state_json, new_state_json, reason, source, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_event_id,
                command_name,
                actor,
                entity_type,
                entity_id,
                json.dumps(previous_state, ensure_ascii=False) if previous_state is not None else None,
                json.dumps(new_state, ensure_ascii=False) if new_state is not None else None,
                reason,
                source,
                _now(),
            ),
        )
        return audit_event_id
