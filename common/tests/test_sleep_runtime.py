"""Sleep-inclusive TTL and bounded sleep-inhibitor diagnostics."""

import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process, ttl_runner


def options(audit):
    return dict(timeout_seconds=30, grace_seconds=0.1, poll_interval_seconds=0.05,
                timeout_exit_code=124, term_signal="TERM", kill_signal="KILL",
                audit_file=audit, timeout_reason="process_ttl_expired", use_caffeinate=False)


def test_continuous_clock_and_sleep_calculation(monkeypatch):
    assert process.continuous_time() > 0
    monkeypatch.setattr(process, "continuous_time", lambda: 120)
    monkeypatch.setattr(process, "awake_time", lambda: 15)
    assert process.sleep_elapsed(100, 10) == 15


def test_sleep_jump_interrupts_and_kills_the_worker(tmp_path, monkeypatch):
    clock, sleep = process.continuous_time, time.sleep
    offset = 0
    def suspended_sleep(_):
        nonlocal offset
        sleep(0.05)
        offset = 1000
    monkeypatch.setattr(process, "continuous_time", lambda: clock() + offset)
    monkeypatch.setattr(ttl_runner.time, "sleep", suspended_sleep)
    audit = tmp_path / "attempt.json"
    code = ttl_runner.run_with_ttl([sys.executable, "-c", "import time; time.sleep(60)"], **options(audit))
    payload = json.loads(audit.read_text())
    assert code == process.load_process_config().sleep_interruption_exit_code
    assert payload["status"] == "interrupted"
    assert payload["reason"] == "host_sleep_interrupted"
    assert payload["sleep_seconds"] >= 999
    assert payload["elapsed_seconds"] >= 1000
    assert not ttl_runner.process_group_exists(payload["timed_out_process_group_id"])


def test_pause_policy_resumes_worker_after_sleep_longer_than_ttl(tmp_path, monkeypatch):
    clock, sleep = process.continuous_time, time.sleep
    offset = 0
    def suspended_sleep(_):
        nonlocal offset
        sleep(0.02)
        offset = 1000
    monkeypatch.setattr(process, "continuous_time", lambda: clock() + offset)
    monkeypatch.setattr(ttl_runner.time, "sleep", suspended_sleep)
    audit = tmp_path / "attempt.json"
    assert ttl_runner.run_with_ttl(
        [sys.executable, "-c", "import time; time.sleep(.15)"],
        sleep_policy="pause", **options(audit),
    ) == 0
    payload = json.loads(audit.read_text())
    assert payload["sleep_policy"] == "pause"
    assert payload["sleep_seconds"] >= 999
    assert payload["elapsed_seconds"] >= 1000
    assert payload["ttl_elapsed_seconds"] < 30
    assert "timeout_reason" not in payload


def test_pause_policy_still_kills_worker_when_awake_budget_expires(tmp_path, monkeypatch):
    continuous, awake, sleep = process.continuous_time, process.awake_time, time.sleep
    offset = 0
    def advance_clocks(_):
        nonlocal offset
        sleep(0.02)
        offset = 1000
    monkeypatch.setattr(process, "continuous_time", lambda: continuous() + 2 * offset)
    monkeypatch.setattr(process, "awake_time", lambda: awake() + offset)
    monkeypatch.setattr(ttl_runner.time, "sleep", advance_clocks)
    audit = tmp_path / "attempt.json"
    assert ttl_runner.run_with_ttl(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        sleep_policy="pause", **options(audit),
    ) == 124
    payload = json.loads(audit.read_text())
    assert payload["status"] == "timed_out"
    assert payload["timeout_reason"] == "process_ttl_expired"
    assert payload["ttl_elapsed_seconds"] >= 1000
    assert payload["sleep_seconds"] >= 999
    assert not ttl_runner.process_group_exists(payload["timed_out_process_group_id"])


@pytest.mark.parametrize("policy", ["interrupt", "pause"])
def test_sleep_policy_cli(monkeypatch, policy):
    monkeypatch.setattr(sys, "argv", ["ttl_runner", "--sleep-policy", policy, "--", "worker"])
    args = ttl_runner.parse_args()
    assert args.sleep_policy == policy
    assert args.command == ["worker"]


