import subprocess
import tempfile
import unittest
from pathlib import Path

from telegram_shared.bridge_env import run_worker_subprocess


class SharedBridgeEnvTests(unittest.TestCase):
    def test_run_worker_subprocess_uses_standard_bridge_subprocess_options(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            captured["argv"] = args[0]
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_worker_subprocess(
                ["python3", "worker.py"],
                cwd=Path(tmp_dir),
                env={"TOKEN": "secret"},
                timeout_seconds=7200,
                run_func=fake_run,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(captured["argv"], ["python3", "worker.py"])
        self.assertEqual(captured["cwd"], tmp_dir)
        self.assertEqual(captured["capture_output"], True)
        self.assertEqual(captured["env"], {"TOKEN": "secret"})
        self.assertEqual(captured["text"], True)
        self.assertEqual(captured["timeout"], 7200)
        self.assertEqual(captured["check"], False)


if __name__ == "__main__":
    unittest.main()
