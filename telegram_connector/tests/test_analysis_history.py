import copy
import asyncio
import inspect
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import tomllib

import pytest

from common import sqlite as common_sqlite
from telegram_connector import telegram_analysis as analysis
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore, StaleGenerationError


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def runtime(tmp_path):
    return history.RuntimeConfig(
        db_path=tmp_path / "history.sqlite3", media_root=tmp_path / "media",
        user_session_name=str(tmp_path / "user"), bot_session_name=str(tmp_path / "bot"),
        api_id="1", api_hash="fixture-api-hash", phone="fixture-phone", bot_token="fixture-token",
        user_password="", tesseract_binary="tesseract", vision_prompt="", sync_batch_size=2,
        default_auth_mode="user", public_auth_mode="bot", private_auth_mode="user", default_channels=[],
        connection_timeout_seconds=0.2, connection_attempts=3, connection_retry_backoff_seconds=0,
        connection_cleanup_timeout_seconds=0.05,
    )


@pytest.fixture
def store():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    try:
        yield AnalysisStore(conn)
    finally:
        conn.close()


@pytest.fixture
def job(store):
    config_dir = history.APP_DIR / "config"
    config = tomllib.loads((config_dir / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    event = {"update_id": 10, "message": {"chat": {"id": 100}, "from": {"id": 7},
                                          "text": "/topics @one,@two since=week until=today"}}
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999",
                                           now=NOW, config_dir=config_dir)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    state = {"history": {"sources": {"11": {"mode": "backfill", "stop_id": None, "cursor": 0,
                                                "state": "running", "returned_messages": 0}}}}
    store.save_checkpoint(running["job_id"], state, generation=running["generation"], max_bytes=10000)
    return store.get_job(running["job_id"])


def entity(peer=11):
    return SimpleNamespace(id=peer, username="fixture", title="Fixture channel")


def write(message_id, *, peer=11, text=None):
    message = SimpleNamespace(id=message_id, date=NOW, message=text or f"message {message_id}",
                              sender_id=7, media=None, edit_date=None)
    return history.StagedMessageWrite(entity(peer), message, None, None, None, None)


def commit(store, job, writes, *, expected=0, cursor=90, returned=None, complete=False, peer=11):
    function = getattr(history, "commit_analysis_history_page", None)
    assert callable(function), "History rows, cumulative download count and cursor must share a transaction"
    return function(store, job["job_id"], generation=job["generation"], entity=entity(peer), writes=writes,
                    expected_cursor=expected, next_cursor=cursor,
                    returned_messages=len(writes) if returned is None else returned, complete=complete)


def source(store, job, peer=11):
    return store.get_job(job["job_id"])["checkpoint"]["history"]["sources"][str(peer)]


def test_s08_page_sync_contract_commits_rows_cursor_and_count_together(store, job):
    commit(store, job, [write(100), write(99)], cursor=99)
    assert [row[0] for row in store.conn.execute("SELECT message_id FROM messages ORDER BY message_id DESC")] == [100, 99]
    assert source(store, job)["cursor"] == 99 and source(store, job)["state"] == "running"
    assert store.get_job(job["job_id"])["downloaded_messages"] == 2
    assert not store.conn.in_transaction
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_s08_page_sync_contract_failed_cursor_write_rolls_back_messages(store, job):
    store.conn.execute("""CREATE TRIGGER reject_checkpoint BEFORE UPDATE OF checkpoint_json ON analysis_jobs
                         BEGIN SELECT RAISE(ABORT,'fixture checkpoint failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture checkpoint"):
        commit(store, job, [write(100)], cursor=100)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert source(store, job)["cursor"] == 0
    assert store.get_job(job["job_id"])["downloaded_messages"] == 0
    assert not store.conn.in_transaction


def test_s08_page_sync_contract_stale_generation_cannot_write_history(store, job):
    stale = dict(job, generation=job["generation"] - 1)
    with pytest.raises(StaleGenerationError):
        commit(store, stale, [write(100)], cursor=100)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_s08_page_sync_contract_replay_and_nonadvancing_cursor_are_rejected(store, job):
    commit(store, job, [write(100)], cursor=100)
    before = store.get_job(job["job_id"])
    for expected, cursor in ((0, 100), (100, 100), (100, 101)):
        with pytest.raises((ValueError, StaleGenerationError)):
            commit(store, job, [write(99)], expected=expected, cursor=cursor)
    assert store.get_job(job["job_id"]) == before
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_s08_page_sync_contract_existing_and_filtered_api_messages_count_toward_shared_cap(store, job):
    from telegram_connector.analysis_store import canonical_json
    snapshot = copy.deepcopy(job["request"])
    snapshot["settings"]["analysis"]["max_messages"] = 4
    snapshot["settings"]["sync"]["sync_limit"] = 0
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(snapshot), job["job_id"]))
    history.upsert_channel(store.conn, entity())
    history.upsert_message(store.conn, entity(), write(100).message, None, None, None, None)
    # One existing message plus two API messages outside the selected range.
    commit(store, job, [write(100)], cursor=98, returned=3)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 3
    with pytest.raises(ValueError, match="download"):
        commit(store, job, [write(97)], expected=98, cursor=96, returned=2)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 3
    assert source(store, job)["cursor"] == 98


def test_s08_page_sync_contract_update_keeps_initial_stop_id_and_marks_only_finished_load(store, job):
    checkpoint = copy.deepcopy(job["checkpoint"])
    checkpoint["history"]["sources"]["11"].update(mode="update", stop_id=80)
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    commit(store, job, [write(100), write(99)], cursor=99)
    assert source(store, job)["stop_id"] == 80
    row = store.conn.execute("SELECT last_tail_at FROM sync_state WHERE channel_id=11").fetchone()
    assert row is None or row[0] is None
    commit(store, job, [write(98)], expected=99, cursor=98, complete=True)
    state = source(store, job)
    assert state["stop_id"] == 80 and state["state"] == "complete"
    assert state["returned_messages"] == 3
    assert store.conn.execute("SELECT last_tail_at FROM sync_state WHERE channel_id=11").fetchone()[0]


@pytest.mark.parametrize("failure", ["cross_peer", "negative_count", "cancelled"])
def test_s08_page_sync_contract_rejects_invalid_page_before_commit(store, job, failure):
    writes = [write(100, peer=12 if failure == "cross_peer" else 11)]
    returned = -1 if failure == "negative_count" else 1
    if failure == "cancelled":
        store.request_cancel(job["job_id"], now=NOW)
    with pytest.raises((ValueError, StaleGenerationError, InterruptedError)):
        commit(store, job, writes, cursor=100, returned=returned)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.parametrize("managed", [False, True])
def test_s08_page_sync_contract_sdk_retry_owner_is_explicit(runtime, monkeypatch, managed):
    assert "managed_requests" in inspect.signature(history.open_telethon_client).parameters, "Analytics must own request retries and FloodWait without changing legacy defaults"
    clients = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.options = kwargs
            clients.append(self)

        async def start(self, **kwargs):
            pass

        async def disconnect(self):
            pass

        async def connect(self):
            await self.start()

        async def get_me(self):
            return SimpleNamespace(id=7)

    monkeypatch.setattr(history, "require_telethon", lambda: Client)
    client = asyncio.run(history.open_telethon_client(runtime, "user", managed_requests=managed))
    if managed:
        assert client.options == {"request_retries": 0, "connection_retries": 0, "retry_delay": 0,
                                  "auto_reconnect": False, "flood_sleep_threshold": 0,
                                  "raise_last_call_error": True, "receive_updates": False}
    else:
        assert client.options == {}


@pytest.mark.parametrize("failure", ["network", "server", "migrate"])
def test_s08_page_sync_contract_managed_startup_retry_keeps_fresh_client_cleanup_guard(runtime, monkeypatch, failure):
    from telethon import errors
    problem = {"network": ConnectionError("fixture transient error"),
               "server": errors.ServerError(request=None, message="fixture server error"),
               "migrate": errors.NetworkMigrateError(request=None, capture=2)}[failure]
    assert "managed_requests" in inspect.signature(history.open_telethon_client).parameters
    clients = []

    class Client:
        def __init__(self, *args, **kwargs):
            assert all(client.closed for client in clients)
            self.closed = False
            self.options = kwargs
            clients.append(self)

        async def start(self, **kwargs):
            if len(clients) == 1:
                raise problem

        async def disconnect(self):
            self.closed = True

        async def connect(self):
            await self.start()

        async def get_me(self):
            return SimpleNamespace(id=7)

    monkeypatch.setattr(history, "require_telethon", lambda: Client)
    client = asyncio.run(history.open_telethon_client(runtime, "user", managed_requests=True))
    assert len(clients) == 2 and clients[0].closed and client is clients[1]
    assert clients[0].options == clients[1].options


def session(runtime, auth="user", *, managed=False):
    factory = getattr(history, "open_telethon_session", None)
    assert callable(factory), "All consumers must lease the canonical Telethon session for the entire client lifetime"
    return factory(runtime, auth, managed_requests=managed)


def context_client(monkeypatch, *, cleanup=None):
    calls = []

    class Client:
        entered = False
        exited = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, *error):
            if cleanup is not None:
                await cleanup()
            self.exited = True
            self.error = error

        async def disconnect(self):
            if cleanup is not None:
                await cleanup()
            self.exited = True

    async def open_client(runtime, auth, **kwargs):
        client = Client()
        calls.append((auth, kwargs, client))
        return client

    monkeypatch.setattr(history, "open_telethon_client", open_client)
    return calls


@pytest.mark.parametrize("second_auth", ["user", "bot"])
def test_s08_page_sync_contract_session_aliases_share_os_lock(runtime, monkeypatch, second_auth):
    calls = context_client(monkeypatch)
    alias = replace(runtime, user_session_name=runtime.user_session_name + ".session",
                    bot_session_name=runtime.user_session_name + ".session")

    async def run():
        async with session(runtime) as client:
            assert client.entered
            with pytest.raises(TimeoutError):
                async with session(alias, second_auth):
                    pytest.fail("Canonical session must have one client owner")
            assert len(calls) == 1
        assert client.exited
        async with session(alias, second_auth):
            assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("managed", [False, True])
def test_s08_page_sync_contract_session_preserves_context_errors_and_profile(runtime, monkeypatch, managed):
    calls = context_client(monkeypatch)

    async def run():
        with pytest.raises(ValueError, match="fixture body"):
            async with session(runtime, managed=managed):
                raise ValueError("fixture body")
        async with session(runtime):
            pass

    asyncio.run(run())
    assert calls[0][1] == ({"managed_requests": True} if managed else {})
    assert calls[0][2].exited
    if not managed:
        assert calls[0][2].error[0] is ValueError


@pytest.mark.parametrize("origin", ["disconnect", "body"])
def test_s08_page_sync_contract_uncertain_cleanup_keeps_session_blocked(runtime, monkeypatch, origin):
    async def hang():
        await asyncio.Event().wait()

    calls = context_client(monkeypatch, cleanup=hang if origin == "disconnect" else None)
    assert callable(getattr(history, "_acquire_session_lock", None)), "Session lease acquisition must be bounded"
    original = history._acquire_session_lock
    held = []

    async def capture(*a, **kw):
        fd = await original(*a, **kw)
        held.append(fd)
        return fd

    monkeypatch.setattr(history, "_acquire_session_lock", capture)

    async def run():
        with pytest.raises(history.AsyncCleanupError):
            async with session(runtime):
                if origin == "body":
                    raise history.AsyncCleanupError("fixture uncertain RPC cancellation")
        with pytest.raises(TimeoutError):
            async with session(runtime):
                pytest.fail("Uncertain cleanup must not permit another client")

    try:
        asyncio.run(run())
        assert len(calls) == 1 and len(held) == 1
        os.fstat(held[0])  # Lease stays owned until fatal worker exit.
    finally:
        for fd in set(held):
            try:
                os.close(fd)  # Isolated fixture teardown; production releases by process exit.
            except OSError:
                pass


def test_s08_page_sync_contract_clean_startup_error_releases_session(runtime, monkeypatch):
    calls = context_client(monkeypatch)
    opened = history.open_telethon_client
    attempts = []

    async def flaky(*a, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("fixture startup error")
        return await opened(*a, **kw)

    monkeypatch.setattr(history, "open_telethon_client", flaky)

    async def run():
        with pytest.raises(ValueError, match="fixture startup"):
            async with session(runtime):
                pass
        async with session(runtime):
            pass

    asyncio.run(run())
    assert len(attempts) == 2 and len(calls) == 1


@pytest.mark.parametrize("entry", ["sync", "export", "digest"])
def test_s08_page_sync_contract_legacy_consumers_use_canonical_session_owner(store, runtime, monkeypatch, tmp_path, entry):
    owner = history
    if entry == "digest":
        monkeypatch.syspath_prepend(str(history.APP_DIR))
        from telegram_connector import telegram_digest as digest
        owner = digest.history_client
    calls = []

    class Client:
        async def get_entity(self, ref):
            return entity()

        def iter_messages(self, *a, **kw):
            async def empty():
                if False:
                    yield None
            return empty()

        async def get_messages(self, *a, **kw):
            message = write(100).message
            message.sender = SimpleNamespace(username="fixture-user", first_name="Fixture")
            return message

    @asynccontextmanager
    async def leased(runtime, auth_mode, **kwargs):
        calls.append((auth_mode, kwargs))
        yield Client()

    monkeypatch.setattr(owner, "open_telethon_session", leased)
    monkeypatch.setattr(owner, "open_telethon_client", lambda *a, **kw: pytest.fail("Consumer bypassed session ownership"))
    if entry == "sync":
        args = SimpleNamespace(limit=2, auth_mode="user", download_media=False, ocr=False, mark_read=False,
                               since="2026-10-16", until="2026-10-16")
        asyncio.run(history.sync_one_channel(store.conn, runtime, args, "backfill", "@one"))
    elif entry == "export":
        asyncio.run(history.fetch_and_export_message(runtime, store.conn, "https://t.me/fixture/100", str(tmp_path)))
    else:
        asyncio.run(digest.run_sync(runtime, channel="@one", since="2026-10-16", until="2026-10-16",
                                   total_limit=2, use_ocr=False, mark_read=False, mode="backfill", auth_mode="user"))
    assert calls == [("user", {})]


def test_s08_page_sync_contract_session_lock_excludes_another_process(runtime, monkeypatch):
    context_client(monkeypatch)
    path = str(history.canonical_session_identity(runtime, "user")) + ".lock"
    code = ("import fcntl,sys;"
            "\nwith open(sys.argv[1],'a+b') as handle:"
            "\n try: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);print('owned')"
            "\n except BlockingIOError: print('busy')")

    def probe():
        result = subprocess.run([sys.executable, "-c", code, path], capture_output=True, text=True, timeout=5, env={})
        assert result.returncode == 0
        return result.stdout.strip()

    async def run():
        async with session(runtime):
            assert probe() == "busy"
        assert probe() == "owned"

    asyncio.run(run())


def test_s08_page_sync_contract_startup_cleanup_error_keeps_lease_until_fatal_exit(runtime, monkeypatch):
    original = history._acquire_session_lock
    held, starts = [], []

    async def capture(*a, **kw):
        fd = await original(*a, **kw)
        held.append(fd)
        return fd

    async def startup(*a, **kw):
        starts.append(1)
        raise history.TelegramConnectionError(history.AsyncCleanupError("fixture uncertain startup"), 1, 3)

    monkeypatch.setattr(history, "_acquire_session_lock", capture)
    monkeypatch.setattr(history, "open_telethon_client", startup)

    async def run():
        with pytest.raises(history.TelegramConnectionError):
            async with session(runtime):
                pass
        with pytest.raises(TimeoutError):
            async with session(runtime):
                pytest.fail("Unsafe startup cleanup cannot be retried with a new client")

    try:
        asyncio.run(run())
        assert len(starts) == len(held) == 1
        os.fstat(held[0])
    finally:
        for fd in set(held):
            try:
                os.close(fd)
            except OSError:
                pass


def test_s08_page_sync_contract_unknown_source_mode_cannot_mark_history_fresh(store, job):
    checkpoint = copy.deepcopy(job["checkpoint"])
    checkpoint["history"]["sources"]["11"]["mode"] = "unknown"
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    with pytest.raises(ValueError, match="mode"):
        commit(store, job, [write(100)], cursor=100, complete=True)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_s08_page_sync_contract_successful_empty_page_finishes_without_fake_message(store, job):
    commit(store, job, [], cursor=0, complete=True)
    assert source(store, job)["state"] == "complete"
    assert store.get_job(job["job_id"])["downloaded_messages"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.parametrize("max_messages,sync_limit", [(4, 2), (2, 4)])
def test_s08_page_sync_contract_positive_sync_limit_and_analysis_limit_both_apply(store, job, max_messages, sync_limit):
    from telegram_connector.analysis_store import canonical_json
    snapshot = copy.deepcopy(job["request"])
    snapshot["settings"]["analysis"]["max_messages"] = max_messages
    snapshot["settings"]["sync"]["sync_limit"] = sync_limit
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(snapshot), job["job_id"]))
    commit(store, job, [write(100), write(99)], cursor=99)
    with pytest.raises(ValueError, match="download"):
        commit(store, job, [write(98)], expected=99, cursor=98)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 2


def test_s08_page_sync_contract_download_cap_is_shared_across_channels(store, job):
    from telegram_connector.analysis_store import canonical_json
    snapshot = copy.deepcopy(job["request"])
    snapshot["settings"]["analysis"]["max_messages"] = 3
    snapshot["settings"]["sync"]["sync_limit"] = 0
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(snapshot), job["job_id"]))
    commit(store, job, [write(100), write(99)], cursor=99, complete=True)
    checkpoint = store.get_job(job["job_id"])["checkpoint"]
    checkpoint["history"]["sources"]["22"] = {"mode": "backfill", "stop_id": None, "cursor": 0,
                                                "state": "running", "returned_messages": 0}
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    with pytest.raises(ValueError, match="download"):
        commit(store, job, [write(100, peer=22), write(99, peer=22)], cursor=99, peer=22)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 2
    assert source(store, job, 22)["cursor"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM messages WHERE channel_id=22").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["text", "empty", "media"])
def test_s08_page_sync_contract_prepared_write_roundtrips_existing_writer(store, monkeypatch, kind):
    import json
    prepare = getattr(history, "prepare_history_write", None)
    apply = getattr(history, "_upsert_prepared_message_no_tx", None)
    assert callable(prepare) and callable(apply), "Durable pending pages need the existing writer's normalized JSON form"
    monkeypatch.setattr(history, "now_utc", lambda: NOW.isoformat())
    item = write(100)
    if kind == "empty":
        item.message.message = ""
    if kind == "media":
        item.message.media = SimpleNamespace()
        item.downloaded_path, item.downloaded_size = "fixture-image.png", 3
    history.upsert_channel(store.conn, item.entity)
    history.upsert_message(store.conn, item.entity, item.message, item.sender_username, item.sender_display_name,
                           item.downloaded_path, item.downloaded_size)
    expected_message = tuple(store.conn.execute("SELECT * FROM messages").fetchone())
    expected_media = [tuple(row) for row in store.conn.execute("SELECT * FROM media_assets")]
    prepared = json.loads(json.dumps(prepare(item), allow_nan=False))
    store.conn.execute("DELETE FROM media_assets")
    store.conn.execute("DELETE FROM messages")
    with common_sqlite.write_tx(store.conn):
        apply(store.conn, prepared)
    assert tuple(store.conn.execute("SELECT * FROM messages").fetchone()) == expected_message
    assert [tuple(row) for row in store.conn.execute("SELECT * FROM media_assets")] == expected_media


def page_policy(store, job, *, cap=20, page=5, flush=2):
    from telegram_connector.analysis_store import canonical_json
    payload = copy.deepcopy(store.get_job(job["job_id"])["request"])
    payload["settings"]["analysis"].update(max_messages=cap, history_page_size=page)
    payload["settings"]["sync"].update(sync_limit=0, batch_size=flush)
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))


def reserve(store, job, limit=5, peer=11):
    function = getattr(history, "reserve_analysis_history_page", None)
    assert callable(function), "Download allowance must be durable before the physical request"
    return function(store, job["job_id"], generation=job["generation"], channel_id=peer, limit=limit)


def stage(store, job, reservation, ids, *, complete=False, peer=11):
    function = getattr(history, "stage_analysis_history_page", None)
    assert callable(function), "Fetched page remainder must survive restart independently of DB flush size"
    items = [{"id": message_id, "row": history.prepare_history_write(write(message_id, peer=peer))} for message_id in ids]
    return function(store, job["job_id"], generation=job["generation"], channel_id=peer,
                    token=reservation["token"], items=items, complete=complete)


def drain(store, job, peer=11):
    function = getattr(history, "drain_analysis_history_page", None)
    assert callable(function), "One DB flush must commit rows and its saved page position atomically"
    return function(store, job["job_id"], generation=job["generation"], entity=entity(peer))


def test_s08_page_sync_contract_pending_page_is_independent_of_flush_size(store, job):
    page_policy(store, job)
    token = reserve(store, job)
    assert token["allowance"] == 5
    stage(store, job, token, [100, 99, 98, 97, 96], complete=True)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    drain(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert source(store, job)["cursor"] == 99
    assert source(store, job)["page"]["position"] == 2
    assert source(store, job)["state"] == "running"
    drain(store, job)
    assert source(store, job)["cursor"] == 97
    drain(store, job)
    assert source(store, job)["cursor"] == 96 and source(store, job)["state"] == "complete"
    assert "page" not in source(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 5
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5


def test_s08_page_sync_contract_pending_page_survives_generation_change_without_refetch(store, job):
    page_policy(store, job)
    token = reserve(store, job)
    stage(store, job, token, [100, 99, 98, 97, 96], complete=True)
    drain(store, job)
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=NOW)
    store.resume_job(job["job_id"], generation=job["generation"], phase="sync", max_queued_jobs=8, now=NOW)
    resumed = store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)
    drain(store, resumed)
    drain(store, resumed)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 5
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5
    assert source(store, job)["state"] == "complete"


def test_s08_page_sync_contract_failed_flush_keeps_received_page_and_accounting(store, job):
    page_policy(store, job)
    stage(store, job, reserve(store, job), [100, 99, 98], complete=True)
    store.conn.execute("""CREATE TRIGGER reject_flush BEFORE UPDATE OF checkpoint_json ON analysis_jobs
                         BEGIN SELECT RAISE(ABORT,'fixture flush failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture flush"):
        drain(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert source(store, job)["page"]["position"] == 0
    assert store.get_job(job["job_id"])["downloaded_messages"] == 3
    store.conn.execute("DROP TRIGGER reject_flush")
    drain(store, job)
    drain(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3


def test_s08_page_sync_contract_lost_response_keeps_reserved_download_allowance(store, job):
    page_policy(store, job, cap=7)
    lost = reserve(store, job)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5
    retry = reserve(store, job)
    assert retry["token"] != lost["token"] and retry["allowance"] == 2
    assert store.get_job(job["job_id"])["checkpoint"]["history"]["unknown_download_messages"] == 5
    with pytest.raises(StaleGenerationError):
        stage(store, job, lost, [100])
    stage(store, job, retry, [100], complete=True)
    drain(store, job)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 6  # 5 unknown + 1 returned.
    assert source(store, job)["returned_messages"] == 1


def test_s08_page_sync_contract_cannot_fetch_while_a_received_page_is_pending(store, job):
    page_policy(store, job)
    stage(store, job, reserve(store, job), [100, 99])
    with pytest.raises(ValueError, match="pending"):
        reserve(store, job)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 2


@pytest.mark.parametrize("bad_ids", [[99, 100], [100, 100], [0]])
def test_s08_page_sync_contract_invalid_page_cannot_release_reserved_budget(store, job, bad_ids):
    page_policy(store, job)
    token = reserve(store, job)
    with pytest.raises(ValueError):
        stage(store, job, token, bad_ids)
    assert source(store, job)["reservation"]["token"] == token["token"]
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5


class PageClient:
    def __init__(self, store, messages, *, outcomes=None):
        self.store, self.messages = store, messages
        self.outcomes = list(outcomes or [])
        self.calls, self.entities = [], []
        self.times = []

    async def get_entity(self, ref):
        assert not self.store.conn.in_transaction
        self.entities.append(ref)
        return entity(22 if ref == "@two" else 11)

    async def get_messages(self, peer, **options):
        from common import process as common_process
        assert not self.store.conn.in_transaction
        self.calls.append((peer.id, options))
        self.times.append(common_process.continuous_time())
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if outcome == "hang":
                await asyncio.Event().wait()
            if isinstance(outcome, BaseException):
                raise outcome
        values = self.messages.get(peer.id, []) if isinstance(self.messages, dict) else self.messages
        selected = [message for message in values if (not options.get("offset_id") or message.id < options["offset_id"])
                    and message.date < options["offset_date"]]
        return selected[:options["limit"]]


def posts(ids):
    result = []
    for message_id in ids:
        message = write(message_id).message
        message.post, message.sender_id = True, None
        result.append(message)
    return result


def load_source(store, job, runtime, client, *, channel="@one", mode="backfill", deadline=None):
    from common import process as common_process
    function = getattr(history, "sync_analysis_source", None)
    assert callable(function), "Analytics needs the real bounded pagination loop, not only storage helpers"
    return asyncio.run(function(store, job["job_id"], runtime, generation=job["generation"], channel=channel,
                                mode=mode, client=client, deadline=deadline or common_process.continuous_time() + 5))


def reset_source_plan(store, job):
    store.save_checkpoint(job["job_id"], {}, generation=job["generation"], max_bytes=10000)


def test_s08_page_sync_contract_loader_paginates_with_independent_db_flush(store, job, runtime, monkeypatch):
    page_policy(store, job)
    reset_source_plan(store, job)
    client = PageClient(store, posts([100, 99, 98, 97, 96]))
    original = history.drain_analysis_history_page
    sizes = []

    def observe(*a, **kw):
        before = store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        result = original(*a, **kw)
        sizes.append(store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] - before)
        return result

    monkeypatch.setattr(history, "drain_analysis_history_page", observe)
    result = load_source(store, job, runtime, client)
    assert result["state"] == "complete" and result["cursor"] == 96
    assert sizes == [2, 2, 1, 0]
    assert len(client.calls) == 2 and [options["offset_id"] for _, options in client.calls] == [0, 96]
    assert all(options["limit"] == 5 and options["wait_time"] == 0 for _, options in client.calls)
    assert all("reply_to" not in options and "search" not in options for _, options in client.calls)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5


def test_s08_page_sync_contract_loader_resume_drains_saved_remainder_before_fetch(store, job, runtime, monkeypatch):
    page_policy(store, job)
    reset_source_plan(store, job)
    client = PageClient(store, posts([100, 99, 98, 97, 96]))
    original = history.drain_analysis_history_page

    def crash(*a, **kw):
        original(*a, **kw)
        raise RuntimeError("fixture crash after committed first flush")

    monkeypatch.setattr(history, "drain_analysis_history_page", crash)
    with pytest.raises(RuntimeError, match="fixture crash"):
        load_source(store, job, runtime, client)
    assert source(store, job)["cursor"] == 99 and len(client.calls) == 1
    monkeypatch.setattr(history, "drain_analysis_history_page", original)
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=NOW)
    store.resume_job(job["job_id"], generation=job["generation"], phase="sync", max_queued_jobs=8, now=NOW)
    resumed = store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)
    assert load_source(store, resumed, runtime, client)["state"] == "complete"
    assert len(client.calls) == 2 and client.calls[-1][1]["offset_id"] == 96
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 5
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5


def test_s08_page_sync_contract_loader_update_preserves_stop_id_after_resume(store, job, runtime, monkeypatch):
    page_policy(store, job, cap=50)
    reset_source_plan(store, job)
    history.upsert_channel(store.conn, entity())
    history.upsert_message(store.conn, entity(), posts([80])[0], None, None, None, None)
    client = PageClient(store, posts(list(range(100, 75, -1))))
    original = history.drain_analysis_history_page

    def crash(*a, **kw):
        original(*a, **kw)
        raise RuntimeError("fixture crash")

    monkeypatch.setattr(history, "drain_analysis_history_page", crash)
    with pytest.raises(RuntimeError, match="fixture crash"):
        load_source(store, job, runtime, client, mode="update")
    assert source(store, job)["stop_id"] == 80 and history.latest_stored_message_id(store.conn, 11) == 100
    monkeypatch.setattr(history, "drain_analysis_history_page", original)
    result = load_source(store, job, runtime, client, mode="update")
    assert result["stop_id"] == 80 and result["state"] == "complete"
    assert store.conn.execute("SELECT MIN(message_id),MAX(message_id),COUNT(*) FROM messages").fetchone()[:] == (80, 100, 21)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 25


def test_s08_page_sync_contract_loader_shares_cap_across_source_order(store, job, runtime):
    page_policy(store, job, cap=7)
    reset_source_plan(store, job)
    client = PageClient(store, {11: posts([100, 99, 98, 97]), 22: posts([100, 99, 98, 97])})
    assert load_source(store, job, runtime, client)["state"] == "complete"
    second = load_source(store, job, runtime, client, channel="@two")
    assert second["state"] == "partial" and second["reason"] == "download_cap"
    assert client.calls[-1][0] == 22 and client.calls[-1][1]["limit"] == 3
    assert store.get_job(job["job_id"])["downloaded_messages"] == 7


@pytest.mark.parametrize("failure", ["network", "server", "migrate"])
def test_s08_page_sync_contract_loader_retry_keeps_unknown_download_cost(store, job, runtime, failure):
    from telethon import errors
    from telegram_connector.analysis_store import canonical_json
    page_policy(store, job)
    reset_source_plan(store, job)
    payload = store.get_job(job["job_id"])["request"]
    payload["settings"]["telethon"]["connection_retry_backoff_seconds"] = 0
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))
    problem = {"network": ConnectionError("fixture reset"),
               "server": errors.ServerError(request=None, message="fixture server error"),
               "migrate": errors.NetworkMigrateError(request=None, capture=2)}[failure]
    client = PageClient(store, posts([100, 99]), outcomes=[problem])
    assert load_source(store, job, runtime, client)["state"] == "complete"
    persisted = store.get_job(job["job_id"])
    unknown = 5 if failure == "network" else 0
    assert persisted["downloaded_messages"] == 2 + unknown
    assert persisted["checkpoint"]["history"].get("unknown_download_messages", 0) == unknown
    assert source(store, job)["returned_messages"] == 2 and len(client.calls) == 3


