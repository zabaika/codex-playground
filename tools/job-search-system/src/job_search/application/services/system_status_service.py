from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3

from job_search import API_CONTRACT_VERSION, __version__
from job_search.application.services.schema_contract_service import SchemaContractService
from job_search.config import RuntimeSettings, WorkspaceSettings
from job_search.infrastructure.repositories.observability_repository import ObservabilityRepository


@dataclass(frozen=True, slots=True)
class SystemCheck:
    name: str
    status: str
    message: str


class SystemStatusService:
    _COUNT_SQL = {
        "schema_migrations": "SELECT COUNT(*) FROM schema_migrations",
        "candidates": "SELECT COUNT(*) FROM candidates",
        "canonical_vacancies": "SELECT COUNT(*) FROM canonical_vacancies",
        "audit_events": "SELECT COUNT(*) FROM audit_events",
        "quality_gate_runs": "SELECT COUNT(*) FROM quality_gate_runs",
        "approval_records": "SELECT COUNT(*) FROM approval_records",
        "manual_board_actions": "SELECT COUNT(*) FROM manual_board_actions",
        "interview_rounds": "SELECT COUNT(*) FROM interview_rounds",
    }

    def __init__(
        self,
        *,
        runtime_settings: RuntimeSettings,
        workspace_settings: WorkspaceSettings,
        migrations_dir: Path,
        schemas_dir: Path | None = None,
    ) -> None:
        self._runtime_settings = runtime_settings
        self._workspace_settings = workspace_settings
        self._migrations_dir = migrations_dir
        self._schemas_dir = schemas_dir or Path(__file__).resolve().parents[4] / "schemas"

    def version(self) -> dict[str, object]:
        schema_manifest = SchemaContractService(schemas_dir=self._schemas_dir).manifest()
        return {
            "package_version": __version__,
            "api_contract_version": API_CONTRACT_VERSION,
            "schema_contract_version": schema_manifest["schema_contract_version"],
            "stage": "stage2-non-ui",
        }

    def status(self) -> dict[str, object]:
        checks = self._checks()
        return {
            **self.version(),
            "active_candidate_id": self._workspace_settings.active_candidate_id,
            "paths": {
                "db_path": str(self._runtime_settings.db_path),
                "artifact_root": str(self._runtime_settings.artifact_root),
                "sqlite_config_path": str(self._runtime_settings.sqlite_config_path),
            },
            "runtime": {
                "default_locale": self._runtime_settings.default_locale,
                "enable_ai_extraction": self._runtime_settings.enable_ai_extraction,
                "api_max_body_bytes": self._runtime_settings.api_max_body_bytes,
                "api_allow_local_file_sources": self._runtime_settings.api_allow_local_file_sources,
            },
            "checks": [asdict(check) for check in checks],
            "summary": self._summary(checks),
            "database": self._database_summary(),
            "schemas": SchemaContractService(schemas_dir=self._schemas_dir).manifest(),
        }

    def observability(self, *, candidate_id: str | None = None, limit: int = 20) -> dict[str, object]:
        if not self._runtime_settings.db_path.exists():
            return {
                **self.version(),
                "candidate_id": candidate_id,
                "counts": {},
                "quality_gate_counts": {},
                "recent_audit_events": [],
                "recent_quality_gate_issues": [],
                "recent_artifact_usage_events": [],
                "recent_board_action_idempotency_keys": [],
            }
        with closing(self._readonly_connection()) as conn:
            conn.row_factory = sqlite3.Row
            summary = ObservabilityRepository(conn).summary(candidate_id=candidate_id, limit=limit)
        return {**self.version(), "candidate_id": candidate_id, **summary}

    def strategy_report(self, *, candidate_id: str | None = None, limit: int = 20) -> dict[str, object]:
        if not self._runtime_settings.db_path.exists():
            return {
                **self.version(),
                "candidate_id": candidate_id,
                "summary": {},
                "funnel": {},
                "by_role": [],
                "by_company": [],
                "by_source_kind": [],
                "follow_up": {},
                "quality": {},
                "board_actions": {},
                "resume_effectiveness": [],
                "position_effectiveness": [],
                "limitations": ["Database does not exist yet."],
            }
        with closing(self._readonly_connection()) as conn:
            conn.row_factory = sqlite3.Row
            report = ObservabilityRepository(conn).strategy_report(candidate_id=candidate_id, limit=limit)
        return {**self.version(), "candidate_id": candidate_id, **report}

    def _checks(self) -> list[SystemCheck]:
        checks = [
            self._path_check("sqlite_config", self._runtime_settings.sqlite_config_path, expected="file"),
            self._path_check("artifact_root", self._runtime_settings.artifact_root, expected="dir-or-missing"),
            self._path_check("migrations_dir", self._migrations_dir, expected="dir"),
            self._pdftotext_check(),
            self._db_check(),
        ]
        if self._runtime_settings.api_allow_local_file_sources:
            checks.append(
                SystemCheck(
                    name="api_file_sources",
                    status="warn",
                    message="API local file source ingestion is enabled; keep API-lite loopback-only",
                )
            )
        return checks

    def _path_check(self, name: str, path: Path, *, expected: str) -> SystemCheck:
        if expected == "file" and path.is_file():
            return SystemCheck(name=name, status="ok", message=str(path))
        if expected == "dir" and path.is_dir():
            return SystemCheck(name=name, status="ok", message=str(path))
        if expected == "dir-or-missing" and (path.is_dir() or not path.exists()):
            status = "ok" if path.is_dir() else "warn"
            message = str(path) if path.is_dir() else f"{path} does not exist yet"
            return SystemCheck(name=name, status=status, message=message)
        return SystemCheck(name=name, status="fail", message=f"Expected {expected}: {path}")

    def _pdftotext_check(self) -> SystemCheck:
        executable = shutil.which("pdftotext")
        if executable:
            return SystemCheck(name="pdftotext", status="ok", message=executable)
        return SystemCheck(name="pdftotext", status="warn", message="PDF source ingestion requires pdftotext")

    def _db_check(self) -> SystemCheck:
        if not self._runtime_settings.db_path.exists():
            return SystemCheck(name="database", status="warn", message=f"{self._runtime_settings.db_path} does not exist yet")
        try:
            with closing(self._readonly_connection()) as conn:
                conn.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            return SystemCheck(name="database", status="fail", message=str(exc))
        return SystemCheck(name="database", status="ok", message=str(self._runtime_settings.db_path))

    def _database_summary(self) -> dict[str, object]:
        if not self._runtime_settings.db_path.exists():
            return {"exists": False}
        try:
            with closing(self._readonly_connection()) as conn:
                return {
                    "exists": True,
                    **{table: self._safe_count(conn, table) for table in self._COUNT_SQL},
                }
        except sqlite3.Error as exc:
            return {"exists": True, "error": str(exc)}

    def _readonly_connection(self) -> sqlite3.Connection:
        uri = f"file:{self._runtime_settings.db_path}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _safe_count(self, conn: sqlite3.Connection, table: str) -> int | None:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        if row is None:
            return None
        return int(conn.execute(self._COUNT_SQL[table]).fetchone()[0])

    def _summary(self, checks: list[SystemCheck]) -> dict[str, int]:
        return {
            "ok": sum(1 for check in checks if check.status == "ok"),
            "warn": sum(1 for check in checks if check.status == "warn"),
            "fail": sum(1 for check in checks if check.status == "fail"),
        }
