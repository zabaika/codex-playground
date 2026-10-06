import json
from datetime import datetime, time
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYGROUND_ROOT = PROJECT_ROOT.parent
if str(PLAYGROUND_ROOT) not in sys.path:
    sys.path.insert(0, str(PLAYGROUND_ROOT))

from common.process import ProcessConfig
from config import LaunchdSettings
from launchd.run_monitor import run_scheduled_check


class TestSettings:
    timeout_seconds = 0.15
    launchd = LaunchdSettings(
        first_run_time=time(10, 0),
        interval_hours=5,
        last_run_time=time(20, 0),
        weekdays=(1, 2, 3, 4, 5),
    )


PROCESS_CONFIG = ProcessConfig(
    default_run_total_timeout_seconds=1800,
    default_termination_grace_seconds=0.1,
    poll_interval_seconds=0.05,
    timeout_exit_code=124,
    term_signal="TERM",
    kill_signal="KILL",
)


def test_runner_kills_a_hung_process_group_and_records_timeout(tmp_path: Path) -> None:
    audit_file = tmp_path / "com.infoext.monitor.last_attempt.json"

    exit_code = run_scheduled_check(
        TestSettings(),
        audit_file=audit_file,
        now=datetime(2026, 10, 2, 10, 0),
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        process_config=PROCESS_CONFIG,
    )

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert exit_code == 124
    assert payload["status"] == "timed_out"
    assert payload["timeout_reason"] == "process_ttl_expired"
    assert payload["termination_grace_seconds"] == 0.1
    assert payload["elapsed_seconds"] < 2


def test_runner_skips_late_wake_without_starting_a_check(tmp_path: Path) -> None:
    audit_file = tmp_path / "com.infoext.monitor.last_attempt.json"

    exit_code = run_scheduled_check(
        TestSettings(),
        audit_file=audit_file,
        now=datetime(2026, 10, 2, 21, 0),
        command=[sys.executable, "-c", "raise SystemExit(1)"],
        process_config=PROCESS_CONFIG,
    )

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["status"] == "skipped"
    assert payload["reason"] == "outside_schedule_window"


def test_runner_skips_weekend_without_starting_a_check(tmp_path: Path) -> None:
    audit_file = tmp_path / "com.infoext.monitor.last_attempt.json"

    exit_code = run_scheduled_check(
        TestSettings(),
        audit_file=audit_file,
        now=datetime(2026, 10, 3, 10, 0),
        command=[sys.executable, "-c", "raise SystemExit(1)"],
        process_config=PROCESS_CONFIG,
    )

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["status"] == "skipped"
    assert payload["reason"] == "outside_schedule_days"


def test_scheduled_runner_owns_the_only_timeout_supervisor(tmp_path, monkeypatch) -> None:
    from common import ttl_runner

    commands = []
    monkeypatch.setattr(ttl_runner, "run_with_ttl", lambda command, **kwargs: (
        commands.append(command) or 0
    ))
    assert run_scheduled_check(
        TestSettings(), audit_file=tmp_path / "attempt.json",
        now=datetime(2026, 10, 2, 10, 0), process_config=PROCESS_CONFIG,
    ) == 0
    assert commands[0][0] == sys.executable
    assert commands[0][-2:] == ["--check-now", "--_ttl-worker"]


def test_worker_sleep_exit_is_preserved_in_scheduled_audit(tmp_path, monkeypatch) -> None:
    from common import ttl_runner
    monkeypatch.setattr(ttl_runner, "run_with_ttl", lambda *_args, **_kwargs: PROCESS_CONFIG.sleep_interruption_exit_code)
    audit = tmp_path / "attempt.json"
    code = run_scheduled_check(TestSettings(), audit_file=audit,
                               now=datetime(2026, 10, 2, 10, 0), process_config=PROCESS_CONFIG)
    payload = json.loads(audit.read_text())
    assert code == PROCESS_CONFIG.sleep_interruption_exit_code
    assert payload["status"] == "interrupted"
    assert payload["reason"] == "host_sleep_interrupted"