def test_s08_page_sync_contract_loader_flood_wait_respects_remaining_run_budget(store, job, runtime):
    from telethon import errors
    page_policy(store, job)
    reset_source_plan(store, job)
    client = PageClient(store, [], outcomes=[errors.FloodWaitError(request=None, capture=30)])
    result = load_source(store, job, runtime, client)
    assert result["state"] == "partial" and result["reason"] == "flood_wait_exceeds_deadline"
    assert len(client.calls) == 1 and store.get_job(job["job_id"])["downloaded_messages"] == 0
    assert "reservation" not in source(store, job)


@pytest.mark.parametrize("global_error", [False, True])
def test_s08_page_sync_contract_loader_distinguishes_source_access_from_credentials(store, job, runtime, global_error):
    from telethon import errors
    page_policy(store, job)
    reset_source_plan(store, job)
    error = errors.AuthKeyUnregisteredError(request=None) if global_error else errors.ChannelPrivateError(request=None)
    client = PageClient(store, [], outcomes=[error])
    if global_error:
        with pytest.raises(errors.AuthKeyUnregisteredError):
            load_source(store, job, runtime, client)
    else:
        result = load_source(store, job, runtime, client)
        assert result["state"] == "failed" and result["reason"] == "source_unavailable"
    assert len(client.calls) == 1 and store.get_job(job["job_id"])["downloaded_messages"] == 0