def test_sleep_policy_cli_defaults_to_interrupt(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ttl_runner", "--", "worker"])
    assert ttl_runner.parse_args().sleep_policy == "interrupt"


def test_invalid_sleep_policy_is_rejected_before_spawn(monkeypatch):
    monkeypatch.setattr(ttl_runner.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Invalid policy spawned worker"))
    with pytest.raises(ValueError, match="Unknown sleep policy"):
        ttl_runner.run_with_ttl(["worker"], sleep_policy="continue", **options(None))


@pytest.mark.parametrize("exit_code", [0, 1])
def test_completed_worker_keeps_exit_code_after_sleep_and_ttl_jump(tmp_path, monkeypatch, exit_code):
    clock = process.continuous_time
    offset = 0
    child = SimpleNamespace(pid=123, poll=Mock(side_effect=[None, exit_code, exit_code]))
    monkeypatch.setattr(ttl_runner.subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(ttl_runner.os, "getpgid", lambda _: 123)
    monkeypatch.setattr(process, "continuous_time", lambda: clock() + offset)
    def complete_then_sleep(_):
        nonlocal offset
        offset = 1000
    monkeypatch.setattr(ttl_runner.time, "sleep", complete_then_sleep)
    monkeypatch.setattr(ttl_runner, "send_signal_to_process_group", lambda *_: pytest.fail("Completed worker must not be killed"))
    audit = tmp_path / "attempt.json"
    assert ttl_runner.run_with_ttl(["synthetic-worker"], **options(audit)) == exit_code
    payload = json.loads(audit.read_text())
    assert payload["sleep_seconds"] >= 999
    assert "timeout_reason" not in payload
    assert payload.get("status") != "interrupted"


def test_caffeinate_uses_idle_and_ac_sleep_flags(monkeypatch):
    popen = Mock(return_value=object())
    monkeypatch.setattr(ttl_runner.sys, "platform", "darwin")
    monkeypatch.setattr(ttl_runner.os.path, "exists", lambda _: True)
    monkeypatch.setattr(ttl_runner.subprocess, "Popen", popen)
    ttl_runner.spawn_caffeinate(123)
    assert popen.call_args.args[0] == ["/usr/bin/caffeinate", "-i", "-s", "-w", "123"]
    assert popen.call_args.kwargs["stderr"] == subprocess.PIPE


def test_caffeinate_start_failure_is_audited_without_failing_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(ttl_runner, "spawn_caffeinate", Mock(side_effect=OSError("synthetic")))
    audit = tmp_path / "attempt.json"
    kwargs = options(audit)
    kwargs["use_caffeinate"] = True
    assert ttl_runner.run_with_ttl([sys.executable, "-c", "pass"], **kwargs) == 0
    payload = json.loads(audit.read_text())
    assert payload["caffeinate"]["status"] == "failed"
    assert payload["caffeinate"]["error_type"] == "OSError"


def test_caffeinate_early_exit_and_stderr_are_audited(tmp_path, monkeypatch):
    inhibitor = subprocess.Popen([sys.executable, "-c", "import sys; print('synthetic failure',file=sys.stderr); sys.exit(2)"],
                                 stderr=subprocess.PIPE, text=True)
    inhibitor.wait()
    monkeypatch.setattr(ttl_runner, "spawn_caffeinate", lambda _: inhibitor)
    audit = tmp_path / "attempt.json"
    kwargs = options(audit)
    kwargs["use_caffeinate"] = True
    assert ttl_runner.run_with_ttl([sys.executable, "-c", "import time; time.sleep(.1)"], **kwargs) == 0
    payload = json.loads(audit.read_text())
    assert payload["caffeinate"]["exit_code"] == 2
    assert payload["caffeinate"]["stderr"] == "synthetic failure"
    assert inhibitor.stderr.closed


def test_audit_write_failure_still_cleans_up_worker_and_inhibitor(tmp_path, monkeypatch):
    popen = subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    inhibitor = popen([sys.executable, "-c", "import time; time.sleep(60)"], stderr=subprocess.PIPE, text=True)
    monkeypatch.setattr(ttl_runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(ttl_runner, "spawn_caffeinate", lambda _: inhibitor)
    monkeypatch.setattr(ttl_runner, "write_audit_payload", Mock(side_effect=OSError("synthetic audit failure")))
    kwargs = options(tmp_path / "attempt.json")
    kwargs["use_caffeinate"] = True
    with pytest.raises(OSError, match="synthetic audit failure"):
        ttl_runner.run_with_ttl([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
    assert children[0].poll() is not None
    assert inhibitor.poll() is not None
