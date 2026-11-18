import asyncio
import errno
from pathlib import Path
import tomllib

import pytest

from telegram_connector import telegram_history_client as history
from telegram_connector import history_session


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


def install_host_sleeps(monkeypatch, sleeps):
    samples = iter(sleeps)
    clock = {"continuous": 0.0, "awake": 0.0}

    async def poll(interval):
        try:
            slept = next(samples)
        except StopIteration:
            raise asyncio.CancelledError from None
        clock["awake"] += interval
        clock["continuous"] += interval + slept

    monkeypatch.setattr(history_session.asyncio, "sleep", poll)
    monkeypatch.setattr(history_session.common_process, "continuous_time", lambda: clock["continuous"])
    monkeypatch.setattr(history_session.common_process, "awake_time", lambda: clock["awake"])


@pytest.mark.parametrize("sleeps,expected", [
    ([0, 0, 0], 0),
    ([4, 0, 0], 0),
    ([0, 91.19, 0, 0], 1),
    ([6, 0, 7, 0], 2),
])
def test_resume_ping_only_after_each_host_sleep(runtime, monkeypatch, capsys, sleeps, expected):
    install_host_sleeps(monkeypatch, sleeps)
    requests = []

    async def client(request):
        requests.append(request)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(history_session._ping_after_host_sleep(client, runtime))
    assert len(requests) == expected
    assert all(type(request).__name__ == "PingRequest" for request in requests)
    assert len({request.ping_id for request in requests}) == expected
    log = capsys.readouterr().err
    assert log.count("resume ping started") == expected
    assert log.count("outcome=succeeded") == expected


@pytest.mark.parametrize("outcome", ["reset", "timeout"])
def test_resume_ping_failure_is_logged_without_retry(runtime, monkeypatch, capsys, outcome):
    install_host_sleeps(monkeypatch, [10, 0, 0])
    calls = []

    async def client(request):
        calls.append(request)
        if outcome == "reset":
            raise ConnectionResetError("secret network details")
        await asyncio.Event().wait()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(history_session._ping_after_host_sleep(client, runtime))
    assert len(calls) == 1
    log = capsys.readouterr().err
    assert "outcome=failed" in log
    assert f"error_type={'ConnectionResetError' if outcome == 'reset' else 'TimeoutError'}" in log
    assert "secret" not in log


@pytest.mark.parametrize("exit_mode", ["normal", "error", "cancel"])
def test_session_stops_pending_resume_ping_before_client_cleanup(runtime, tmp_path, monkeypatch, exit_mode):
    install_host_sleeps(monkeypatch, [10])
    monkeypatch.setattr(history_session.history_settings, "SESSION_DIR", tmp_path)
    events = []

    class Client:
        def __init__(self):
            self.pinging = asyncio.Event()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            assert events == ["ping_cancelled"]
            events.append("client_closed")

        async def __call__(self, request):
            self.pinging.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("ping_cancelled")

    async def run():
        client = Client()

        async def open_client(*args):
            return client

        monkeypatch.setattr(history_session, "open_telethon_client", open_client)

        async def use_session():
            async with history_session.open_telethon_session(runtime, "user"):
                await client.pinging.wait()
                if exit_mode == "error":
                    raise ValueError("body error")
                if exit_mode == "cancel":
                    await asyncio.Event().wait()

        task = asyncio.create_task(use_session())
        await client.pinging.wait()
        if exit_mode == "cancel":
            task.cancel()
        if exit_mode == "normal":
            await task
        else:
            with pytest.raises(ValueError if exit_mode == "error" else asyncio.CancelledError):
                await task
        fd = await history_session._acquire_session_lock(runtime, "user")
        history_session.os.close(fd)
        assert not (asyncio.all_tasks() - {asyncio.current_task()})

    asyncio.run(run())
    assert events == ["ping_cancelled", "client_closed"]


def test_managed_session_keeps_analytics_request_ownership(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(history_session.history_settings, "SESSION_DIR", tmp_path)
    events = []

    async def forbidden_monitor(*args):
        pytest.fail("Managed analytics must retain ownership of network requests")

    class Client:
        async def disconnect(self):
            events.append("client_closed")

    async def open_client(*args, **kwargs):
        assert kwargs == {"managed_requests": True}
        return Client()

    monkeypatch.setattr(history_session, "open_telethon_client", open_client)
    monkeypatch.setattr(history_session, "_ping_after_host_sleep", forbidden_monitor)

    async def run():
        async with history_session.open_telethon_session(runtime, "user", managed_requests=True):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert events == ["client_closed"]


def test_resume_ping_cleanup_failure_keeps_session_locked(runtime, tmp_path, monkeypatch, capsys):
    install_host_sleeps(monkeypatch, [10])
    monkeypatch.setattr(history_session.history_settings, "SESSION_DIR", tmp_path)
    acquired = []
    original_acquire = history_session._acquire_session_lock

    async def capture_lock(*args):
        fd = await original_acquire(*args)
        acquired.append(fd)
        return fd

    class Client:
        def __init__(self):
            self.pinging = asyncio.Event()
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            self.closed = True

        async def __call__(self, request):
            self.pinging.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise RuntimeError("secret cancellation details")

    async def run():
        client = Client()

        async def open_client(*args):
            return client

        monkeypatch.setattr(history_session, "open_telethon_client", open_client)
        monkeypatch.setattr(history_session, "_acquire_session_lock", capture_lock)
        with pytest.raises(history_session.AsyncCleanupError):
            async with history_session.open_telethon_session(runtime, "user"):
                await client.pinging.wait()
        assert client.closed
        with history_session.canonical_session_identity(runtime, "user").with_suffix(".session.lock").open("a") as lock:
            with pytest.raises(BlockingIOError):
                history_session.fcntl.flock(lock.fileno(), history_session.fcntl.LOCK_EX | history_session.fcntl.LOCK_NB)
        assert not (asyncio.all_tasks() - {asyncio.current_task()})

    try:
        asyncio.run(run())
    finally:
        for fd in acquired:
            history_session.os.close(fd)
    log = capsys.readouterr().err
    assert "error_type=AsyncCleanupError" in log
    assert "secret" not in log
