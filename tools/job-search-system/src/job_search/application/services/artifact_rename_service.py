from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

from job_search.application.services.artifact_path_service import ArtifactPathService
from job_search.config import RuntimeSettings
from job_search.infrastructure.db.connection import load_connection, write_tx
from job_search.infrastructure.repositories.audit_repository import AuditRepository


@dataclass(frozen=True, slots=True)
class ArtifactRenameRequest:
    candidate_ids: tuple[str, ...] = ()
    apply: bool = False
    backup_dir: Path | None = None


class ArtifactRenameService:
    def __init__(self, *, runtime_settings: RuntimeSettings) -> None:
        self._runtime_settings = runtime_settings

    def rename(self, request: ArtifactRenameRequest) -> dict[str, object]:
        candidate_ids = tuple(dict.fromkeys(candidate_id.strip() for candidate_id in request.candidate_ids if candidate_id.strip()))
        conn = load_connection(self._runtime_settings.db_path, self._runtime_settings.sqlite_config_path)
        try:
            plans = self._build_plans(conn, candidate_ids)
            backup_path = None
            if request.apply and plans:
                backup_path = self._backup_database(request.backup_dir)
                audit = AuditRepository(conn)
                with write_tx(conn, immediate=True):
                    for plan in plans:
                        old_path = Path(str(plan["old_path"]))
                        new_path = Path(str(plan["new_path"]))
                        if new_path.exists() and old_path != new_path:
                            raise ValueError(f"Target artifact path already exists: {new_path}")
                        if old_path.exists():
                            new_path.parent.mkdir(parents=True, exist_ok=True)
                            old_path.rename(new_path)
                        conn.execute(
                            "UPDATE artifacts SET storage_path = ? WHERE artifact_id = ?",
                            (str(new_path), str(plan["artifact_id"])),
                        )
                        audit.record_event(
                            command_name="RenameArtifactFiles",
                            actor="system",
                            entity_type="artifact",
                            entity_id=str(plan["artifact_id"]),
                            previous_state={"storage_path": str(old_path)},
                            new_state={"storage_path": str(new_path)},
                            reason="operator_friendly_artifact_filename",
                            source="system_cli",
                        )
            return {
                "apply": request.apply,
                "backup_path": str(backup_path) if backup_path else None,
                "candidate_ids": list(candidate_ids),
                "rename_count": len(plans),
                "renames": plans,
            }
        finally:
            conn.close()

    def _build_plans(self, conn, candidate_ids: tuple[str, ...]) -> list[dict[str, object]]:
        sql = """
            SELECT a.artifact_id, a.artifact_type, a.candidate_id, a.storage_path, a.notes,
                   c.display_name, cp.full_name
            FROM artifacts a
            LEFT JOIN candidates c ON c.candidate_id = a.candidate_id
            LEFT JOIN candidate_profiles cp ON cp.candidate_id = a.candidate_id
        """
        params: list[str] = []
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            sql += f" WHERE a.candidate_id IN ({placeholders})"
            params.extend(candidate_ids)
        rows = conn.execute(sql, params).fetchall()
        plans: list[dict[str, object]] = []
        for row in rows:
            old_path = Path(str(row["storage_path"]))
            if not self._is_candidate_artifact_path(old_path):
                continue
            artifact_label = self._artifact_label(str(row["artifact_type"]), row["notes"])
            new_path = old_path.parent / ArtifactPathService.artifact_filename(
                artifact_id=str(row["artifact_id"]),
                artifact_type=str(row["artifact_type"]),
                artifact_label=artifact_label,
            )
            if old_path == new_path:
                continue
            plans.append(
                {
                    "artifact_id": str(row["artifact_id"]),
                    "artifact_type": str(row["artifact_type"]),
                    "old_path": str(old_path),
                    "new_path": str(new_path),
                    "old_exists": old_path.exists(),
                    "new_exists": new_path.exists(),
                }
            )
        return plans

    def _is_candidate_artifact_path(self, path: Path) -> bool:
        try:
            path.relative_to(self._runtime_settings.artifact_root / "candidates")
        except ValueError:
            return False
        return True

    def _artifact_label(self, artifact_type: str, raw_notes: object) -> str | None:
        notes = self._parse_notes(raw_notes)
        if artifact_type == "candidate_profile_draft":
            return "ai-profile-draft" if notes.get("source") == "ai_extraction" else "deterministic-profile-draft"
        for key in ("target_role", "primary_role"):
            if notes.get(key):
                if notes.get("language"):
                    return f"{notes[key]}-{notes['language']}"
                return str(notes[key])
        if notes.get("canonical_vacancy_id"):
            return f"vacancy-{notes['canonical_vacancy_id']}"
        return None

    def _parse_notes(self, raw_notes: object) -> dict[str, object]:
        if not raw_notes:
            return {}
        try:
            parsed = json.loads(str(raw_notes))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _backup_database(self, backup_dir: Path | None) -> Path:
        target_dir = backup_dir or Path("/private/tmp")
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = target_dir / f"job_search_before_artifact_rename_{timestamp}.sqlite"
        shutil.copy2(self._runtime_settings.db_path, backup_path)
        return backup_path
