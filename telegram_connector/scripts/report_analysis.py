"""Read one analytic trace without initializing storage or making API calls."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from analysis_store import AnalysisStore
from analysis_telemetry import summarize_job
from common import process as common_process
from common import ttl_runner


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--pricing", type=Path,
                        help="Reviewed per-million USD rates; omission leaves cost unknown")
    parser.add_argument("--worker-inner", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.worker_inner:
        policy = common_process.load_process_config()
        command = [sys.executable, str(Path(__file__).resolve()), "--db", str(args.db),
                   "--job-id", args.job_id, "--worker-inner"]
        if args.pricing:
            command += ["--pricing", str(args.pricing)]
        return ttl_runner.run_with_ttl(command,
            timeout_seconds=policy.default_run_total_timeout_seconds,
            grace_seconds=policy.default_termination_grace_seconds,
            poll_interval_seconds=policy.poll_interval_seconds,
            timeout_exit_code=policy.timeout_exit_code, term_signal=policy.term_signal,
            kill_signal=policy.kill_signal, audit_file=None, timeout_reason="analysis_report_ttl_expired", use_caffeinate=False,
            sleep_policy="interrupt", env={key: value for key, value in os.environ.items()
                if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "PYTHONDONTWRITEBYTECODE"}})
    path = args.db.expanduser().resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        pricing = json.loads(args.pricing.read_text()) if args.pricing else None
        result = summarize_job(AnalysisStore(connection), args.job_id, pricing=pricing)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
