from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3

from job_search.config import RuntimeSettings
from job_search.infrastructure.db.connection import load_connection, write_tx


@dataclass(frozen=True, slots=True)
class ArtifactCleanupRequest:
    keep_candidate_ids: tuple[str, ...]
    keep_artifact_folders: tuple[str, ...] = ()
    apply: bool = False
    backup_dir: Path | None = None


class ArtifactCleanupService:
    _CANDIDATE_ENTITY_TABLES = {
        "applications": "application_id",
        "artifact_usage_events": "artifact_usage_event_id",
        "artifacts": "artifact_id",
        "candidate_achievement_evidence": "achievement_evidence_id",
        "candidate_awards": "award_id",
        "candidate_certifications": "certification_id",
        "candidate_education_entries": "education_entry_id",
        "candidate_experience_entries": "experience_entry_id",
        "candidate_external_profiles": "external_profile_id",
        "candidate_language_proficiencies": "language_proficiency_id",
        "candidate_profile_drafts": "candidate_profile_draft_id",
        "candidate_profile_snapshots": "candidate_profile_snapshot_id",
        "candidate_publications": "publication_id",
        "candidate_recommendations": "recommendation_id",
        "candidate_skill_signals": "skill_signal_id",
        "candidate_sources": "candidate_source_id",
        "candidate_work_authorizations": "work_authorization_id",
        "canonical_vacancies": "canonical_vacancy_id",
        "follow_up_reminders": "reminder_id",
        "manual_board_actions": "board_action_id",
        "quality_gate_runs": "quality_gate_run_id",
        "source_occurrences": "source_occurrence_id",
        "touchpoints": "touchpoint_id",
    }
    _COUNT_SQL = {
        "candidates": "SELECT COUNT(*) FROM candidates",
        "artifacts": "SELECT COUNT(*) FROM artifacts",
        "canonical_vacancies": "SELECT COUNT(*) FROM canonical_vacancies",
        "audit_events": "SELECT COUNT(*) FROM audit_events",
        "quality_gate_runs": "SELECT COUNT(*) FROM quality_gate_runs",
        "manual_board_actions": "SELECT COUNT(*) FROM manual_board_actions",
    }

    def __init__(self, *, runtime_settings: RuntimeSettings) -> None:
        self._runtime_settings = runtime_settings

    def cleanup(self, request: ArtifactCleanupRequest) -> dict[str, object]:
        keep_candidate_ids = tuple(dict.fromkeys(candidate_id.strip() for candidate_id in request.keep_candidate_ids if candidate_id.strip()))
        explicit_keep_folders = tuple(dict.fromkeys(folder.strip() for folder in request.keep_artifact_folders if folder.strip()))
        if request.apply and not keep_candidate_ids:
            raise ValueError("cleanup-artifacts requires at least one --keep-candidate-id when --apply is used")

        conn = load_connection(self._runtime_settings.db_path, self._runtime_settings.sqlite_config_path)
        try:
            before_counts = self._database_counts(conn)
            all_candidate_ids = self._all_candidate_ids(conn)
            missing_keep_candidate_ids = sorted(set(keep_candidate_ids) - set(all_candidate_ids))
            delete_candidate_ids = sorted(set(all_candidate_ids) - set(keep_candidate_ids))
            entity_ids_to_delete = self._candidate_related_entity_ids(conn, delete_candidate_ids)
            referenced_folders = self._referenced_candidate_folders(conn, keep_candidate_ids)
            protected_folders = sorted(set(explicit_keep_folders) | referenced_folders)
            existing_folders = self._existing_candidate_artifact_folders()
            delete_folders = sorted(folder for folder in existing_folders if folder not in protected_folders)
            metadata_files = self._metadata_files()
            backup_path = None

            if request.apply:
                backup_path = self._backup_database(request.backup_dir)
                self._apply_database_cleanup(conn, delete_candidate_ids, entity_ids_to_delete)
                self._apply_artifact_folder_cleanup(delete_folders, metadata_files)

            after_counts = self._database_counts(conn) if request.apply else before_counts
            return {
                "apply": request.apply,
                "backup_path": str(backup_path) if backup_path else None,
                "keep_candidate_ids": list(keep_candidate_ids),
                "missing_keep_candidate_ids": missing_keep_candidate_ids,
                "delete_candidate_ids": delete_candidate_ids,
                "keep_artifact_folders": list(explicit_keep_folders),
                "referenced_artifact_folders": sorted(referenced_folders),
                "protected_artifact_folders": protected_folders,
                "delete_artifact_folders": delete_folders,
                "metadata_files": [str(path) for path in metadata_files],
                "before_counts": before_counts,
                "after_counts": after_counts,
            }
        finally:
            conn.close()

    def _all_candidate_ids(self, conn: sqlite3.Connection) -> list[str]:
        return [str(row[0]) for row in conn.execute("SELECT candidate_id FROM candidates")]

    def _candidate_related_entity_ids(self, conn: sqlite3.Connection, candidate_ids: list[str]) -> set[str]:
        entity_ids = set(candidate_ids)
        if not candidate_ids:
            return entity_ids
        placeholders = ",".join("?" for _ in candidate_ids)
        for table, id_column in self._CANDIDATE_ENTITY_TABLES.items():
            if not self._table_exists(conn, table):
                continue
            # table/id_column come from _CANDIDATE_ENTITY_TABLES allowlist; values are parameterized.
            rows = conn.execute(
                f"SELECT {id_column} FROM {table} WHERE candidate_id IN ({placeholders})",  # nosec B608
                candidate_ids,
            ).fetchall()
            entity_ids.update(str(row[0]) for row in rows if row[0])
        return entity_ids

    def _referenced_candidate_folders(self, conn: sqlite3.Connection, candidate_ids: tuple[str, ...]) -> set[str]:
        if not candidate_ids:
            return set()
        placeholders = ",".join("?" for _ in candidate_ids)
        # placeholders are generated only for parameter binding.
        rows = conn.execute(
            f"SELECT storage_path FROM artifacts WHERE candidate_id IN ({placeholders})",  # nosec B608
            candidate_ids,
        ).fetchall()
        folders: set[str] = set()
        candidate_root = self._runtime_settings.artifact_root / "candidates"
        for row in rows:
            storage_path = Path(str(row[0]))
            try:
                relative = storage_path.relative_to(candidate_root)
            except ValueError:
                continue
            if relative.parts:
                folders.add(relative.parts[0])
        return folders

    def _existing_candidate_artifact_folders(self) -> list[str]:
        candidate_root = self._runtime_settings.artifact_root / "candidates"
        if not candidate_root.exists():
            return []
        return sorted(path.name for path in candidate_root.iterdir() if path.is_dir() and not path.name.startswith("."))

    def _metadata_files(self) -> list[Path]:
        if not self._runtime_settings.artifact_root.exists():
            return []
        return sorted(self._runtime_settings.artifact_root.rglob(".DS_Store"))

    def _backup_database(self, backup_dir: Path | None) -> Path:
        target_dir = backup_dir or Path("/private/tmp")
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = target_dir / f"job_search_before_cleanup_{timestamp}.sqlite"
        shutil.copy2(self._runtime_settings.db_path, backup_path)
        return backup_path

    def _apply_database_cleanup(self, conn: sqlite3.Connection, candidate_ids: list[str], entity_ids: set[str]) -> None:
        with write_tx(conn, immediate=True):
            if entity_ids:
                ids = sorted(entity_ids)
                chunk_size = 500
                for index in range(0, len(ids), chunk_size):
                    chunk = ids[index : index + chunk_size]
                    placeholders = ",".join("?" for _ in chunk)
                    # placeholders are generated only for parameter binding.
                    conn.execute(
                        f"DELETE FROM audit_events WHERE entity_id IN ({placeholders})",  # nosec B608
                        chunk,
                    )
                for candidate_id in candidate_ids:
                    like = f"%{candidate_id}%"
                    conn.execute(
                        "DELETE FROM audit_events WHERE previous_state_json LIKE ? OR new_state_json LIKE ?",
                        (like, like),
                    )
            if candidate_ids:
                conn.executemany("DELETE FROM candidates WHERE candidate_id = ?", [(candidate_id,) for candidate_id in candidate_ids])

    def _apply_artifact_folder_cleanup(self, folder_names: list[str], metadata_files: list[Path]) -> None:
        candidate_root = self._runtime_settings.artifact_root / "candidates"
        for folder_name in folder_names:
            path = candidate_root / folder_name
            if path.is_dir():
                shutil.rmtree(path)
        for path in metadata_files:
            if path.exists() and path.is_file():
                path.unlink()

    def _database_counts(self, conn: sqlite3.Connection) -> dict[str, int | None]:
        return {table: self._safe_count(conn, table) for table in self._COUNT_SQL}

    def _safe_count(self, conn: sqlite3.Connection, table: str) -> int | None:
        if not self._table_exists(conn, table):
            return None
        return int(conn.execute(self._COUNT_SQL[table]).fetchone()[0])

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None
