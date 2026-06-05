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

from job_search.application.services.artifact_rename_service import ArtifactRenameRequest, ArtifactRenameService  # noqa: E402
from job_search.config import RuntimeSettings  # noqa: E402
from job_search.infrastructure.db.connection import load_connection  # noqa: E402
from job_search.infrastructure.db.schema_version import apply_migrations  # noqa: E402


class ArtifactRenameServiceTest(unittest.TestCase):
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
        self.service = ArtifactRenameService(runtime_settings=self.settings)
        self._seed_records()

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_dry_run_reports_human_friendly_target_without_mutating(self) -> None:
        result = self.service.rename(ArtifactRenameRequest(candidate_ids=("candidate-1",), apply=False))

        self.assertFalse(result["apply"])
        self.assertEqual(result["rename_count"], 1)
        rename = result["renames"][0]
        self.assertTrue(str(rename["new_path"]).endswith("resume-markdown--cto-en--4d6e4ff4.md"))
        self.assertTrue(Path(str(rename["old_path"])).exists())

    def test_apply_renames_file_updates_storage_path_and_records_audit(self) -> None:
        backup_dir = self.root / "backups"

        result = self.service.rename(
            ArtifactRenameRequest(candidate_ids=("candidate-1",), apply=True, backup_dir=backup_dir)
        )

        self.assertTrue(result["apply"])
        self.assertTrue(Path(str(result["backup_path"])).is_file())
        new_path = Path(str(result["renames"][0]["new_path"]))
        self.assertTrue(new_path.exists())
        stored = self.conn.execute("SELECT storage_path FROM artifacts WHERE artifact_id = ?", ("4d6e4ff4-c218",)).fetchone()
        self.assertEqual(str(stored["storage_path"]), str(new_path))
        audit_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE command_name = 'RenameArtifactFiles'"
        ).fetchone()["count"]
        self.assertEqual(audit_count, 1)

    def _seed_records(self) -> None:
        now = "2026-05-27T00:00:00+00:00"
        folder = self.artifact_root / "candidates" / "ilya-melnikov--candidate"
        old_path = folder / "drafts" / "4d6e4ff4-c218.md"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text("# Resume\n", encoding="utf-8")
        with self.conn:
            self.conn.execute(
                "INSERT INTO candidates(candidate_id, display_name, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                ("candidate-1", "Example Candidate", "active", now, now),
            )
            self.conn.execute(
                "INSERT INTO candidate_profiles(candidate_id, full_name, updated_at) VALUES(?, ?, ?)",
                ("candidate-1", "Example Candidate", now),
            )
            self.conn.execute(
                """
                INSERT INTO artifacts(artifact_id, artifact_type, candidate_id, storage_path, content_hash, notes, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "4d6e4ff4-c218",
                    "resume_markdown",
                    "candidate-1",
                    str(old_path),
                    "hash",
                    '{"target_role":"CTO","language":"en"}',
                    now,
                ),
            )


if __name__ == "__main__":
    unittest.main()
