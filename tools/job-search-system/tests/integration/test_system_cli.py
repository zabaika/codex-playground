from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND_ROOT = PROJECT_ROOT.parents[1]


class SystemCliIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "config" / "runtime.local.toml"
        self.workspace_path = self.root / "config" / "workspace.local.toml"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            "\n".join(
                [
                    "[paths]",
                    f"db_path = '{self.root / 'data' / 'job_search.sqlite'}'",
                    f"artifact_root = '{self.root / 'data' / 'artifacts'}'",
                    f"sqlite_config_path = '{PLAYGROUND_ROOT / 'common' / 'config' / 'sqlite.toml'}'",
                    "",
                    "[runtime]",
                    "default_locale = 'en'",
                    "enable_ai_extraction = false",
                    "api_max_body_bytes = 1048576",
                    "api_allow_local_file_sources = false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_system_version_and_doctor_run_outside_repo_root(self) -> None:
        version = self._json_stdout(self._run_system_cli("version"))
        self.assertEqual(version["package_version"], "0.1.0")
        self.assertEqual(version["api_contract_version"], "2026-06-05.1")
        self.assertEqual(version["schema_contract_version"], "2026-06-01.2")

        doctor = self._json_stdout(self._run_system_cli("doctor"))
        self.assertEqual(doctor["summary"]["fail"], 0)
        self.assertIn("database", {check["name"] for check in doctor["checks"]})
        self.assertEqual(doctor["schemas"]["schema_contract_version"], "2026-06-01.2")

        observability = self._json_stdout(self._run_system_cli("observability"))
        self.assertEqual(observability["counts"], {})
        self.assertEqual(observability["recent_audit_events"], [])
        self.assertEqual(observability["recent_board_action_idempotency_keys"], [])

        strategy = self._json_stdout(self._run_system_cli("strategy-report"))
        self.assertEqual(strategy["summary"], {})
        self.assertEqual(strategy["resume_effectiveness"], [])

    def _run_system_cli(self, command: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PLAYGROUND_ROOT}",
        }
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "job_search.interfaces.cli.system_cli",
                "--config-path",
                str(self.config_path),
                "--workspace-path",
                str(self.workspace_path),
                command,
            ],
            cwd=self.root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    def _json_stdout(self, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
        return json.loads(result.stdout)


if __name__ == "__main__":
    unittest.main()
