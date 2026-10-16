from __future__ import annotations

import json
import inspect
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import unittest
import pytest

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


def test_stop_callback_terminates_owned_process_group(tmp_path):
    assert "stop_requested" in inspect.signature(ttl_runner.run_with_ttl).parameters, "Outer TTL needs cooperative stop observation"
    ready = tmp_path / "ready"
    audit = tmp_path / "audit.json"
    audit.write_text('{"phase":"extract"}')
    code = ("import signal,time,sys; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text('ready'); time.sleep(30)")
    result = ttl_runner.run_with_ttl(
        [sys.executable, "-c", code, str(ready)], timeout_seconds=5, grace_seconds=0.1,
        poll_interval_seconds=0.02, timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
        audit_file=audit, timeout_reason="process_ttl_expired", use_caffeinate=False,
        stop_requested=ready.exists,
    )
    assert result == 128 + signal.SIGTERM
    payload = json.loads(audit.read_text())
    assert payload["status"] == "cancelled" and payload["phase"] == "extract"
    assert payload["reason"] == "cancel_requested" and "timeout_reason" not in payload
    assert not ttl_runner.process_group_exists(payload["stopped_process_group_id"])


def test_stop_callback_failure_cleans_child_before_propagating(tmp_path, monkeypatch):
    assert "stop_requested" in inspect.signature(ttl_runner.run_with_ttl).parameters
    children = []
    original = subprocess.Popen

    def capture(*a, **kw):
        child = original(*a, **kw)
        children.append(child)
        return child

    def stop():
        raise OSError("fixture callback failure")

    monkeypatch.setattr(ttl_runner.subprocess, "Popen", capture)
    with pytest.raises(OSError, match="fixture callback"):
        ttl_runner.run_with_ttl(
            [sys.executable, "-c", "import time;time.sleep(30)"], timeout_seconds=5, grace_seconds=0.1,
            poll_interval_seconds=0.02, timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
            audit_file=None, timeout_reason="process_ttl_expired", use_caffeinate=False,
            stop_requested=stop,
        )
    assert children[0].poll() is not None
    assert not ttl_runner.process_group_exists(children[0].pid)


def test_ttl_child_receives_explicit_environment_only(tmp_path, monkeypatch):
    assert "env" in inspect.signature(ttl_runner.run_with_ttl).parameters, "Child environment must remain caller-owned"
    monkeypatch.setenv("FIXTURE_BLOCKED", "must not reach worker")
    captured = tmp_path / "environment.json"
    code = ("import os,json,sys,time;from pathlib import Path;"
            "Path(sys.argv[1]).write_text(json.dumps([os.getenv('FIXTURE_ALLOWED'),os.getenv('FIXTURE_BLOCKED')]));time.sleep(30)")
    result = ttl_runner.run_with_ttl(
        [sys.executable, "-c", code, str(captured)], timeout_seconds=0.3, grace_seconds=0.1,
        poll_interval_seconds=0.02, timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
        audit_file=None, timeout_reason="process_ttl_expired", use_caffeinate=False,
        env={"FIXTURE_ALLOWED": "yes"},
    )
    assert result == 124
    assert json.loads(captured.read_text()) == ["yes", None]


def test_root_exit_does_not_leave_owned_descendant(tmp_path):
    group_file = tmp_path / "group.json"
    marker = tmp_path / "descendant-ready"
    descendant = "import signal,sys,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(sys.argv[1]).write_text('ready');time.sleep(30)"
    code = ("import json,os,subprocess,sys,time;from pathlib import Path;"
            "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
            "\nwhile not Path(sys.argv[2]).exists(): time.sleep(0.01)"
            "\nPath(sys.argv[3]).write_text(json.dumps({'pgid':os.getpgrp()}))")
    group = None
    try:
        result = ttl_runner.run_with_ttl(
            [sys.executable, "-c", code, descendant, str(marker), str(group_file)],
            timeout_seconds=5, grace_seconds=0.1, poll_interval_seconds=0.02,
            timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
            audit_file=None, timeout_reason="process_ttl_expired", use_caffeinate=False,
        )
        group = json.loads(group_file.read_text())["pgid"]
        assert result == 0
        assert ttl_runner.wait_for_process_group_exit(group, timeout_seconds=0.3, poll_interval_seconds=0.02), "Root exit is not proof that its process group stopped"
    finally:
        if group is None and group_file.exists():
            group = json.loads(group_file.read_text())["pgid"]
        if group is not None:
            ttl_runner.send_signal_to_process_group(group, "KILL")


if __name__ == "__main__":
    unittest.main()