def test_s08_page_sync_contract_loader_request_wait_is_bounded_by_deadline(store, job, runtime):
    from common import process as common_process
    page_policy(store, job)
    reset_source_plan(store, job)
    client = PageClient(store, [], outcomes=["hang"])
    result = load_source(store, job, runtime, client, deadline=common_process.continuous_time() + 0.05)
    assert result["state"] == "partial" and result["reason"] == "run_deadline"
    assert len(client.calls) == 1
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5


@pytest.mark.parametrize("default_auth,expected", [("user", "user"), ("auto", "bot")])
def test_s08_page_sync_contract_loader_uses_managed_session_by_default(store, job, runtime, monkeypatch, default_auth, expected):
    from telegram_connector.analysis_store import canonical_json
    page_policy(store, job)
    reset_source_plan(store, job)
    payload = store.get_job(job["job_id"])["request"]
    payload["settings"]["auth"] = {"default_mode": default_auth, "public_channel_mode": "bot", "private_channel_mode": "user"}
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))
    calls = []

    @asynccontextmanager
    async def leased(runtime, auth, **options):
        calls.append((auth, options))
        yield PageClient(store, [])

    monkeypatch.setattr(history, "open_telethon_session", leased)
    assert load_source(store, job, runtime, None)["state"] == "complete"
    assert calls == [(expected, {"managed_requests": True})]


