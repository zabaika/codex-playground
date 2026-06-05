from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND_ROOT = PROJECT_ROOT.parents[1]
SMOKE_RUNNER = PROJECT_ROOT / "scripts" / "smoke" / "stage1-smoke-run.sh"
OPERATOR_SCRIPTS = sorted((PROJECT_ROOT / "scripts" / "operator").glob("*.sh"))


class SmokeRunnerTest(unittest.TestCase):
    def test_runner_shell_syntax_is_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SMOKE_RUNNER)],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_operator_scripts_shell_syntax_is_valid(self) -> None:
        self.assertTrue(OPERATOR_SCRIPTS)
        result = subprocess.run(
            ["bash", "-n", *[str(path) for path in OPERATOR_SCRIPTS]],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_state_self_test_covers_partial_rerun_cleanup(self) -> None:
        result = subprocess.run(
            ["bash", str(SMOKE_RUNNER), "self-test-state"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("partial rerun cleanup", result.stdout)

    def test_job_search_imports_common_without_runtime_sys_path_patch(self) -> None:
        connection_module = PROJECT_ROOT / "src" / "job_search" / "infrastructure" / "db" / "connection.py"
        self.assertNotIn("sys.path", connection_module.read_text(encoding="utf-8"))

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from job_search.infrastructure.db.connection import load_connection; print(load_connection.__name__)",
            ],
            text=True,
            capture_output=True,
            check=False,
            env={"PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PLAYGROUND_ROOT}"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "load_connection")


if __name__ == "__main__":
    unittest.main()
