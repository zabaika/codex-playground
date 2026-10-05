#!/usr/bin/env python3
"""Scheduled InfoExt runner with shared process-group timeout control."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Sequence


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
for path in (RUNTIME_ROOT, RUNTIME_ROOT.parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common import process as common_process
from common import ttl_runner
from common.json_io import write_json_atomic
from config import PROJECT_ROOT, Settings, load_settings


LABEL = "com.infoext.monitor"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_last_attempt(path: Path, payload: dict[str, object]) -> None:
    """Atomically publish the latest scheduled-run state for operators."""
    write_json_atomic(path, payload)


def scheduled_times(settings: Settings) -> list[str]:
    return [value.strftime("%H:%M") for value in settings.launchd.calendar_times]


def run_scheduled_check(
    settings: Settings,
    *,
    audit_file: Path,
    now: datetime | None = None,
    command: Sequence[str] | None = None,
    process_config: common_process.ProcessConfig | None = None,
) -> int:
    """Run one launchd-triggered check, or record why the trigger was skipped."""
    started_at = utc_timestamp()
    started_monotonic = time.monotonic()
    current = now or datetime.now().astimezone()
    calendar_times = scheduled_times(settings)
    base_payload: dict[str, object] = {
        "started_at": started_at,
        "updated_at": started_at,
        "scheduled_times": calendar_times,
        "schedule_window": {
            "first_run_time": settings.launchd.first_run_time.strftime("%H:%M"),
            "last_run_time": settings.launchd.last_run_time.strftime("%H:%M"),
            "weekdays": list(settings.launchd.weekdays),
        },
    }
    if not settings.launchd.allows_weekday(current):
        base_payload.update(
            {
                "finished_at": started_at,
                "status": "skipped",
                "phase": "scheduling",
                "reason": "outside_schedule_days",
                "elapsed_seconds": 0.0,
            }
        )
        write_last_attempt(audit_file, base_payload)
        return 0
    if not settings.launchd.contains(current.timetz().replace(tzinfo=None)):
        base_payload.update(
            {
                "finished_at": started_at,
                "status": "skipped",
                "phase": "scheduling",
                "reason": "outside_schedule_window",
                "elapsed_seconds": 0.0,
            }
        )
        write_last_attempt(audit_file, base_payload)
        return 0

    process_config = process_config or common_process.load_process_config()
    base_payload.update(
        {
            "status": "running",
            "phase": "checking",
            "run_total_timeout_seconds": settings.timeout_seconds,
            "termination_grace_seconds": process_config.default_termination_grace_seconds,
            "timeout_exit_code": process_config.timeout_exit_code,
        }
    )
    write_last_attempt(audit_file, base_payload)
    # The scheduled runner already owns the hard TTL; avoid a second supervisor.
    check_command = list(command or [
        sys.executable, str(RUNTIME_ROOT / "main.py"), "--check-now", "--_ttl-worker"
    ])
    try:
        exit_code = ttl_runner.run_with_ttl(
            check_command,
            timeout_seconds=settings.timeout_seconds,
            grace_seconds=process_config.default_termination_grace_seconds,
            poll_interval_seconds=process_config.poll_interval_seconds,
            timeout_exit_code=process_config.timeout_exit_code,
            term_signal=process_config.term_signal,
            kill_signal=process_config.kill_signal,
            audit_file=audit_file,
            timeout_reason="process_ttl_expired",
            use_caffeinate=True,
        )
    except BaseException as exc:
        base_payload.update(
            {
                "updated_at": utc_timestamp(),
                "finished_at": utc_timestamp(),
                "status": "failed",
                "phase": "launcher",
                "error_type": exc.__class__.__name__,
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
            }
        )
        write_last_attempt(audit_file, base_payload)
        raise

    try:
        payload = json.loads(audit_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = dict(base_payload)
    if not isinstance(payload, dict):
        payload = dict(base_payload)
    payload.update(
        {
            "updated_at": utc_timestamp(),
            "finished_at": payload.get("finished_at") or utc_timestamp(),
            "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
            "exit_code": exit_code,
        }
    )
    if exit_code == process_config.timeout_exit_code:
        payload.setdefault("status", "timed_out")
        payload.setdefault("phase", "checking")
    elif exit_code == 0:
        payload.update({"status": "succeeded", "phase": "complete"})
    else:
        payload.update({"status": "failed", "phase": "complete"})
    write_last_attempt(audit_file, payload)
    return exit_code


def main() -> int:
    audit_file = PROJECT_ROOT / "data" / "launchd" / f"{LABEL}.last_attempt.json"
    try:
        settings = load_settings(PROJECT_ROOT, require_infoext=False)
    except Exception as exc:
        timestamp = utc_timestamp()
        write_last_attempt(
            audit_file,
            {
                "started_at": timestamp,
                "updated_at": timestamp,
                "finished_at": timestamp,
                "status": "failed",
                "phase": "configuration",
                "error_type": exc.__class__.__name__,
                "elapsed_seconds": 0.0,
            },
        )
        print("InfoExt scheduled run could not load its configuration.", file=sys.stderr)
        return 2
    return run_scheduled_check(settings, audit_file=audit_file)


if __name__ == "__main__":
    raise SystemExit(main())