@pytest.mark.parametrize("authorized", [False, True])
def test_s08_page_sync_contract_managed_user_requires_existing_authorization_without_prompts(runtime, monkeypatch, authorized):
    clients = []

    class Client:
        def __init__(self, *a, **kw):
            self.connected, self.closed = False, False
            clients.append(self)

        async def start(self, **kwargs):
            pytest.fail("Analytics may not initiate interactive phone/OTP login")

        async def connect(self):
            self.connected = True

        async def get_me(self):
            return SimpleNamespace(id=7) if authorized else None

        async def disconnect(self):
            self.closed = True

    monkeypatch.setattr(history, "require_telethon", lambda: Client)
    if authorized:
        assert asyncio.run(history.open_telethon_client(runtime, "user", managed_requests=True)) is clients[0]
        assert clients[0].connected and not clients[0].closed
    else:
        with pytest.raises(history.TelegramConnectionError) as caught:
            asyncio.run(history.open_telethon_client(runtime, "user", managed_requests=True))
        assert isinstance(caught.value.cause, PermissionError)
        assert clients[0].connected and clients[0].closed and len(clients) == 1


def test_s08_page_sync_contract_managed_context_does_not_restart_authenticated_client(runtime, monkeypatch):
    closed = []

    class Client:
        async def __aenter__(self):
            pytest.fail("Native __aenter__ would invoke interactive start again")

        async def disconnect(self):
            closed.append(True)

    async def opened(*a, **kw):
        return Client()

    monkeypatch.setattr(history, "open_telethon_client", opened)

    async def run():
        async with session(runtime, managed=True):
            pass

    asyncio.run(run())
    assert closed == [True]


