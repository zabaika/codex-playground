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
SLEEP_POLICIES = ("interrupt", "pause")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process
from common.json_io import write_json_atomic


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
    parser.add_argument("--sleep-policy", choices=SLEEP_POLICIES, default="interrupt",
                        help="interrupt: terminate after host sleep; pause: exclude sleep from TTL and resume")
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
    write_json_atomic(audit_path, payload)


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
    deadline = common_process.continuous_time() + timeout_seconds
    while common_process.continuous_time() < deadline:
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
        [caffeinate_path, "-i", "-s", "-w", str(child_pid)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
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
    sleep_policy: str = "interrupt",
) -> int:
    if sleep_policy not in SLEEP_POLICIES:
        raise ValueError(f"Unknown sleep policy: {sleep_policy}")
    config = common_process.load_process_config()
    started_continuous = common_process.continuous_time()
    started_awake = common_process.awake_time()
    ttl_clock = common_process.awake_time if sleep_policy == "pause" else common_process.continuous_time
    started_ttl = started_awake if sleep_policy == "pause" else started_continuous
    caffeinate_info: dict[str, Any] = {"requested": use_caffeinate, "flags": ["-i", "-s", "-w"], "status": "disabled"}
    child = subprocess.Popen(list(command), start_new_session=True)
    process_group_id = os.getpgid(child.pid)
    caffeinate_proc = None
    if use_caffeinate:
        try:
            caffeinate_proc = spawn_caffeinate(child.pid)
            caffeinate_info.update(status="started" if caffeinate_proc else "unavailable",
                                   pid=caffeinate_proc.pid if caffeinate_proc else None)
        except OSError as exc:
            caffeinate_info.update(status="failed", error_type=type(exc).__name__)
            print(f"ttl_runner: caffeinate could not start ({type(exc).__name__})", file=sys.stderr)

    def publish_diagnostics() -> None:
        if audit_file is not None:
            payload = load_audit_payload(audit_file)
            payload.update(caffeinate=caffeinate_info,
                           sleep_policy=sleep_policy,
                           ttl_elapsed_seconds=round(ttl_clock() - started_ttl, 3),
                           elapsed_seconds=round(common_process.continuous_time() - started_continuous, 3),
                           sleep_seconds=round(common_process.sleep_elapsed(started_continuous, started_awake), 3))
            write_audit_payload(audit_file, payload)

    def finish_caffeinate() -> None:
        if caffeinate_proc is not None:
            early_exit = caffeinate_proc.poll()
            cleanup_caffeinate(caffeinate_proc)
            caffeinate_info.update(status="exited" if early_exit is not None else "stopped",
                                   exit_code=caffeinate_proc.returncode)
            if caffeinate_proc.stderr is not None:
                stderr = caffeinate_proc.stderr.read().strip()
                caffeinate_proc.stderr.close()
                if stderr:
                    caffeinate_info["stderr"] = stderr
                    print(f"ttl_runner: caffeinate: {stderr}", file=sys.stderr, flush=True)
        publish_diagnostics()

    try:
        publish_diagnostics()
        deadline = started_ttl + timeout_seconds
        interrupted_by_sleep = False

        while True:
            return_code = child.poll()
            if return_code is not None:
                return return_code
            interrupted_by_sleep = (
                sleep_policy == "interrupt"
                and common_process.sleep_elapsed(started_continuous, started_awake) >= config.sleep_interruption_threshold_seconds
            )
            if interrupted_by_sleep or ttl_clock() >= deadline:
                break
            if caffeinate_proc is not None and caffeinate_proc.poll() is not None and caffeinate_info["status"] == "started":
                caffeinate_info.update(status="exited_early", exit_code=caffeinate_proc.returncode)
                print(f"ttl_runner: caffeinate exited during the worker (code {caffeinate_proc.returncode})", file=sys.stderr, flush=True)
                publish_diagnostics()
            time.sleep(poll_interval_seconds)

        reason = "host_sleep_interrupted" if interrupted_by_sleep else timeout_reason
        print(
            f"ttl_runner: {reason} for pid={child.pid} pgid={process_group_id}",
            file=sys.stderr,
            flush=True,
        )
        mark_timeout_audit(
            audit_file,
            timeout_seconds=timeout_seconds,
            grace_seconds=grace_seconds,
            reason=reason,
            pid=child.pid,
            process_group_id=process_group_id,
        )
        if interrupted_by_sleep and audit_file is not None:
            payload = load_audit_payload(audit_file)
            payload.update(status="interrupted", reason=reason)
            write_audit_payload(audit_file, payload)
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
        try:
            child.wait(timeout=max(1.0, grace_seconds))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        return config.sleep_interruption_exit_code if interrupted_by_sleep else timeout_exit_code
    finally:
        # Audit/clock failures must not leave workers or sleep inhibitors alive.
        try:
            if child.poll() is None:
                send_signal_to_process_group(process_group_id, kill_signal)
                child.wait(timeout=max(1.0, grace_seconds))
        finally:
            finish_caffeinate()


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
        sleep_policy=args.sleep_policy,
    )


if __name__ == "__main__":
    sys.exit(main())
