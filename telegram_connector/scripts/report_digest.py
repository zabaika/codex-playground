"""Read retained digest telemetry without migrations, secrets or API requests."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(APP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(APP_DIR.parent))

from common import process as common_process
from common import ttl_runner
from digest_telemetry import summarize_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--worker-inner', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.worker_inner:
        policy = common_process.load_process_config()
        command = [sys.executable, str(Path(__file__).resolve()),
                   '--db', str(args.db), '--worker-inner']
        if args.run_id:
            command += ['--run-id', args.run_id]
        if args.audit:
            command += ['--audit', str(args.audit)]
        return ttl_runner.run_with_ttl(command,
            timeout_seconds=policy.default_run_total_timeout_seconds,
            grace_seconds=policy.default_termination_grace_seconds,
            poll_interval_seconds=policy.poll_interval_seconds,
            timeout_exit_code=policy.timeout_exit_code, term_signal=policy.term_signal,
            kill_signal=policy.kill_signal, audit_file=None,
            timeout_reason='digest_report_ttl_expired', use_caffeinate=False,
            sleep_policy='interrupt', env={key: value for key, value in os.environ.items()
                if key in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'PYTHONDONTWRITEBYTECODE'}})
    path = args.db.expanduser().resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        audit = json.loads(args.audit.read_text()) if args.audit else None
        print(json.dumps(summarize_run(conn, args.run_id, audit), ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