def test_s08_page_sync_contract_loader_private_numeric_reference_refreshes_dialogs(store, job, runtime):
    from telegram_connector.analysis_store import canonical_json
    page_policy(store, job)
    reset_source_plan(store, job)
    payload = store.get_job(job["job_id"])["request"]
    payload["request"]["channels"] = ["-10011"]
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))

    class PrivateClient(PageClient):
        dialogs_loaded = False

        async def get_entity(self, ref):
            self.entities.append(ref)
            if not self.dialogs_loaded:
                raise ValueError("fixture peer is not cached")
            return entity()

        async def get_dialogs(self):
            assert not store.conn.in_transaction
            self.dialogs_loaded = True
            return []

    client = PrivateClient(store, posts([100, 99]))
    result = load_source(store, job, runtime, client, channel="-10011")
    assert result["state"] == "complete" and client.dialogs_loaded
    assert len(client.entities) == 2
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_s08_page_sync_contract_loader_stops_at_period_and_frozen_cutoff(store, job, runtime):
    from datetime import timedelta
    page_policy(store, job)
    reset_source_plan(store, job)
    messages = posts([101, 100, 99, 98, 97, 96])
    messages[0].date = NOW + timedelta(seconds=1)
    messages[2].date = NOW - timedelta(days=2)
    for message in messages[3:]:
        message.date = NOW - timedelta(days=8)
    client = PageClient(store, messages)
    assert load_source(store, job, runtime, client)["state"] == "complete"
    assert [row[0] for row in store.conn.execute("SELECT message_id FROM messages ORDER BY message_id DESC")] == [100, 99]
    assert len(client.calls) == 1
    assert client.calls[0][1]["offset_date"] == NOW + timedelta(seconds=1)
    assert store.get_job(job["job_id"])["downloaded_messages"] == 5


def zero_backoff(store, job):
    from telegram_connector.analysis_store import canonical_json
    payload = store.get_job(job["job_id"])["request"]
    payload["settings"]["telethon"]["connection_retry_backoff_seconds"] = 0
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))


def test_s08_page_sync_contract_loader_waits_for_short_flood_then_retries_without_charging_rejection(store, job, runtime):
    from telethon import errors
    page_policy(store, job)
    reset_source_plan(store, job)
    zero_backoff(store, job)
    client = PageClient(store, posts([100, 99]), outcomes=[errors.FloodWaitError(request=None, capture=1)])
    assert load_source(store, job, runtime, client)["state"] == "complete"
    assert client.times[1] - client.times[0] >= 0.9
    assert len(client.calls) == 3 and store.get_job(job["job_id"])["downloaded_messages"] == 2
    assert store.get_job(job["job_id"])["checkpoint"]["history"].get("unknown_download_messages", 0) == 0


def test_s08_page_sync_contract_loader_exhausts_attempts_without_advancing_cursor(store, job, runtime):
    page_policy(store, job)
    reset_source_plan(store, job)
    zero_backoff(store, job)
    client = PageClient(store, [], outcomes=[ConnectionError("fixture reset")] * 3)
    result = load_source(store, job, runtime, client)
    assert result["state"] == "partial" and result["reason"] == "request_retries_exhausted"
    assert len(client.calls) == 3 and result["cursor"] == 0
    assert store.get_job(job["job_id"])["downloaded_messages"] == 15
    assert store.get_job(job["job_id"])["checkpoint"]["history"]["unknown_download_messages"] == 15


@pytest.mark.parametrize("ocr", [False, True])
def test_s08_page_sync_contract_sender_and_media_are_prepared_outside_write_transaction(store, job, runtime, monkeypatch, tmp_path, ocr):
    from telegram_connector.analysis_store import canonical_json
    page_policy(store, job)
    reset_source_plan(store, job)
    payload = store.get_job(job["job_id"])["request"]
    payload["settings"]["processing"]["ocr"] = ocr
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))
    message = posts([100])[0]
    message.post, message.sender_id, message.media = False, 7, SimpleNamespace()

    async def sender():
        assert not store.conn.in_transaction
        return SimpleNamespace(username="fixture-user", first_name="Fixture")

    message.get_sender = sender
    monkeypatch.setattr(history, "message_media_kind", lambda value: "photo" if getattr(value, "media", None) else None)

    class MediaClient(PageClient):
        downloads = 0

        async def download_media(self, message, *, file):
            assert not store.conn.in_transaction
            self.downloads += 1
            path = Path(file + ".png")
            path.write_bytes(b"fixture image")
            return str(path)

    client = MediaClient(store, [message])
    assert load_source(store, job, runtime, client)["state"] == "complete"
    assert client.downloads == int(ocr)
    row = store.conn.execute("SELECT sender_username,sender_display_name FROM messages").fetchone()
    assert tuple(row) == ("fixture-user", "Fixture")
    media = store.conn.execute("SELECT local_path,ocr_status FROM media_assets").fetchone()
    assert media[1] == ("pending" if ocr else "skipped")
    assert bool(media[0]) == ocr


def test_s08_page_sync_contract_loader_rejects_scope_expansion_before_network(store, job, runtime):
    client = PageClient(store, [])
    with pytest.raises(ValueError, match="scope"):
        load_source(store, job, runtime, client, channel="@outside")
    assert not client.entities and not client.calls


def tagged_entity(kind):
    return type(kind, (), {"id": 11, "username": "fixture", "title": "Fixture"})()


