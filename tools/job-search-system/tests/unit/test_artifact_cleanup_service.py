from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT.parents[1]) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parents[1]))

from job_search.application.services.artifact_cleanup_service import ArtifactCleanupRequest, ArtifactCleanupService  # noqa: E402
from job_search.config import RuntimeSettings  # noqa: E402
from job_search.infrastructure.db.connection import load_connection, write_tx  # noqa: E402
from job_search.infrastructure.db.schema_version import apply_migrations  # noqa: E402


class ArtifactCleanupServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data" / "job_search.sqlite"
        self.artifact_root = self.root / "data" / "artifacts"
        self.sqlite_config_path = PROJECT_ROOT.parents[1] / "common" / "config" / "sqlite.toml"
        self.conn = load_connection(self.db_path, self.sqlite_config_path)
        apply_migrations(self.conn, PROJECT_ROOT / "src" / "job_search" / "infrastructure" / "migrations")
        self.settings = RuntimeSettings(
            db_path=self.db_path,
            artifact_root=self.artifact_root,
            sqlite_config_path=self.sqlite_config_path,
            default_locale="en",
            enable_ai_extraction=False,
            api_max_body_bytes=1024 * 1024,
            api_allow_local_file_sources=False,
        )
        self.service = ArtifactCleanupService(runtime_settings=self.settings)
        self._seed_records()

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_dry_run_reports_cleanup_without_mutating_state(self) -> None:
        result = self.service.cleanup(
            ArtifactCleanupRequest(
                keep_candidate_ids=("keep-candidate",),
                keep_artifact_folders=("keep-folder",),
                apply=False,
            )
        )

        self.assertFalse(result["apply"])
        self.assertIn("delete-candidate", result["delete_candidate_ids"])
        self.assertIn("delete-folder", result["delete_artifact_folders"])
        self.assertEqual(self._count("candidates"), 2)
        self.assertTrue((self.artifact_root / "candidates" / "delete-folder").exists())

    def test_apply_removes_deleted_candidate_records_audit_events_and_folders(self) -> None:
        backup_dir = self.root / "backups"

        result = self.service.cleanup(
            ArtifactCleanupRequest(
                keep_candidate_ids=("keep-candidate",),
                keep_artifact_folders=("keep-folder",),
                apply=True,
                backup_dir=backup_dir,
            )
        )

        self.assertTrue(result["apply"])
        self.assertTrue(Path(str(result["backup_path"])).is_file())
        self.assertEqual(self._count("candidates"), 1)
        self.assertEqual(self._count("artifacts"), 1)
        self.assertEqual(self._count("canonical_vacancies"), 0)
        self.assertEqual(self._count("audit_events"), 1)
        self.assertTrue((self.artifact_root / "candidates" / "keep-folder").exists())
        self.assertFalse((self.artifact_root / "candidates" / "delete-folder").exists())
        self.assertFalse((self.artifact_root / ".DS_Store").exists())

    def _seed_records(self) -> None:
        keep_file = self.artifact_root / "candidates" / "keep-folder" / "sources" / "keep.md"
        delete_file = self.artifact_root / "candidates" / "delete-folder" / "sources" / "delete.md"
        keep_file.parent.mkdir(parents=True, exist_ok=True)
        delete_file.parent.mkdir(parents=True, exist_ok=True)
        keep_file.write_text("keep", encoding="utf-8")
        delete_file.write_text("delete", encoding="utf-8")
        (self.artifact_root / ".DS_Store").write_text("metadata", encoding="utf-8")

        with write_tx(self.conn, immediate=True):
            self.conn.execute(
                "INSERT INTO candidates(candidate_id, display_name, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                ("keep-candidate", "Keep Candidate", "active", "2026-05-27T00:00:00+00:00", "2026-05-27T00:00:00+00:00"),
            )
            self.conn.execute(
                "INSERT INTO candidates(candidate_id, display_name, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (
                    "delete-candidate",
                    "Delete Candidate",
                    "active",
                    "2026-05-27T00:00:00+00:00",
                    "2026-05-27T00:00:00+00:00",
                ),
            )
            self.conn.execute(
                """
                INSERT INTO artifacts(artifact_id, artifact_type, candidate_id, storage_path, content_hash, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                ("keep-artifact", "resume_source", "keep-candidate", str(keep_file), "hash-keep", "2026-05-27T00:00:00+00:00"),
            )
            self.conn.execute(
                """
                INSERT INTO artifacts(artifact_id, artifact_type, candidate_id, storage_path, content_hash, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    "delete-artifact",
                    "resume_source",
                    "delete-candidate",
                    str(delete_file),
                    "hash-delete",
                    "2026-05-27T00:00:00+00:00",
                ),
            )
            self.conn.execute(
                """
                INSERT INTO canonical_vacancies(
                    canonical_vacancy_id, candidate_id, company_name, role_title, location_text,
                    normalized_company_name, normalized_role_title, normalized_location_text, dedupe_key,
                    workflow_stage, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "delete-vacancy",
                    "delete-candidate",
                    "Delete Corp",
                    "CTO",
                    "Remote",
                    "delete corp",
                    "cto",
                    "remote",
                    "delete-key",
                    "new",
                    "2026-05-27T00:00:00+00:00",
                    "2026-05-27T00:00:00+00:00",
                ),
            )
            self.conn.execute(
                """
                INSERT INTO audit_events(audit_event_id, command_name, actor, entity_type, entity_id, previous_state_json, new_state_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("audit-keep", "CreateCandidate", "system", "candidate", "keep-candidate", None, None, "2026-05-27T00:00:00+00:00"),
            )
            self.conn.execute(
                """
                INSERT INTO audit_events(audit_event_id, command_name, actor, entity_type, entity_id, previous_state_json, new_state_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "audit-delete",
                    "ImportVacancyBatch",
                    "system",
                    "canonical_vacancy",
                    "delete-vacancy",
                    None,
                    '{"candidate_id":"delete-candidate"}',
                    "2026-05-27T00:00:00+00:00",
                ),
            )

    def _count(self, table: str) -> int:
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
