from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process
from common import ttl_runner


class ProcessConfigTests(unittest.TestCase):
    def test_load_process_config_from_default_bundle(self) -> None:
        config = common_process.load_process_config()

        self.assertEqual(config.default_run_total_timeout_seconds, 1800)
        self.assertEqual(config.default_termination_grace_seconds, 10)
        self.assertEqual(config.poll_interval_seconds, 1.0)
        self.assertEqual(config.timeout_exit_code, 124)
        self.assertEqual(config.term_signal, "TERM")
        self.assertEqual(config.kill_signal, "KILL")


class TtlRunnerTests(unittest.TestCase):
    def test_run_with_ttl_marks_timeout_in_audit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            audit_path = Path(tmp_dir) / "digest.last_attempt.json"
            audit_path.write_text('{"status":"started","phase":"syncing"}\n', encoding="utf-8")

            exit_code = ttl_runner.run_with_ttl(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_seconds=0.2,
                grace_seconds=0.2,
                poll_interval_seconds=0.05,
                timeout_exit_code=124,
                term_signal="TERM",
                kill_signal="KILL",
                audit_file=audit_path,
                timeout_reason="process_ttl_expired",
                use_caffeinate=False,
            )

            payload = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 124)
        self.assertEqual(payload["status"], "timed_out")
        self.assertEqual(payload["phase"], "syncing")
        self.assertEqual(payload["timeout_reason"], "process_ttl_expired")
        self.assertEqual(payload["run_total_timeout_seconds"], 0.2)
        self.assertEqual(payload["termination_grace_seconds"], 0.2)
        self.assertIsNotNone(payload["finished_at"])
        self.assertIsNotNone(payload["updated_at"])


if __name__ == "__main__":
    unittest.main()