def test_s08_page_sync_contract_loader_does_not_alias_peer_namespaces_in_legacy_schema(store, job, runtime):
    page_policy(store, job)
    reset_source_plan(store, job)

    class MixedClient(PageClient):
        async def get_entity(self, ref):
            return tagged_entity("Chat" if ref == "@one" else "Channel")

    client = MixedClient(store, posts([100, 99]))
    assert load_source(store, job, runtime, client)["state"] == "complete"
    calls_before = len(client.calls)
    second = load_source(store, job, runtime, client, channel="@two")
    assert second["state"] == "failed" and second["reason"] == "source_identity_conflict"
    assert len(client.calls) == calls_before
    assert store.conn.execute("SELECT channel_type FROM channels WHERE channel_id=11").fetchone()[0] == "Chat"


def test_s08_page_sync_contract_loader_rejects_user_account_as_channel(store, job, runtime):
    page_policy(store, job)
    reset_source_plan(store, job)

    class UserClient(PageClient):
        async def get_entity(self, ref):
            return tagged_entity("User")

    client = UserClient(store, posts([100, 99]))
    result = load_source(store, job, runtime, client)
    assert result["state"] == "failed" and result["reason"] == "unsupported_source_type"
    assert not client.calls


@pytest.mark.parametrize("field,value", [("default_mode", "invalid"), ("public_channel_mode", "invalid"),
                                         ("private_channel_mode", "invalid"), ("public_channel_mode", "auto")])
def test_s08_page_sync_contract_invalid_auth_profile_is_rejected_before_admission(field, value):
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    config["auth"][field] = value
    with pytest.raises(ValueError, match="auth"):
        analysis.resolve_analysis_settings(config, config_dir=folder)


def policy_job(store, *, since="2026-10-10", until="2026-10-12", update_id=20, channels="@one"):
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    config["processing"]["ocr"] = False
    config["telethon"]["connection_retry_backoff_seconds"] = 0
    event = {"update_id": update_id, "message": {"chat": {"id": 100}, "from": {"id": 7},
              "text": f"/topics {channels} since={since} until={until}"}}
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999",
                                           now=NOW, config_dir=folder)
    return store.claim_job(queued["job_id"], max_running_jobs=8, now=NOW)


def dated_posts(*pairs):
    values = posts([item[0] for item in pairs])
    for message, (_, date) in zip(values, pairs):
        message.date = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    return values


def cached_posts(store, *pairs, peer=11):
    history.upsert_channel(store.conn, entity(peer))
    for message in dated_posts(*pairs):
        history.upsert_message(store.conn, entity(peer), message, None, None, None, None)


def ensure_history(store, job, runtime, client):
    function = getattr(history, "ensure_analysis_history", None)
    assert callable(function), "History orchestration must select reuse, scoped backfill and fresh update"
    return asyncio.run(function(store, job["job_id"], runtime, generation=job["generation"], client=client))


def test_s09_history_policy_reuses_closed_utc_days_without_edge_posts(store, runtime):
    job = policy_job(store)
    cached_posts(store, (10, "2026-10-10T18:00:00"), (30, "2026-10-12T08:00:00"))
    client = PageClient(store, [])
    result = ensure_history(store, job, runtime, client)
    assert not client.calls
    assert client.entities == ["@one"]  # Availability is still checked.
    assert result["sources"]["11"]["state"] == "complete"
    assert result["sources"]["11"]["mode"] == "reuse"
    assert store.get_job(job["job_id"])["downloaded_messages"] == 0


def test_s09_history_policy_current_day_updates_only_fresh_edge_and_freezes_stop(store, runtime):
    job = policy_job(store, since="2026-10-10", until="today")
    cached_posts(store, (10, "2026-10-10T18:00:00"), (30, "2026-10-15T08:00:00"),
                 (40, "2026-10-16T08:00:00"), (60, "2026-10-16T18:00:00"))
    client = PageClient(store, dated_posts((60, "2026-10-16T18:00:00"), (50, "2026-10-16T11:00:00"),
                                         (40, "2026-10-16T08:00:00"), (30, "2026-10-15T08:00:00")))
    result = ensure_history(store, job, runtime, client)
    source = result["sources"]["11"]
    assert source["state"] == "complete" and source["mode"] == "update"
    assert source["stop_id"] == 40  # Existing local future rows must not become the stop.
    assert source["since"] == "2026-10-16T00:00:00+00:00"
    assert source["until"] == NOW.isoformat()
    assert store.conn.execute("SELECT 1 FROM messages WHERE message_id=50").fetchone()
    assert all(options["offset_date"] <= NOW.replace(second=1) for _, options in client.calls)


def test_s09_history_policy_new_channel_downloads_only_requested_period(store, runtime):
    job = policy_job(store, since="2026-10-12", until="2026-10-12")
    client = PageClient(store, dated_posts((60, "2026-10-16T10:00:00"), (30, "2026-10-12T10:00:00"),
                                         (20, "2026-10-11T10:00:00"), (10, "2026-01-01T10:00:00")))
    result = ensure_history(store, job, runtime, client)
    assert result["sources"]["11"]["state"] == "complete"
    assert [r[0] for r in store.conn.execute("SELECT message_id FROM messages")] == [30]
    assert client.calls[0][1]["offset_date"].date().isoformat() == "2026-10-13"


def test_s09_history_policy_missing_older_edge_does_not_redownload_cached_middle(store, runtime):
    job = policy_job(store)
    cached_posts(store, (20, "2026-10-11T08:00:00"), (30, "2026-10-12T08:00:00"))
    client = PageClient(store, dated_posts((30, "2026-10-12T08:00:00"), (20, "2026-10-11T08:00:00"),
                                         (10, "2026-10-10T10:00:00"), (9, "2026-10-09T10:00:00")))
    result = ensure_history(store, job, runtime, client)
    assert result["sources"]["11"]["state"] == "complete"
    assert client.calls[0][1]["offset_date"].date().isoformat() == "2026-10-11"
    assert store.get_job(job["job_id"])["downloaded_messages"] == 2


@pytest.mark.parametrize("outside_archive", [False, True])
def test_s09_history_policy_successful_empty_closed_period_is_reused_by_next_job(store, runtime, outside_archive):
    if outside_archive:
        cached_posts(store, (1, "2026-10-01T10:00:00"), (99, "2026-10-20T10:00:00"))
    first = policy_job(store)
    first_client = PageClient(store, [])
    result = ensure_history(store, first, runtime, first_client)
    assert len(first_client.calls) == 1
    assert result["sources"]["11"]["mode"] == "backfill"
    assert result["sources"]["11"]["state"] == "complete"
    store.finish_job(first["job_id"], generation=first["generation"], status="completed", now=NOW)
    second = policy_job(store, update_id=21)
    client = PageClient(store, [])
    result = ensure_history(store, second, runtime, client)
    assert not client.calls, "An empty successful range is different from never-loaded history"
    assert result["sources"]["11"]["mode"] == "reuse"


@pytest.mark.parametrize("legacy_timestamp", [False, True])
def test_s38_empty_requested_window_inside_archive_requires_bounded_backfill(store, runtime, legacy_timestamp):
    job = policy_job(store)
    cached_posts(store, (1, "2026-10-01T10:00:00"), (99, "2026-10-20T10:00:00"))
    if legacy_timestamp:
        history.update_sync_state(store.conn, 11, last_full_sync_at=NOW.isoformat())
    client = PageClient(store, dated_posts((30, "2026-10-12T10:00:00"), (20, "2026-10-11T10:00:00"),
                                         (10, "2026-10-10T10:00:00"), (9, "2026-10-09T10:00:00")))
    result = ensure_history(store, job, runtime, client)
    assert client.calls, "Archive edges do not prove an unobserved empty requested range was loaded"
    assert client.calls[0][1]["offset_date"].date().isoformat() == "2026-10-13"
    assert result["sources"]["11"]["state"] == "complete"
    assert [r[0] for r in store.conn.execute("SELECT message_id FROM messages WHERE channel_id=11 AND date_utc>=? AND date_utc<=? ORDER BY message_id",
        (job["request"]["request"]["since"], job["request"]["request"]["until"]))] == [10, 20, 30]


