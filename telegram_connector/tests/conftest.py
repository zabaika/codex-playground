"""Keep misplaced runtime mocks from touching operator data or secret backends."""
from pathlib import Path
import ipaddress
import socket
import subprocess
from urllib.parse import unquote, urlsplit

import pytest

from common import sqlite as common_sqlite


@pytest.fixture(autouse=True)
def isolate_operator_runtime(monkeypatch):
    operator_data = (Path(__file__).resolve().parents[1] / "data").resolve()
    connect = common_sqlite.connect_sqlite
    run = subprocess.run
    connect_socket = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def isolated_connect(target, *args, **kwargs):
        value = str(target)
        if value != ":memory:":
            path = unquote(urlsplit(value).path) if value.startswith("file:") else value
            if Path(path).expanduser().resolve().is_relative_to(operator_data):
                raise AssertionError("Fixture attempted to access operator history data")
        return connect(target, *args, **kwargs)

    def isolated_run(command, *args, **kwargs):
        executable = command[0] if isinstance(command, (list, tuple)) else command.split()[0]
        if Path(str(executable)).name in {"security", "op"}:
            raise AssertionError("Fixture attempted to resolve real operator secrets")
        return run(command, *args, **kwargs)

    def local_connection(sock, address, operation):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            host = address[0]
            if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
                raise AssertionError("Fixture attempted an external network connection")
        return operation(sock, address)

    monkeypatch.setattr(common_sqlite, "connect_sqlite", isolated_connect)
    monkeypatch.setattr(subprocess, "run", isolated_run)
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address:
                        local_connection(sock, address, connect_socket))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda sock, address:
                        local_connection(sock, address, connect_ex))
