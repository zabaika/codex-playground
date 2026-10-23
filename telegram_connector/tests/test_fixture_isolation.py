from pathlib import Path
import subprocess
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from common import sqlite as common_sqlite


@pytest.mark.parametrize("uri", [False, True])
def test_operator_history_is_blocked_before_sqlite_open(uri):
    database = Path(__file__).resolve().parents[1] / "data/telegram_history.sqlite3"
    target = database.as_uri() + "?mode=ro" if uri else database
    with patch("sqlite3.connect", side_effect=AssertionError("SQLite must not be called")) as connect:
        with pytest.raises(AssertionError, match="operator history data"):
            common_sqlite.connect_sqlite(target)
    connect.assert_not_called()


@pytest.mark.parametrize("command", [["security", "find-generic-password"], ["op", "read"]])
def test_real_secret_backend_is_blocked_before_process_start(command):
    with patch("subprocess.Popen", side_effect=AssertionError("Backend must not start")) as process:
        with pytest.raises(AssertionError, match="real operator secrets"):
            subprocess.run(command)
    process.assert_not_called()


def test_temporary_fixture_database_remains_available(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / "fixture.sqlite3")
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_external_connection_is_blocked_before_real_socket_operation(method):
    # A fake receiver cannot invoke the real C socket operation even if a guard
    # regresses; this failure fixture never opens a network connection.
    sock = SimpleNamespace(family=socket.AF_INET)
    with pytest.raises(AssertionError, match="external network connection"):
        getattr(socket.socket, method)(sock, ("203.0.113.1", 443))