def test_s38_previous_reuse_without_download_does_not_prove_empty_range(store, runtime):
    cached_posts(store, (1, "2026-10-01T10:00:00"), (99, "2026-10-20T10:00:00"))
    first = policy_job(store)
    store.save_checkpoint(first["job_id"], {"history": {"sources": {"11": {
        "mode": "reuse", "state": "complete", "cursor": 0, "stop_id": None, "returned_messages": 0}}}},
        generation=first["generation"], max_bytes=first["request"]["settings"]["analysis"]["max_intermediate_bytes"])
    store.finish_job(first["job_id"], generation=first["generation"], status="completed", now=NOW)
    second = policy_job(store, update_id=21)
    client = PageClient(store, dated_posts((20, "2026-10-11T10:00:00"), (9, "2026-10-09T10:00:00")))
    ensure_history(store, second, runtime, client)
    assert client.calls, "An old reuse decision is not evidence of a successful empty Telegram fetch"
    assert store.conn.execute("SELECT 1 FROM messages WHERE channel_id=11 AND message_id=20").fetchone()


def test_s09_history_policy_known_partial_continues_old_cursor_despite_new_local_max(store, runtime):
    first = policy_job(store)
    page_policy(store, first, cap=2, page=2)
    messages = dated_posts((100, "2026-10-12T11:00:00"), (99, "2026-10-12T10:00:00"),
                           (98, "2026-10-11T10:00:00"), (97, "2026-10-10T10:00:00"),
                           (96, "2026-10-09T10:00:00"))
    result = ensure_history(store, first, runtime, PageClient(store, messages))
    assert result["sources"]["11"]["state"] == "partial"
    assert result["sources"]["11"]["cursor"] == 99
    # A locally present lower edge does not erase the known incomplete cursor.
    cached_posts(store, (90, "2026-10-10T08:00:00"))
    store.finish_job(first["job_id"], generation=first["generation"], status="partial", now=NOW)
    second = policy_job(store, update_id=21)
    client = PageClient(store, messages)
    result = ensure_history(store, second, runtime, client)
    assert client.calls and client.calls[0][1]["offset_id"] == 99
    assert result["sources"]["11"]["state"] == "complete"
    assert store.conn.execute("SELECT 1 FROM messages WHERE message_id=98").fetchone()


def test_s09_history_policy_denied_source_is_distinct_from_empty_and_other_sources_continue(store, runtime):
    from telethon.errors import ChannelPrivateError
    job = policy_job(store, channels="@one,@two")

    class Client(PageClient):
        async def get_entity(self, ref):
            if ref == "@one":
                raise ChannelPrivateError(None)
            return await super().get_entity(ref)

    result = ensure_history(store, job, runtime, Client(store, []))
    assert result["failed_refs"]["@one"]["state"] == "failed"
    assert result["failed_refs"]["@one"]["reason"] == "source_unavailable"
    assert result["sources"]["22"]["state"] == "complete"


def test_s09_history_policy_both_missing_edges_preserve_cached_middle(store, runtime):
    job = policy_job(store, since="2026-10-09", until="2026-10-14")
    cached_posts(store, (30, "2026-10-11T10:00:00"), (40, "2026-10-12T10:00:00"))
    values = dated_posts((70, "2026-10-15T10:00:00"), (60, "2026-10-14T10:00:00"),
                         (50, "2026-10-13T10:00:00"), (40, "2026-10-12T10:00:00"),
                         (30, "2026-10-11T10:00:00"), (20, "2026-10-10T10:00:00"),
                         (10, "2026-10-09T10:00:00"), (9, "2026-10-08T10:00:00"))
    client = PageClient(store, values)
    result = ensure_history(store, job, runtime, client)
    assert result["sources"]["11"]["state"] == "complete"
    assert [options["offset_date"].date().isoformat() for _, options in client.calls] == ["2026-10-11", "2026-10-15"]
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 6


@pytest.mark.parametrize("kind", ["ChatForbidden", "ChannelForbidden"])
def test_s09_history_policy_forbidden_entity_cannot_reuse_cached_history(store, runtime, kind):
    job = policy_job(store)
    cached_posts(store, (10, "2026-10-10T18:00:00"), (30, "2026-10-12T08:00:00"))

    class Client(PageClient):
        async def get_entity(self, ref):
            return tagged_entity(kind)

    client = Client(store, [])
    result = ensure_history(store, job, runtime, client)
    assert result["failed_refs"]["@one"]["reason"] == "source_unavailable"
    assert not client.calls and not result.get("sources")


def test_s09_history_policy_legacy_sync_error_cannot_be_hidden_by_local_bounds(store, runtime):
    job = policy_job(store)
    cached_posts(store, (10, "2026-10-10T18:00:00"), (30, "2026-10-12T08:00:00"))
    history.update_sync_state(store.conn, 11, last_error="fixture interrupted sync")
    client = PageClient(store, [])
    result = ensure_history(store, job, runtime, client)
    assert client.calls, "A known failure without a resumable cursor needs a bounded recheck"
    assert result["sources"]["11"]["state"] == "complete"
    assert store.conn.execute("SELECT last_error FROM sync_state WHERE channel_id=11").fetchone()[0] is None


def test_s09_history_policy_successful_legacy_timestamp_without_range_does_not_prove_empty_scope(store, runtime):
    job = policy_job(store)
    history.update_sync_state(store.conn, 11, last_full_sync_at=NOW.isoformat())
    client = PageClient(store, [])
    ensure_history(store, job, runtime, client)
    assert len(client.calls) == 1


def test_s09_history_policy_future_only_cache_does_not_duplicate_refresh_windows(store, runtime):
    job = policy_job(store, since="2026-10-10", until="today")
    cached_posts(store, (100, "2026-10-17T18:00:00"))
    client = PageClient(store, [])
    result = ensure_history(store, job, runtime, client)
    assert result["sources"]["11"]["state"] == "complete"
    assert len(client.calls) == 1


def test_s09_history_policy_resume_pending_page_keeps_accounting_and_then_advances_window(store, runtime, monkeypatch):
    job = policy_job(store, since="2026-10-09", until="2026-10-14")
    page_policy(store, job, cap=20, page=5, flush=1)
    cached_posts(store, (30, "2026-10-11T10:00:00"), (40, "2026-10-12T10:00:00"))
    values = dated_posts((60, "2026-10-14T10:00:00"), (50, "2026-10-13T10:00:00"),
                         (20, "2026-10-10T10:00:00"), (10, "2026-10-09T10:00:00"), (9, "2026-10-08T10:00:00"))
    original = history.drain_analysis_history_page
    calls = []

    def crash_after_flush(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("fixture process crash")
        return value

    with monkeypatch.context() as patch:
        patch.setattr(history, "drain_analysis_history_page", crash_after_flush)
        with pytest.raises(RuntimeError, match="fixture process crash"):
            ensure_history(store, job, runtime, PageClient(store, values))
    before = store.get_job(job["job_id"])
    assert before["checkpoint"]["history"]["sources"]["11"]["page"]["position"] == 1
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=NOW)
    store.resume_job(job["job_id"], generation=job["generation"], phase="sync", max_queued_jobs=8, now=NOW)
    resumed = store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)
    client = PageClient(store, values)
    result = ensure_history(store, resumed, runtime, client)
    assert result["sources"]["11"]["state"] == "complete"
    assert len(client.calls) == 1  # Only the second edge; pending first page is drained.
    assert client.calls[0][1]["offset_date"].date().isoformat() == "2026-10-15"
    assert store.get_job(job["job_id"])["downloaded_messages"] >= before["downloaded_messages"]
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 6


