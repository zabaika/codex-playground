import asyncio
import errno
from pathlib import Path
import tomllib

import pytest

from telegram_connector import telegram_history_client as history


@pytest.fixture
def runtime():
    return history.RuntimeConfig(
        db_path=Path("unused.sqlite3"), media_root=Path("unused-media"),
        user_session_name="user", bot_session_name="bot", api_id="1", api_hash="secret-hash",
        phone="secret-phone", bot_token="secret-token", user_password="secret-password",
        tesseract_binary="tesseract", vision_prompt="", sync_batch_size=1,
        default_auth_mode="user", public_auth_mode="bot", private_auth_mode="user", default_channels=[],
        connection_timeout_seconds=0.01, connection_retry_backoff_seconds=0,
        connection_cleanup_timeout_seconds=0.05,
    )


def install_clients(monkeypatch, outcomes, *, cleanup_error=None):
    clients = []

    class Client:
        def __init__(self, *args):
            assert all(c.closed for c in clients)
            self.outcome = outcomes[len(clients)]
            self.closed = False
            clients.append(self)

        async def start(self, **kwargs):
            if callable(self.outcome):
                await self.outcome()
            if self.outcome == "hang":
                await asyncio.Event().wait()
            if isinstance(self.outcome, BaseException):
                raise self.outcome

        async def disconnect(self):
            if cleanup_error:
                raise cleanup_error
            self.closed = True

    monkeypatch.setattr(history.history_session, "require_telethon", lambda: Client)
    return clients


def test_timeout_reconnects_with_fresh_client(runtime, monkeypatch, capsys):
    clients = install_clients(monkeypatch, ["hang", "ok"])
    result = asyncio.run(history.open_telethon_client(runtime, "user"))
    assert result is clients[1]
    assert clients[0].closed
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert "timeout (attempts: 1/3)" in lines[0]
    assert "recovered (attempts: 2/3)" in lines[1]


def test_initial_success_is_silent(runtime, monkeypatch, capsys):
    clients = install_clients(monkeypatch, ["ok"])
    assert asyncio.run(history.open_telethon_client(runtime, "bot")) is clients[0]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("error,retryable", [
    (ConnectionResetError(errno.ECONNRESET, "secret"), True),
    (ConnectionError("secret"), True),
    (OSError(errno.EHOSTUNREACH, "secret"), True),
    (PermissionError(errno.EACCES, "secret"), False),
    (ValueError("secret"), False),
])
def test_error_boundary_and_redaction(runtime, monkeypatch, capsys, error, retryable):
    clients = install_clients(monkeypatch, [error] * 3)
    with pytest.raises(history.TelegramConnectionError) as caught:
        asyncio.run(history.open_telethon_client(runtime, "user"))
    expected = 3 if retryable else 1
    assert len(clients) == caught.value.attempts == expected
    assert all(c.closed for c in clients)
    assert len(capsys.readouterr().err.splitlines()) == expected
    assert "secret" not in caught.value.operator_summary()


def test_failed_cleanup_stops_retries(runtime, monkeypatch):
    clients = install_clients(monkeypatch, ["hang"], cleanup_error=TimeoutError())
    with pytest.raises(history.TelegramConnectionError) as caught:
        asyncio.run(history.open_telethon_client(runtime, "user"))
    assert caught.value.attempts == len(clients) == 1
    assert "cleanup_error" in caught.value.operator_summary()


def test_start_cancellation_cleanup_exception_stops_retries(runtime, monkeypatch):
    async def broken_start():
        try:
            await asyncio.Event().wait()
        finally:
            raise TimeoutError("secret cleanup details")

    clients = install_clients(monkeypatch, [broken_start])
    with pytest.raises(history.TelegramConnectionError) as caught:
        asyncio.run(history.open_telethon_client(runtime, "user"))
    assert caught.value.attempts == len(clients) == 1
    assert clients[0].closed
    assert "cleanup_error" in caught.value.operator_summary()
    assert "secret" not in caught.value.operator_summary()


def test_cancellation_closes_connection_without_retry(runtime, monkeypatch):
    clients = install_clients(monkeypatch, [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(history.open_telethon_client(runtime, "user"))
    assert len(clients) == 1 and clients[0].closed


def test_connection_budgets_are_configurable(monkeypatch):
    config = tomllib.loads((Path(history.__file__).parent / "config/runtime.example.toml").read_text())
    config["telethon"].update(
        connection_timeout_seconds=17, connection_attempts=2,
        connection_retry_backoff_seconds=0, connection_cleanup_timeout_seconds=4,
    )
    monkeypatch.setattr(history.history_settings, "load_runtime_config", lambda: config)
    monkeypatch.setattr(history.history_settings, "resolve_secret_value", lambda value, label: "fake")
    loaded = history.resolve_runtime()
    assert (loaded.connection_timeout_seconds, loaded.connection_attempts,
            loaded.connection_retry_backoff_seconds, loaded.connection_cleanup_timeout_seconds) == (17, 2, 0, 4)
