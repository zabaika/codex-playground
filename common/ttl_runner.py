#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Sequence


COMMON_ROOT = Path(__file__).resolve().parent
REPO_ROOT = COMMON_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process


def audit_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a one-shot command with a hard wall-clock TTL and clean process-group shutdown."
    )
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--grace-seconds", type=float, default=None)
    parser.add_argument("--poll-interval-seconds", type=float, default=None)
    parser.add_argument("--timeout-exit-code", type=int, default=None)
    parser.add_argument("--term-signal", default=None)
    parser.add_argument("--kill-signal", default=None)
    parser.add_argument("--audit-file")
    parser.add_argument("--timeout-reason", default="process_ttl_expired")
    parser.add_argument("--use-caffeinate", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after '--'")
    return args


def load_audit_payload(audit_path: Path) -> dict[str, Any]:
    if not audit_path.exists():
        return {}
    try:
        with audit_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_audit_payload(audit_path: Path, payload: dict[str, Any]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = audit_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(audit_path)


def mark_timeout_audit(
    audit_path: Path | None,
    *,
    timeout_seconds: float,
    grace_seconds: float,
    reason: str,
    pid: int,
    process_group_id: int,
) -> None:
    if audit_path is None:
        return
    timestamp = audit_timestamp()
    payload = load_audit_payload(audit_path)
    payload.update(
        {
            "updated_at": timestamp,
            "finished_at": timestamp,
            "status": "timed_out",
            "error": reason,
            "timeout_reason": reason,
            "run_total_timeout_seconds": timeout_seconds,
            "termination_grace_seconds": grace_seconds,
            "timed_out_pid": pid,
            "timed_out_process_group_id": process_group_id,
        }
    )
    write_audit_payload(audit_path, payload)


def process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def send_signal_to_process_group(process_group_id: int, signal_name: str) -> None:
    try:
        os.killpg(process_group_id, common_process.resolve_signal(signal_name))
    except ProcessLookupError:
        return
    except PermissionError:
        return


def wait_for_process_group_exit(process_group_id: int, *, timeout_seconds: float, poll_interval_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_group_exists(process_group_id):
            return True
        time.sleep(poll_interval_seconds)
    return not process_group_exists(process_group_id)


def spawn_caffeinate(child_pid: int) -> subprocess.Popen[str] | None:
    if sys.platform != "darwin":
        return None
    caffeinate_path = "/usr/bin/caffeinate"
    if not os.path.exists(caffeinate_path):
        return None
    return subprocess.Popen(
        [caffeinate_path, "-i", "-w", str(child_pid)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def cleanup_caffeinate(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run_with_ttl(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    grace_seconds: float,
    poll_interval_seconds: float,
    timeout_exit_code: int,
    term_signal: str,
    kill_signal: str,
    audit_file: Path | None,
    timeout_reason: str,
    use_caffeinate: bool,
) -> int:
    child = subprocess.Popen(list(command), start_new_session=True)
    process_group_id = os.getpgid(child.pid)
    caffeinate_proc = spawn_caffeinate(child.pid) if use_caffeinate else None
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        return_code = child.poll()
        if return_code is not None:
            cleanup_caffeinate(caffeinate_proc)
            return return_code
        time.sleep(poll_interval_seconds)

    print(
        f"ttl_runner: hard TTL expired after {timeout_seconds:g}s for pid={child.pid} pgid={process_group_id}",
        file=sys.stderr,
        flush=True,
    )
    mark_timeout_audit(
        audit_file,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
        reason=timeout_reason,
        pid=child.pid,
        process_group_id=process_group_id,
    )
    send_signal_to_process_group(process_group_id, term_signal)
    if not wait_for_process_group_exit(
        process_group_id,
        timeout_seconds=grace_seconds,
        poll_interval_seconds=max(0.05, min(poll_interval_seconds, 0.25)),
    ):
        print(
            f"ttl_runner: process group {process_group_id} ignored {term_signal}, escalating to {kill_signal}",
            file=sys.stderr,
            flush=True,
        )
        send_signal_to_process_group(process_group_id, kill_signal)
        wait_for_process_group_exit(
            process_group_id,
            timeout_seconds=max(1.0, grace_seconds),
            poll_interval_seconds=max(0.05, min(poll_interval_seconds, 0.25)),
        )
    cleanup_caffeinate(caffeinate_proc)
    try:
        child.wait(timeout=max(1.0, grace_seconds))
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    return timeout_exit_code


def main() -> int:
    args = parse_args()
    config = common_process.load_process_config()
    timeout_seconds = args.timeout_seconds or config.default_run_total_timeout_seconds
    grace_seconds = args.grace_seconds or config.default_termination_grace_seconds
    poll_interval_seconds = args.poll_interval_seconds or config.poll_interval_seconds
    timeout_exit_code = args.timeout_exit_code if args.timeout_exit_code is not None else config.timeout_exit_code
    term_signal = args.term_signal or config.term_signal
    kill_signal = args.kill_signal or config.kill_signal
    audit_file = Path(args.audit_file).expanduser() if args.audit_file else None

    return run_with_ttl(
        args.command,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout_exit_code=timeout_exit_code,
        term_signal=term_signal,
        kill_signal=kill_signal,
        audit_file=audit_file,
        timeout_reason=args.timeout_reason,
        use_caffeinate=args.use_caffeinate,
    )


if __name__ == "__main__":
    sys.exit(main())
