import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from common import sqlite as common_sqlite
from telegram_connector import telegram_history_client as history
from telegram_connector.tests.test_analysis_budget import admit_budget_job


def reporter():
    path = Path(__file__).resolve().parents[1] / "scripts/report_analysis.py"
    spec = importlib.util.spec_from_file_location("analysis_reporter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_public_entry_uses_outer_ttl_before_opening_storage(tmp_path, monkeypatch):
    module = reporter()
    calls = []
    monkeypatch.setattr(module.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("Supervisor must not open the database")))

    def supervise(command, **options):
        calls.append((command, options))
        return 124

    monkeypatch.setattr(module.ttl_runner, "run_with_ttl", supervise)
    assert module.main(["--db", str(tmp_path / "unused.sqlite3"), "--job-id", "fixture"]) == 124
    command, options = calls[0]
    assert "--worker-inner" in command and options["timeout_seconds"] > 0
    assert options["timeout_reason"] == "analysis_report_ttl_expired"
    assert not options["use_caffeinate"]
    assert not any("OPENAI" in key or "TELEGRAM" in key for key in options["env"])


def test_report_reader_does_not_migrate_or_mutate_the_database(tmp_path):
    path = tmp_path / "history.sqlite3"
    connection = common_sqlite.connect_sqlite(path)
    try:
        history.init_db(connection)
        _, job = admit_budget_job(connection)
    finally:
        connection.close()
    before = path.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts/report_analysis.py"
    completed = subprocess.run([sys.executable, str(script), "--db", str(path), "--job-id", job["job_id"]],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["job_id"] == job["job_id"]
    assert result["cost"]["usd"] is None
    assert path.read_bytes() == before
