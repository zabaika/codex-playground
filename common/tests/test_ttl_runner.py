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
    previous_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
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
    assert {signum: signal.getsignal(signum) for signum in previous_handlers} == previous_handlers


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


def test_external_timeout_during_nested_cleanup_stops_descendant(tmp_path):
    import time

    group_file = tmp_path / "group.json"
    ready = tmp_path / "ready"
    effect = tmp_path / "orphan-effect"
    descendant = (
        "import signal,sys,time;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "Path(sys.argv[1]).touch();time.sleep(5);Path(sys.argv[2]).touch()"
    )
    worker = (
        "import json,os,subprocess,sys,time;from pathlib import Path;"
        "subprocess.Popen([sys.executable,'-I','-c',sys.argv[1],sys.argv[2],sys.argv[3]]);"
        "\nwhile not Path(sys.argv[2]).exists():time.sleep(0.01)"
        "\nPath(sys.argv[4]).write_text(json.dumps({'pgid':os.getpgrp()}))"
    )
    nested = [sys.executable, "-I", ttl_runner.__file__, "--timeout-seconds", "30",
              "--grace-seconds", "20", "--poll-interval-seconds", "0.02", "--",
              sys.executable, "-I", "-c", worker, descendant, str(ready), str(effect), str(group_file)]
    group = None
    try:
        result = ttl_runner.run_with_ttl(
            nested, timeout_seconds=3, grace_seconds=0.1, poll_interval_seconds=0.02,
            timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
            audit_file=None, timeout_reason="fixture_outer_timeout", use_caffeinate=False,
        )
        assert result == 124
        assert group_file.exists()
        group = json.loads(group_file.read_text())["pgid"]
        deadline = time.monotonic() + 3
        while ttl_runner.process_group_exists(group) and time.monotonic() < deadline and not effect.exists():
            time.sleep(0.02)
        assert not effect.exists(), "Descendant survived cancellation during nested cleanup"
        assert not ttl_runner.process_group_exists(group)
    finally:
        if group is None and group_file.exists():
            group = json.loads(group_file.read_text())["pgid"]
        if group is not None:
            ttl_runner.send_signal_to_process_group(group, "KILL")


def test_external_term_is_cancelled_not_a_local_timeout(tmp_path):
    import time

    group_file = tmp_path / "group.json"
    audit = tmp_path / "audit.json"
    audit.write_text('{"phase":"fixture"}')
    code = (
        "import json,os,signal,sys,time;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "Path(sys.argv[1]).write_text(json.dumps({'pgid':os.getpgrp()}));time.sleep(30)"
    )
    supervisor = subprocess.Popen([
        sys.executable, "-I", ttl_runner.__file__, "--timeout-seconds", "30",
        "--grace-seconds", "20", "--poll-interval-seconds", "0.02",
        "--audit-file", str(audit), "--", sys.executable, "-I", "-c", code, str(group_file),
    ])
    group = None
    try:
        deadline = time.monotonic() + 5
        while not group_file.exists() and supervisor.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert group_file.exists()
        group = json.loads(group_file.read_text())["pgid"]
        supervisor.send_signal(signal.SIGTERM)
        assert supervisor.wait(timeout=3) == 128 + signal.SIGTERM
        payload = json.loads(audit.read_text())
        assert payload["status"] == "cancelled" and payload["reason"] == "external_signal"
        assert payload["phase"] == "fixture" and payload["cancel_signal"] == "SIGTERM"
        assert "timeout_reason" not in payload
        assert not ttl_runner.process_group_exists(group)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=3)
        if group is None and group_file.exists():
            group = json.loads(group_file.read_text())["pgid"]
        if group is not None:
            ttl_runner.send_signal_to_process_group(group, "KILL")


def test_application_sigterm_handler_keeps_its_exception_and_is_restored(tmp_path, monkeypatch):
    class FixtureTermination(BaseException):
        pass

    def application_handler(signum, frame):
        raise FixtureTermination()

    children = []
    actual_popen = subprocess.Popen

    def capture(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(ttl_runner.subprocess, "Popen", capture)
    previous = signal.signal(signal.SIGTERM, application_handler)
    try:
        with pytest.raises(FixtureTermination):
            ttl_runner.run_with_ttl(
                [sys.executable, "-I", "-c",
                 "import os,signal,time;time.sleep(0.1);os.kill(os.getppid(),signal.SIGTERM);time.sleep(30)"],
                timeout_seconds=5, grace_seconds=0.1, poll_interval_seconds=0.02,
                timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
                audit_file=None, timeout_reason="fixture", use_caffeinate=False,
            )
        assert signal.getsignal(signal.SIGTERM) is application_handler
        assert children[0].poll() is not None
        assert not ttl_runner.process_group_exists(children[0].pid)
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_thread_caller_keeps_application_owned_signal_policy():
    from concurrent.futures import ThreadPoolExecutor

    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            ttl_runner.run_with_ttl, [sys.executable, "-I", "-c", "pass"],
            timeout_seconds=5, grace_seconds=0.1, poll_interval_seconds=0.02,
            timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
            audit_file=None, timeout_reason="fixture", use_caffeinate=False,
        ).result(timeout=10)
    assert result == 0
    assert {signum: signal.getsignal(signum) for signum in previous} == previous


if __name__ == "__main__":
    unittest.main()