def test_s09_history_policy_new_job_inherits_received_remainder_without_network_refetch(store, runtime, monkeypatch):
    first = policy_job(store)
    page_policy(store, first, cap=20, page=5, flush=1)
    values = dated_posts((30, "2026-10-12T10:00:00"), (20, "2026-10-11T10:00:00"),
                         (10, "2026-10-10T10:00:00"), (9, "2026-10-09T10:00:00"))
    original = history.drain_analysis_history_page

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fixture process crash")

    with monkeypatch.context() as patch:
        patch.setattr(history, "drain_analysis_history_page", crash)
        with pytest.raises(RuntimeError):
            ensure_history(store, first, runtime, PageClient(store, values))
    store.finish_job(first["job_id"], generation=first["generation"], status="interrupted", now=NOW)
    second = policy_job(store, update_id=21)
    client = PageClient(store, [])
    result = ensure_history(store, second, runtime, client)
    assert not client.calls
    assert result["sources"]["11"]["state"] == "complete"
    assert store.get_job(second["job_id"])["downloaded_messages"] == 0  # Reused normalized page, no new API history.
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3


def test_s09_history_policy_access_recovery_restores_previously_reused_source(store, runtime):
    from telethon.errors import ChannelPrivateError
    job = policy_job(store)
    cached_posts(store, (10, "2026-10-10T18:00:00"), (30, "2026-10-12T08:00:00"))
    ensure_history(store, job, runtime, PageClient(store, []))

    class Denied(PageClient):
        async def get_entity(self, ref):
            raise ChannelPrivateError(None)

    failed = ensure_history(store, job, runtime, Denied(store, []))
    assert failed["sources"]["11"]["state"] == "failed"
    recovered = ensure_history(store, job, runtime, PageClient(store, []))
    assert recovered["sources"]["11"]["state"] == "complete"
    assert "reason" not in recovered["sources"]["11"]


def test_s09_history_policy_colliding_peer_kind_cannot_reuse_other_namespace(store, runtime):
    job = policy_job(store)
    cached_posts(store, (10, "2026-10-10T18:00:00"), (30, "2026-10-12T08:00:00"))
    history.upsert_channel(store.conn, tagged_entity("Chat"))

    class Client(PageClient):
        async def get_entity(self, ref):
            return tagged_entity("Channel")

    result = ensure_history(store, job, runtime, Client(store, []))
    assert result["failed_refs"]["@one"]["reason"] == "source_identity_conflict"
    assert not result.get("sources")
    assert store.conn.execute("SELECT channel_type FROM channels WHERE channel_id=11").fetchone()[0] == "Chat"


def test_s09_history_policy_partial_update_keeps_old_stop_across_new_jobs(store, runtime):
    first = policy_job(store, until="today")
    cached_posts(store, (10, "2026-10-10T10:00:00"), (40, "2026-10-16T08:00:00"))
    page_policy(store, first, cap=1, page=1)
    values = dated_posts((60, "2026-10-16T11:00:00"), (50, "2026-10-16T10:00:00"), (40, "2026-10-16T08:00:00"))
    result = ensure_history(store, first, runtime, PageClient(store, values))
    assert result["sources"]["11"]["state"] == "partial"
    store.finish_job(first["job_id"], generation=first["generation"], status="partial", now=NOW)
    second = policy_job(store, until="today", update_id=21)
    client = PageClient(store, values)
    result = ensure_history(store, second, runtime, client)
    assert result["sources"]["11"]["stop_id"] == 40
    assert client.calls[0][1]["offset_id"] == 60
    assert store.conn.execute("SELECT 1 FROM messages WHERE message_id=50").fetchone()


def test_s09_history_policy_partial_overlapping_new_scope_is_not_silently_reused(store, runtime):
    first = policy_job(store)
    page_policy(store, first, cap=1, page=1)
    values = dated_posts((30, "2026-10-12T10:00:00"), (20, "2026-10-11T10:00:00"), (10, "2026-10-10T10:00:00"))
    ensure_history(store, first, runtime, PageClient(store, values))
    cached_posts(store, (1, "2026-10-09T10:00:00"), (40, "2026-10-14T10:00:00"))
    store.finish_job(first["job_id"], generation=first["generation"], status="partial", now=NOW)
    second = policy_job(store, since="2026-10-09", until="2026-10-14", update_id=21)
    client = PageClient(store, values)
    result = ensure_history(store, second, runtime, client)
    assert result["sources"]["11"]["state"] == "complete"
    assert client.calls and store.conn.execute("SELECT 1 FROM messages WHERE message_id=20").fetchone()


def test_s09_history_policy_aliases_of_one_peer_download_once_in_requested_order(store, runtime):
    job = policy_job(store, channels="@one,@alias")
    client = PageClient(store, [])
    result = ensure_history(store, job, runtime, client)
    assert client.entities == ["@one", "@alias"]
    assert len(client.calls) == 1
    assert result["refs"] == {"@one": 11, "@alias": 11}
    assert len(result["sources"]) == 1


def test_s09_history_policy_bounded_session_start_failure_allows_next_role(store, runtime, monkeypatch):
    from telegram_connector.analysis_store import canonical_json
    job = policy_job(store, channels="@one,-10022")
    snapshot = copy.deepcopy(job["request"])
    snapshot["settings"]["auth"]["default_mode"] = "auto"
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(snapshot), job["job_id"]))
    client = PageClient(store, [])
    opened = []

    @asynccontextmanager
    async def session(profile, auth, **options):
        opened.append(auth)
        if auth == "bot":
            raise history.TelegramConnectionError(ConnectionError("fixture unavailable"), 3, 3)
        yield client

    monkeypatch.setattr(history, "open_telethon_session", session)
    function = getattr(history, "ensure_analysis_history")
    result = asyncio.run(function(store, job["job_id"], runtime, generation=job["generation"]))
    assert opened == ["bot", "user"]
    assert result["failed_refs"]["@one"]["state"] == "partial"
    assert result["refs"]["-10022"] == 11  # Fixture identity, resolved by a separate auth role.


def test_s09_history_policy_known_failure_and_missing_edge_are_not_downloaded_twice(store, runtime):
    job = policy_job(store)
    cached_posts(store, (30, "2026-10-12T08:00:00"))
    history.update_sync_state(store.conn, 11, last_error="fixture interrupted sync")
    client = PageClient(store, [])
    ensure_history(store, job, runtime, client)
    assert len(client.calls) == 1  # Union overlapping work, instead of a gap plus full recheck.


def test_s09_history_policy_expired_run_enumerates_unstarted_sources_without_opening_session(store, runtime, monkeypatch):
    from common import process as common_process
    job = policy_job(store, channels="@one,@two")
    opened = []

    @asynccontextmanager
    async def session(*args, **kwargs):
        opened.append(True)
        yield PageClient(store, [])

    monkeypatch.setattr(history, "open_telethon_session", session)
    result = asyncio.run(history.ensure_analysis_history(store, job["job_id"], runtime, generation=job["generation"],
                                                         deadline=common_process.continuous_time() - 1))
    assert not opened
    assert set(result["failed_refs"]) == {"@one", "@two"}
    assert all(item["reason"] == "run_deadline" for item in result["failed_refs"].values())


@pytest.mark.parametrize("reason", ["credentials", "uncertain_cleanup"])
def test_s09_history_policy_global_session_failure_does_not_start_another_client(store, runtime, monkeypatch, reason):
    from telegram_shared.async_retry import AsyncCleanupError
    job = policy_job(store, channels="@one,@two")
    opened = []

    @asynccontextmanager
    async def session(*args, **kwargs):
        opened.append(True)
        if reason == "credentials":
            raise PermissionError("fixture unauthorized session")
        raise history.TelegramConnectionError(AsyncCleanupError("fixture uncertain disconnect"), 1, 3)
        yield  # pragma: no cover - async context-manager protocol only.

    monkeypatch.setattr(history, "open_telethon_session", session)
    expected = PermissionError if reason == "credentials" else history.TelegramConnectionError
    with pytest.raises(expected):
        asyncio.run(history.ensure_analysis_history(store, job["job_id"], runtime, generation=job["generation"]))
    assert len(opened) == 1


def test_s09_history_policy_retained_checkpoint_scan_does_not_hold_sqlite_write_transaction(store, runtime, monkeypatch):
    job = policy_job(store)
    original = history._previous_analysis_history

    def inspect_previous(*args):
        assert not store.conn.in_transaction, "Retained-job scans must not block other SQLite writers"
        return original(*args)

    monkeypatch.setattr(history, "_previous_analysis_history", inspect_previous)
    ensure_history(store, job, runtime, PageClient(store, []))
