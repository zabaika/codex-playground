import copy
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import shlex
import selectors
import subprocess
import sys
import tomllib
from types import SimpleNamespace
from threading import Barrier, Event

import pytest

from common import sqlite as common_sqlite
from telegram_connector import telegram_analysis as analysis
from telegram_connector import telegram_bridge as bridge
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore, StaleGenerationError


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
CONFIG_DIR = Path(history.APP_DIR) / "config"
REAL_HISTORY_HANDLER = bridge.handle_history_command


@pytest.fixture
def config():
    values = tomllib.loads((CONFIG_DIR / "runtime.example.toml").read_text())
    values["processing"]["model"] = "fixture-model"
    values["bridge"].update(allowed_chat_ids="100", allowed_user_ids="7", allowed_usernames="")
    return values


@pytest.fixture
def access():
    return {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}


@pytest.fixture
def store():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    try:
        yield AnalysisStore(conn)
    finally:
        conn.close()


def update(update_id=42, text='/search @one since=week until=today "Какие банки  открывают счета?"'):
    return {"update_id": update_id, "message": {
        "chat": {"id": 100}, "from": {"id": 7, "username": "alice"}, "date": 1792065600, "text": text,
    }}


def admit(store, config, access, event, *, namespace="telegram:999", now=NOW):
    function = getattr(analysis, "admit_analysis_job", None)
    assert callable(function), "Analytics needs durable, ACL-bound admission"
    return function(store, config, event, access=access, bot_namespace=namespace, now=now, config_dir=CONFIG_DIR)


def test_s04_admission_boundary_freezes_input_and_deduplicates_replay(store, config, access, monkeypatch):
    first, created = admit(store, config, access, update())
    assert created
    assert first["status"] == "queued"
    assert first["requester_id"] == "7" and first["destination"] == "100"
    assert first["admission_key"] == "telegram:999:42"
    assert first["request"]["request"]["query"] == "Какие банки  открывают счета?"
    assert first["accepted_at"] == first["cutoff_at"] == NOW.isoformat()
    assert first["request"]["settings"]["analysis_prompts"]["hash"]
    assert not store.conn.in_transaction
    # Replay uses the original accepted snapshot even after policy/prompt changes.
    monkeypatch.setattr(analysis, "resolve_analysis_settings", lambda *a, **k: pytest.fail("Replay must not rebuild the snapshot"))
    replay, created = admit(store, config, access, update(), now=NOW + timedelta(days=2))
    assert not created
    assert replay == first
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 1


@pytest.mark.parametrize("change", ["text", "requester", "destination"])
def test_s04_admission_boundary_changed_identity_conflicts(store, config, access, change):
    first, _ = admit(store, config, access, update())
    changed = update()
    if change == "text":
        changed["message"]["text"] = '/search @one "другой вопрос"'
    elif change == "requester":
        changed["message"]["from"]["id"] = 8
        access["users"] = frozenset({"7", "8"})
    else:
        changed["message"]["chat"]["id"] = 101
        access["chats"] = frozenset({"100", "101"})
    with pytest.raises(analysis.AnalysisRejection, match="conflict"):
        admit(store, config, access, changed)
    assert store.get_job(first["job_id"]) == first


def test_s04_admission_boundary_scope_includes_bot_and_update(store, config, access):
    first, _ = admit(store, config, access, update())
    other_bot, _ = admit(store, config, access, update(), namespace="telegram:111")
    other_update, _ = admit(store, config, access, update(43))
    assert len({first["job_id"], other_bot["job_id"], other_update["job_id"]}) == 3


@pytest.mark.parametrize("failure", ["no-chats", "wrong-chat", "no-users", "wrong-user", "missing-user"])
def test_s04_admission_boundary_acl_precedes_storage(store, config, access, failure, monkeypatch):
    event = update()
    if failure == "no-chats":
        access["chats"] = frozenset()
    elif failure == "wrong-chat":
        event["message"]["chat"]["id"] = 999
    elif failure == "no-users":
        access["users"] = frozenset()
    elif failure == "wrong-user":
        event["message"]["from"]["id"] = 999
    else:
        event["message"]["from"].pop("id")
    def unexpected_storage(*args, **kwargs):
        pytest.fail("Forbidden admission must not query jobs")
    monkeypatch.setattr(store, "get_by_admission", unexpected_storage, raising=False)
    assert callable(getattr(analysis, "admit_analysis_job", None))
    with pytest.raises(analysis.AnalysisRejection, match="forbidden"):
        admit(store, config, access, event)


def test_s04_admission_boundary_queue_cap_is_atomic_and_replay_survives_full_queue(store, config, access):
    config["analysis"]["max_queued_jobs"] = 1
    first, _ = admit(store, config, access, update())
    assert callable(getattr(analysis, "admit_analysis_job", None))
    with pytest.raises(analysis.AnalysisRejection, match="queue_full"):
        admit(store, config, access, update(43))
    replay, created = admit(store, config, access, update())
    assert not created and replay["job_id"] == first["job_id"]
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 1


@pytest.mark.parametrize("text", ["/topics", '/search @one ""', "/qa @one --include-comments"])
def test_s04_admission_boundary_invalid_request_is_not_queued(store, config, access, text):
    assert callable(getattr(analysis, "admit_analysis_job", None))
    with pytest.raises(analysis.AnalysisRejection, match="invalid_request"):
        admit(store, config, access, update(text=text))
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 0


def test_s04_admission_boundary_escaped_query_has_no_hidden_identity_limit(store, config, access):
    query = '"' * 1500
    job, created = admit(store, config, access, update(text=f"/search @one {shlex.quote(query)}"))
    assert created
    assert job["request"]["request"]["query"] == query


@pytest.fixture
def listener(tmp_path, config, monkeypatch):
    path = tmp_path / "history.sqlite3"
    config["paths"]["history_db"] = str(path)
    conn = common_sqlite.connect_sqlite(path)
    history.init_db(conn)
    conn.close()
    state = {"events": [update()], "offsets": [], "sent": [], "legacy": [], "token": "999:fixture-token"}
    monkeypatch.setattr(bridge, "load_runtime_config", lambda: config)
    monkeypatch.setattr(bridge, "resolve_bridge_secrets", lambda _: {"TELEGRAM_BOT_TOKEN": state["token"]})
    monkeypatch.setattr(bridge, "load_offset", lambda: 42)
    monkeypatch.setattr(bridge, "fetch_updates", lambda *_: state["events"])
    monkeypatch.setattr(bridge, "append_inbox", lambda _: None)
    monkeypatch.setattr(bridge, "print_update", lambda _: None)
    monkeypatch.setattr(bridge, "save_offset", state["offsets"].append)
    monkeypatch.setattr(bridge, "send_text_message", lambda token, chat, text, **kw: state["sent"].append((str(chat), text)))
    monkeypatch.setattr(bridge, "handle_history_command", lambda *a, **kw: state["legacy"].append(a[2]))
    # S04 tests isolate admission from worker execution. S05 integration below
    # replaces this boundary with the actual threaded supervisor.
    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda *a, **kw: nullcontext(None), raising=False)

    def jobs():
        connection = common_sqlite.connect_sqlite(path)
        try:
            ids = connection.execute("SELECT job_id FROM analysis_jobs ORDER BY admission_key").fetchall()
            return [AnalysisStore(connection).get_job(row[0]) for row in ids]
        finally:
            connection.close()

    state["jobs"] = jobs
    state["args"] = SimpleNamespace(from_scratch=False, timeout=1, once=True, echo=False, run_commands=True)
    return state


def test_s04_admission_boundary_listener_commits_before_offset_and_ack(listener, monkeypatch):
    order = []

    def save(value):
        assert len(listener["jobs"]()) == 1, "Offset must follow a committed admission"
        order.append("offset")
        listener["offsets"].append(value)

    def send(token, chat, text, **kw):
        assert order == ["offset"], "Acknowledgement must follow the saved offset"
        job = listener["jobs"]()[0]
        assert job["job_id"] in text
        assert "Какие банки" not in text and "fixture-token" not in text
        order.append("ack")

    monkeypatch.setattr(bridge, "save_offset", save)
    monkeypatch.setattr(bridge, "send_text_message", send)
    assert bridge.cmd_listen(listener["args"]) == 0
    assert order == ["offset", "ack"]
    assert not listener["legacy"]


def test_s04_admission_boundary_listener_commit_failure_stops_batch(listener, monkeypatch, capsys):
    listener["events"].append(update(43))
    original = AnalysisStore.admit_job

    def fail_commit(self, *a, **kw):
        self.conn.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
                                 if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT" else sqlite3.SQLITE_OK)
        try:
            return original(self, *a, **kw)
        finally:
            self.conn.set_authorizer(None)

    monkeypatch.setattr(AnalysisStore, "admit_job", fail_commit)
    assert bridge.cmd_listen(listener["args"]) == 1
    assert not listener["offsets"] and not listener["sent"] and not listener["jobs"]()
    assert not listener["legacy"]
    assert "Какие банки" not in capsys.readouterr().err


def test_s04_admission_boundary_listener_offset_failure_replays_same_job(listener, monkeypatch):
    listener["events"].append(update(43))

    def fail_offset(value):
        raise OSError("fixture offset failure")

    monkeypatch.setattr(bridge, "save_offset", fail_offset)
    assert bridge.cmd_listen(listener["args"]) == 1
    first = listener["jobs"]()
    assert len(first) == 1
    assert not listener["sent"] and not listener["legacy"]
    listener["events"] = [update()]
    listener["token"] = "999:rotated-fixture-token"
    monkeypatch.setattr(bridge, "save_offset", listener["offsets"].append)
    assert bridge.cmd_listen(listener["args"]) == 0
    assert listener["jobs"]() == first
    assert listener["offsets"] == [43]
    assert len(listener["sent"]) == 1


@pytest.mark.parametrize("text", ['/search @one "unterminated', '/qa', '/topics @one --include-comments'])
def test_s04_admission_boundary_listener_invalid_is_safe_error(listener, text):
    listener["events"] = [update(text=text)]
    assert bridge.cmd_listen(listener["args"]) == 0
    assert not listener["jobs"]() and not listener["legacy"]
    assert listener["offsets"] == [43]
    assert len(listener["sent"]) == 1
    assert listener["sent"][0][1] == analysis.AnalysisRejection.MESSAGES["invalid_request"]


def test_s04_admission_boundary_listener_acl_rejects_before_opening_store(listener, monkeypatch):
    listener["events"][0]["message"]["from"]["id"] = 99
    monkeypatch.setattr(common_sqlite, "connect_sqlite", lambda *a, **k: pytest.fail("Denied command must not open storage"))
    assert bridge.cmd_listen(listener["args"]) == 0
    assert listener["offsets"] == [43]
    assert listener["sent"] == [("100", analysis.AnalysisRejection.MESSAGES["forbidden"])]
    assert not listener["legacy"]


def test_s04_admission_boundary_listener_ack_failure_keeps_job_and_continues(listener, monkeypatch):
    listener["events"].append(update(43, "/qa @two"))
    attempts = []

    def fail_ack(token, chat, text, **kw):
        attempts.append(text)
        raise SystemExit("Telegram API sendMessage fixture-token")

    monkeypatch.setattr(bridge, "send_text_message", fail_ack)
    assert bridge.cmd_listen(listener["args"]) == 0
    assert len(listener["jobs"]()) == 2
    assert listener["offsets"] == [43, 44] and len(attempts) == 2


def test_s04_admission_boundary_listener_resolves_acl_and_initializes_db_once(listener, monkeypatch):
    listener["events"].append(update(43, "QA@fixture_bot @two"))
    calls = []
    for name in ("parse_allowed_chat_ids", "parse_allowed_user_ids", "parse_allowed_usernames"):
        original = getattr(bridge, name)

        def count(config, name=name, original=original):
            calls.append(name)
            return original(config)

        monkeypatch.setattr(bridge, name, count)
    original_init = history.init_db
    monkeypatch.setattr(history, "init_db", lambda conn: (calls.append("init"), original_init(conn))[-1])
    assert bridge.cmd_listen(listener["args"]) == 0
    assert len(listener["jobs"]()) == 2
    assert sorted(calls) == ["init", "parse_allowed_chat_ids", "parse_allowed_user_ids", "parse_allowed_usernames"]


def test_s04_admission_boundary_listener_disabled_does_not_admit(listener):
    listener["args"].run_commands = False
    assert bridge.cmd_listen(listener["args"]) == 0
    assert not listener["jobs"]() and not listener["sent"] and not listener["legacy"]
    assert listener["offsets"] == [43]


@pytest.mark.parametrize("reason", ["queue_full", "conflict"])
def test_s04_admission_boundary_listener_rejects_without_replacing_accepted_job(listener, config, reason):
    config["analysis"]["max_queued_jobs"] = 1
    assert bridge.cmd_listen(listener["args"]) == 0
    original = listener["jobs"]()
    listener["events"] = [update(43 if reason == "queue_full" else 42, '/search @one "changed question"')]
    listener["sent"].clear()
    assert bridge.cmd_listen(listener["args"]) == 0
    assert listener["jobs"]() == original
    assert listener["sent"] == [("100", analysis.AnalysisRejection.MESSAGES[reason])]


def test_s04_admission_boundary_listener_resource_closed_after_storage_fault(listener, monkeypatch):
    connections = []
    original_connect = common_sqlite.connect_sqlite

    def capture(*a, **kw):
        conn = original_connect(*a, **kw)
        connections.append(conn)
        return conn

    monkeypatch.setattr(common_sqlite, "connect_sqlite", capture)
    def fail_admission(*a, **kw):
        raise sqlite3.OperationalError("fixture")

    monkeypatch.setattr(AnalysisStore, "admit_job", fail_admission)
    assert bridge.cmd_listen(listener["args"]) == 1
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


@pytest.mark.parametrize("text", ['/SEARCH@fixture @one "private query', 'qa @one', '/unknown-private-query "bad'])
def test_s04_admission_boundary_logging_handles_malformed_input_without_query(text, capsys):
    event = update(text=text)
    bridge.print_update(event)
    payload = bridge.redact_update_for_storage(event)
    output = capsys.readouterr().out
    assert "private" not in output
    assert "private" not in str(payload)
    if text.lower().startswith(("/search", "qa")):
        assert payload["command"] in {"/search", "/qa"}
        assert payload.get("command_text", payload["command"]) == payload["command"]


def claim(store, job_id, *, cap=1):
    function = getattr(store, "claim_job", None)
    assert callable(function), "Worker ownership needs an atomic claim with a running cap"
    return function(job_id, max_running_jobs=cap, now=NOW)


def finish(store, job, status="interrupted"):
    function = getattr(store, "finish_job", None)
    assert callable(function), "Worker outcomes must commit against the owned generation"
    return function(job["job_id"], generation=job["generation"], status=status, now=NOW)


def test_s05_supervisor_claim_is_conditional_and_fences_stale_checkpoint(store, config, access):
    queued, _ = admit(store, config, access, update())
    running = claim(store, queued["job_id"])
    assert running["status"] == "running" and running["generation"] == queued["generation"] + 1
    assert claim(store, queued["job_id"]) is None
    with pytest.raises(StaleGenerationError):
        store.save_checkpoint(queued["job_id"], {"stale": True}, generation=queued["generation"], max_bytes=1000)
    store.save_checkpoint(running["job_id"], {"cursor": 10}, generation=running["generation"], max_bytes=1000)
    assert store.get_job(running["job_id"])["checkpoint"] == {"cursor": 10}


def test_s05_supervisor_claim_cap_and_cancelled_queue(store, config, access):
    first, _ = admit(store, config, access, update())
    second, _ = admit(store, config, access, update(43))
    running = claim(store, first["job_id"])
    assert claim(store, second["job_id"]) is None
    finish(store, running)
    store.conn.execute("UPDATE analysis_jobs SET cancel_requested=1 WHERE job_id=?", (second["job_id"],))
    assert claim(store, second["job_id"]) is None
    assert claim(store, "missing") is None


@pytest.mark.parametrize("same_job", [True, False])
def test_s05_supervisor_claim_race_never_exceeds_cap(listener, config, access, same_job):
    conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
    try:
        repo = AnalysisStore(conn)
        first, _ = admit(repo, config, access, update())
        second, _ = admit(repo, config, access, update(43))
    finally:
        conn.close()
    barrier = Barrier(2)

    def race(job):
        connection = common_sqlite.connect_sqlite(config["paths"]["history_db"])
        try:
            barrier.wait(timeout=5)
            return claim(AnalysisStore(connection), job["job_id"])
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(race, job) for job in (first, first if same_job else second)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert [job["status"] for job in listener["jobs"]()].count("running") == 1


@pytest.mark.parametrize("status", ["completed", "partial", "failed", "timed_out", "cancelled", "interrupted"])
def test_s05_supervisor_claim_terminal_outcome_preserves_ledger(store, config, access, status):
    queued, _ = admit(store, config, access, update())
    running = claim(store, queued["job_id"])
    store.conn.execute("UPDATE analysis_jobs SET used_tokens=17,llm_calls=2,reserved_tokens=30 WHERE job_id=?", (running["job_id"],))
    store.save_checkpoint(running["job_id"], {"next": "select"}, generation=running["generation"], max_bytes=1000)
    stale = dict(running, generation=running["generation"] - 1)
    with pytest.raises(StaleGenerationError):
        finish(store, stale, status)
    finish(store, running, status)
    result = store.get_job(running["job_id"])
    assert result["status"] == status and result["finished_at"] == NOW.isoformat()
    assert (result["used_tokens"], result["llm_calls"], result["reserved_tokens"]) == (17, 2, 30)
    assert result["checkpoint"] == {"next": "select"}
    with pytest.raises(StaleGenerationError):
        finish(store, running, status)
    with pytest.raises(StaleGenerationError):
        store.save_checkpoint(running["job_id"], {"late": True}, generation=running["generation"], max_bytes=1000)


def ownership(path, owner="supervisor"):
    function = getattr(bridge, "analysis_ownership_lock", None)
    assert callable(function), "Ownership must use an OS lock, independent of reusable PIDs"
    return function(path, owner=owner)


def test_s05_supervisor_claim_ownership_excludes_second_owner_and_keeps_inode(tmp_path):
    path = tmp_path / "history.sqlite3"
    with ownership(path) as first:
        assert first is not None
        with ownership(path) as competing:
            assert competing is None
    with ownership(path) as next_owner:
        assert next_owner is not None
    assert len(list(tmp_path.glob("*.lock"))) == 1, "Never unlink lock files while contenders may hold their inode"


def test_s05_supervisor_claim_recovery_respects_live_worker_without_pid(listener, config, access):
    conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
    repo = AnalysisStore(conn)
    try:
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])
        # No PID is authoritative. This stale metadata must not override a live lock.
        repo.save_checkpoint(running["job_id"], {"pid": 1}, generation=running["generation"], max_bytes=1000)
        function = getattr(bridge, "recover_analysis_jobs", None)
        assert callable(function), "Restart recovery must reconcile OS ownership before changing durable jobs"
        with ownership(Path(config["paths"]["history_db"]), running["job_id"]) as held:
            assert held is not None
            code = (
                "from pathlib import Path; from telegram_connector import telegram_bridge as b; "
                "import sys; "
                "\nwith b.analysis_ownership_lock(Path(sys.argv[1]), owner=sys.argv[2]) as lock:"
                "\n print('busy' if lock is None else 'owned', flush=True)"
            )
            completed = subprocess.run([sys.executable, "-c", code, config["paths"]["history_db"], running["job_id"]],
                                       cwd=history.APP_DIR.parent, capture_output=True, text=True, timeout=5)
            assert completed.returncode == 0 and completed.stdout.strip() == "busy"
            function(repo, Path(config["paths"]["history_db"]), now=NOW)
            assert repo.get_job(running["job_id"])["status"] == "running"
        function(repo, Path(config["paths"]["history_db"]), now=NOW)
        result = repo.get_job(running["job_id"])
        assert result["status"] == "interrupted" and result["checkpoint"] == {"pid": 1}
    finally:
        conn.close()


def test_s05_supervisor_claim_child_lock_survives_supervisor_context_exit(tmp_path):
    path = tmp_path / "history.sqlite3"
    job_id = "a" * 32
    # The child owns its lock itself; the parent never infers ownership from its PID.
    assert callable(getattr(bridge, "analysis_ownership_lock", None)), "Worker OS ownership is required"
    code = (
        "from pathlib import Path; from telegram_connector import telegram_bridge as b; import sys;"
        "\nwith b.analysis_ownership_lock(Path(sys.argv[1]), owner=sys.argv[2]) as lock:"
        "\n assert lock is not None"
        "\n print('ready', flush=True)"
        "\n sys.stdin.readline()"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(path), job_id], cwd=history.APP_DIR.parent,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "Fixture child must report ownership"
        assert child.stdout.readline().strip() == "ready"
        assert child.poll() is None
        with ownership(path) as supervisor:
            assert supervisor is not None
        with ownership(path, job_id) as duplicate_worker:
            assert duplicate_worker is None
        child.communicate("stop\n", timeout=5)
        assert child.returncode == 0
        with ownership(path, job_id) as resumed:
            assert resumed is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def run_worker(repo, path, job, executor):
    function = getattr(analysis, "run_analysis", None)
    assert callable(function), "The worker must validate generation under its own process lock"
    return function(repo, path, job_id=job["job_id"], generation=job["generation"], executor=executor)


def test_s05_supervisor_claim_worker_owns_lock_and_retains_checkpoint(listener, config, access):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])

        def executor(store, job):
            with ownership(path, job["job_id"]) as competitor:
                assert competitor is None
            store.save_checkpoint(job["job_id"], {"fixture_cursor": 5}, generation=job["generation"], max_bytes=1000)
            return "interrupted"

        assert run_worker(repo, path, running, executor) == 0
        result = repo.get_job(running["job_id"])
        assert result["status"] == "interrupted" and result["checkpoint"] == {"fixture_cursor": 5}
    finally:
        conn.close()


@pytest.mark.parametrize("reason", ["stale", "terminal", "lock_busy"])
def test_s05_supervisor_claim_worker_does_not_execute_without_ownership(listener, config, access, reason):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])
        invocation = dict(running)
        if reason == "stale":
            invocation["generation"] -= 1
        elif reason == "terminal":
            finish(repo, running)
        before = repo.get_job(running["job_id"])

        def executor(*a):
            pytest.fail("Unowned worker must not execute")

        if reason == "lock_busy":
            with ownership(path, running["job_id"]):
                assert run_worker(repo, path, invocation, executor) != 0
        else:
            assert run_worker(repo, path, invocation, executor) != 0
        assert repo.get_job(running["job_id"]) == before
    finally:
        conn.close()


@pytest.mark.parametrize("diagnostic_room", [True, False])
def test_s05_supervisor_claim_worker_failure_is_safely_recorded(listener, config, access, capsys, monkeypatch, diagnostic_room):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])
        save_checkpoint = repo.save_checkpoint

        def bounded_checkpoint(job_id, checkpoint, **kwargs):
            if "last_failure" in checkpoint and not diagnostic_room:
                raise analysis.BudgetExceededError("max_intermediate_bytes")
            return save_checkpoint(job_id, checkpoint, **kwargs)

        monkeypatch.setattr(repo, "save_checkpoint", bounded_checkpoint)

        def executor(store, job):
            store.save_checkpoint(job["job_id"], {"cursor": 20}, generation=job["generation"], max_bytes=1000)
            raise RuntimeError("private message fixture-secret-token")

        assert run_worker(repo, path, running, executor) == 1
        result = repo.get_job(running["job_id"])
        assert result["status"] == "failed" and result["checkpoint"]["cursor"] == 20
        if diagnostic_room:
            diagnostic = result["checkpoint"]["last_failure"]
            assert diagnostic["exception"] == "RuntimeError"
            assert diagnostic["phase"] == running["phase"]
            assert diagnostic["location"]["module"] == "telegram_analysis"
            assert diagnostic["location"]["function"] == "run_analysis"
            assert type(diagnostic["location"]["line"]) is int
        else:
            assert "last_failure" not in result["checkpoint"]
        assert "fixture-secret-token" not in str(result)
        output = capsys.readouterr()
        assert "private message" not in output.out + output.err
    finally:
        conn.close()


def test_s05_supervisor_claim_worker_cli_has_no_secret_dependency():
    completed = subprocess.run([sys.executable, str(history.APP_DIR / "telegram_analysis.py"), "--help"],
                               cwd=history.APP_DIR.parent, capture_output=True, text=True, timeout=5)
    assert completed.returncode == 0
    assert "--job-id" in completed.stdout and "--generation" in completed.stdout and "--db" in completed.stdout


class FixtureProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, *a, **kw):
        pytest.fail("Supervisor must not wait for an active worker")


def supervisor(config, access, *, launch, legacy_handler=None):
    factory = getattr(bridge, "BridgeSupervisor", None)
    assert callable(factory), "Polling needs an independent durable-queue supervisor"
    return factory(config, {"TELEGRAM_BOT_TOKEN": "999:fixture-token"}, access,
                   launch=launch, legacy_handler=legacy_handler,
                   progress_api=lambda token, method, payload, **kw: {"message_id": 501, "chat": {"id": payload["chat_id"]}},
                   access_provider=lambda: access)


def seed_job(config, access, *, event=None):
    conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
    try:
        return admit(AnalysisStore(conn), config, access, event or update())[0]
    finally:
        conn.close()


def test_s05_supervisor_claim_background_starts_and_observes_without_waiting(listener, config, access):
    seed_job(config, access)
    process = FixtureProcess()
    launched, observed = Event(), Event()

    def launch(job):
        assert job["status"] == "running"
        launched.set()
        return process

    runtime = supervisor(config, access, launch=launch)
    original = runtime._tick

    def tick(store):
        original(store)
        if listener["jobs"]()[0]["status"] == "interrupted":
            observed.set()

    runtime._tick = tick
    with runtime:
        assert launched.wait(5), "Queued work must start independently of long polling"
        assert listener["jobs"]()[0]["status"] == "running"
        # A child which exits without its own terminal commit is interrupted.
        process.returncode = 0
        runtime.wake()
        assert observed.wait(5)
    assert listener["jobs"]()[0]["status"] == "interrupted"


def test_s05_supervisor_claim_second_supervisor_cannot_launch(listener, config, access):
    seed_job(config, access)
    launched = Event()
    with supervisor(config, access, launch=lambda job: (launched.set(), FixtureProcess())[1]):
        assert launched.wait(5)
        with supervisor(config, access, launch=lambda job: pytest.fail("Duplicate supervisor launch")):
            assert listener["jobs"]()[0]["status"] == "running"


@pytest.mark.parametrize("problem", ["revoked", "version"])
def test_s05_supervisor_claim_rechecks_queued_access_and_version(listener, config, access, problem):
    job = seed_job(config, access)
    if problem == "revoked":
        access["users"] = frozenset({"8"})
    else:
        conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
        try:
            job["request"]["settings"]["algorithm_version"] += 1
            from telegram_connector.analysis_store import canonical_json
            conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(job["request"]), job["job_id"]))
        finally:
            conn.close()
    checked = Event()
    runtime = supervisor(config, access, launch=lambda job: pytest.fail("Unapproved queued work must not launch"))
    original = runtime._tick
    runtime._tick = lambda store: (original(store), checked.set())
    with runtime:
        assert checked.wait(5)
    result = listener["jobs"]()[0]
    assert result["status"] == "failed" and result["llm_calls"] == 0


def test_s05_supervisor_claim_startup_cannot_interrupt_worker_before_lock_acquisition(listener, config, access):
    seed_job(config, access)
    process = FixtureProcess()
    launched, checked = Event(), Event()
    runtime = supervisor(config, access, launch=lambda job: (launched.set(), process)[1])
    original = runtime._tick
    cycles = []

    def tick(store):
        original(store)
        cycles.append(1)
        if len(cycles) >= 2:
            checked.set()

    runtime._tick = tick
    with runtime:
        assert launched.wait(5)
        runtime.wake()
        assert checked.wait(5)
        assert listener["jobs"]()[0]["status"] == "running"


def test_s05_supervisor_claim_listener_polls_during_legacy_subprocess(listener, config, access, monkeypatch):
    entered, release, polled = Event(), Event(), Event()
    calls = []

    def run_subprocess(argv, **kwargs):
        calls.append((argv, kwargs))
        entered.set()
        assert release.wait(5), "Listener must continue before the long subprocess ends"
        return subprocess.CompletedProcess(argv, 0, '{"status":"ok","processed_messages":5}', '')

    monkeypatch.setattr(bridge, "shared_run_worker_subprocess", run_subprocess)
    monkeypatch.setattr(bridge, "handle_history_command", REAL_HISTORY_HANDLER)
    factory = getattr(bridge, "BridgeSupervisor", None)
    assert callable(factory), "Listener requires a supervisor boundary"
    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda c, s, a: factory(c, s, a, launch=lambda job: FixtureProcess()))
    listener["args"].once = False
    count = 0

    def fetch(*args):
        nonlocal count
        count += 1
        if count == 1:
            return [update(text="/update @one 5"), update(43, "/tail @two 5")]
        if count == 2:
            assert entered.wait(5)
            polled.set()
            return [update(44, "/qa @one")]
        assert listener["jobs"](), "New analytics input must be admitted while the old subprocess is blocked"
        release.set()
        raise KeyboardInterrupt()

    monkeypatch.setattr(bridge, "fetch_updates", fetch)
    try:
        with pytest.raises(KeyboardInterrupt):
            bridge.cmd_listen(listener["args"])
    finally:
        release.set()
    assert polled.is_set() and listener["offsets"] == [43, 44, 45]
    assert [argv[argv.index("--mode") + 1] for argv, _ in calls] == ["update", "tail"]
    assert all(call[1]["env"]["TELEGRAM_BOT_TOKEN"] == "999:fixture-token" for call in calls)


def test_s05_supervisor_claim_legacy_does_not_require_llm_model(listener, config, access):
    config["processing"].pop("model")
    handled = Event()
    with supervisor(config, access, launch=lambda job: pytest.fail("No queued analysis"),
                    legacy_handler=lambda *a, **kw: handled.set()) as runtime:
        assert runtime.submit_legacy(update(text="/help"))
        assert handled.wait(5)


def test_s05_supervisor_claim_listener_initializes_schema_once(listener, config, access, monkeypatch):
    factory = getattr(bridge, "BridgeSupervisor", None)
    assert callable(factory)
    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda c, s, a: factory(c, s, a, launch=lambda job: FixtureProcess()))
    calls = []
    original = history.init_db
    monkeypatch.setattr(history, "init_db", lambda conn: (calls.append(1), original(conn))[-1])
    assert bridge.cmd_listen(listener["args"]) == 0
    assert calls == [1], "Startup migration must not scan the history again for admission"


def test_s05_supervisor_claim_listener_polls_during_analysis_worker(listener, config, access, monkeypatch):
    launched = Event()
    process = FixtureProcess()
    runtime = supervisor(config, access, launch=lambda job: (launched.set(), process)[1])
    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda *a: runtime)
    listener["args"].once = False
    count = 0

    def fetch(*a):
        nonlocal count
        count += 1
        if count == 1:
            return [update(text="/qa @one")]
        if count == 2:
            assert launched.wait(5)
            return [update(43, '/search @two "условия"')]
        jobs = listener["jobs"]()
        assert len(jobs) == 2
        assert sorted(job["status"] for job in jobs) == ["queued", "running"]
        assert process.returncode is None
        raise KeyboardInterrupt()

    monkeypatch.setattr(bridge, "fetch_updates", fetch)
    with pytest.raises(KeyboardInterrupt):
        bridge.cmd_listen(listener["args"])
    assert listener["offsets"] == [43, 44]


def test_s05_supervisor_claim_legacy_queue_is_bounded_and_serial(listener, config, access):
    config["analysis"]["max_queued_jobs"] = 1
    entered, release = Event(), Event()
    calls = []

    def handler(token, cfg, event, **kw):
        calls.append(event["update_id"])
        entered.set()
        assert release.wait(5)

    with supervisor(config, access, launch=lambda job: FixtureProcess(), legacy_handler=handler) as runtime:
        assert runtime.submit_legacy(update(42, "/help"))
        assert entered.wait(5)
        assert runtime.submit_legacy(update(43, "/help"))
        assert not runtime.submit_legacy(update(44, "/help"))
        release.set()
    assert calls == [42, 43]


def test_s05_supervisor_claim_bad_analytics_settings_do_not_break_legacy(listener, config, access):
    config["analysis"]["max_running_jobs"] = 0
    handled = Event()
    with supervisor(config, access, launch=lambda job: pytest.fail("Analytics is disabled"),
                    legacy_handler=lambda *a, **kw: handled.set()) as runtime:
        assert runtime.submit_legacy(update(text="/help"))
        assert handled.wait(5)


def test_s05_supervisor_claim_launch_uses_bounded_private_child_environment(listener, config, access, monkeypatch):
    job = seed_job(config, access)
    monkeypatch.setenv("UNRELATED_FIXTURE_SECRET", "never pass this")
    calls = []
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or FixtureProcess())
    runtime = supervisor(config, access, launch=None)
    runtime._launch(job)
    argv, options = calls[0]
    assert argv == [sys.executable, str(history.APP_DIR / "telegram_analysis.py"), "--db", config["paths"]["history_db"],
                    "--job-id", job["job_id"], "--generation", str(job["generation"])]
    assert "Какие банки" not in str(argv)
    assert options["env"]["TELEGRAM_BOT_TOKEN"] == "999:fixture-token"
    assert "UNRELATED_FIXTURE_SECRET" not in options["env"]
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert options["start_new_session"]


def test_s05_supervisor_claim_denied_legacy_cannot_fill_work_queue(listener, config, access, monkeypatch):
    listener["events"] = [update(text="/update @one 5")]
    listener["events"][0]["message"]["from"]["id"] = 99
    runtime = supervisor(config, access, launch=lambda job: FixtureProcess())
    monkeypatch.setattr(runtime, "submit_legacy", lambda event: pytest.fail("Denied caller must not occupy the legacy lane"))
    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda *a: runtime)
    assert bridge.cmd_listen(listener["args"]) == 0
    assert len(listener["legacy"]) == 1  # Existing handler owns the rejection wording.


def control(store, config, access, command, job, *, event=None, now=NOW):
    function = getattr(analysis, "control_analysis_job", None)
    assert callable(function), "Job controls must authorize the owner and preserve durable state"
    return function(store, config, event or update(), command=command, job_id=job["job_id"],
                    access=access, now=now, config_dir=CONFIG_DIR)


def stopped_job(store, config, access, status="interrupted"):
    queued, _ = admit(store, config, access, update())
    running = claim(store, queued["job_id"])
    store.save_checkpoint(running["job_id"], {"resumable": True, "cursor": 50}, generation=running["generation"], max_bytes=1000)
    finish(store, running, status)
    return store.get_job(running["job_id"])


def test_s06_controls_and_resume_status_exposes_only_safe_fields(store, config, access):
    queued, _ = admit(store, config, access, update())
    store.conn.execute("UPDATE analysis_jobs SET checkpoint_json=?,delivery_json=? WHERE job_id=?",
                       ('{"private":"fixture-secret-token"}', '{"outcome":"failed"}', queued["job_id"]))
    config["processing"].pop("model")
    result = control(store, config, access, "status", queued)
    assert result["status"] == "queued" and result["delivery"] == "failed" and result["phase"] == "sync"
    assert result["job_id"] == queued["job_id"] and result["llm_calls"] == 0
    assert not {"request", "checkpoint", "result", "request_hash", "requester_id", "destination"} & result.keys()
    assert "fixture-secret-token" not in str(result) and "Какие банки" not in str(result)


@pytest.mark.parametrize("command", ["status", "cancel", "resume"])
@pytest.mark.parametrize("who", ["other_user", "other_chat", "revoked"])
def test_s06_controls_and_resume_reauthorize_owner(store, config, access, command, who, monkeypatch):
    job = stopped_job(store, config, access)
    event = update()
    if who == "other_user":
        event["message"]["from"]["id"] = 8
        access["users"] = frozenset({"7", "8"})
    elif who == "other_chat":
        event["message"]["chat"]["id"] = 101
        access["chats"] = frozenset({"100", "101"})
    else:
        access["users"] = frozenset({"8"})
    before = store.get_job(job["job_id"])
    with pytest.raises(analysis.AnalysisRejection, match="forbidden" if who == "revoked" else "not_found"):
        control(store, config, access, command, job, event=event)
    assert store.get_job(job["job_id"]) == before


@pytest.mark.parametrize("running", [True, False])
def test_s06_controls_and_resume_cancel_intent_is_durable(store, config, access, running):
    job, _ = admit(store, config, access, update())
    if running:
        job = claim(store, job["job_id"])
    result = control(store, config, access, "cancel", job)
    assert result["cancel_requested"] == 1
    assert result["status"] == ("running" if running else "cancelled")
    persisted = store.get_job(job["job_id"])
    assert persisted["accepted_at"] == job["accepted_at"] and persisted["cutoff_at"] == job["cutoff_at"]
    assert control(store, config, access, "cancel", job)["status"] == result["status"]


@pytest.mark.parametrize("status", ["interrupted", "timed_out", "partial", "failed"])
def test_s06_controls_and_resume_keeps_cutoff_request_and_cumulative_ledger(store, config, access, status):
    job = stopped_job(store, config, access, status)
    store.conn.execute("UPDATE analysis_jobs SET llm_calls=2,provider_requests=4,used_tokens=100,reserved_tokens=80,downloaded_messages=90 WHERE job_id=?",
                       (job["job_id"],))
    before = store.get_job(job["job_id"])
    result = control(store, config, access, "resume", job)
    after = store.get_job(job["job_id"])
    assert result["status"] == "queued" and after["finished_at"] is None
    assert after["generation"] > before["generation"]
    for key in ("cutoff_at", "accepted_at", "request", "checkpoint", "llm_calls", "provider_requests",
                "used_tokens", "reserved_tokens", "downloaded_messages", "intermediate_bytes"):
        assert after[key] == before[key], key
    with pytest.raises(StaleGenerationError):
        store.save_checkpoint(job["job_id"], {"late": True}, generation=before["generation"], max_bytes=1000)
    assert control(store, config, access, "resume", job)["status"] == "queued"
    assert store.get_job(job["job_id"]) == after


@pytest.mark.parametrize("reason", ["not_resumable", "unknown_delivery", "budget_exhausted", "policy_changed", "forbidden"])
def test_s38_failed_resume_retains_checkpoint_delivery_acl_and_budget_guards(store, config, access, reason):
    job = stopped_job(store, config, access, "failed")
    if reason == "not_resumable":
        store.conn.execute("UPDATE analysis_jobs SET checkpoint_json='{}' WHERE job_id=?", (job["job_id"],))
    elif reason == "unknown_delivery":
        store.conn.execute("UPDATE analysis_jobs SET delivery_json=? WHERE job_id=?", ('{"outcome":"unknown"}', job["job_id"]))
    elif reason == "budget_exhausted":
        store.conn.execute("UPDATE analysis_jobs SET used_tokens=? WHERE job_id=?",
            (job["request"]["settings"]["analysis"]["max_total_tokens"], job["job_id"]))
    elif reason == "policy_changed":
        config["analysis"]["max_llm_calls"] += 1
    else:
        access["users"] = frozenset({"8"})
    before = store.get_job(job["job_id"])
    with pytest.raises(analysis.AnalysisRejection, match=reason):
        control(store, config, access, "resume", job)
    assert store.get_job(job["job_id"]) == before
    if reason == "not_resumable":
        with pytest.raises(StaleGenerationError):
            store.resume_job(job["job_id"], generation=job["generation"], phase=job["phase"], max_queued_jobs=1, now=NOW)


@pytest.mark.parametrize("counter,setting", [("llm_calls", "max_llm_calls"), ("provider_requests", "max_provider_requests"),
                                            ("used_tokens", "max_total_tokens"), ("reserved_tokens", "max_total_tokens")])
def test_s06_controls_and_resume_exhausted_budget_is_not_reset(store, config, access, counter, setting):
    job = stopped_job(store, config, access)
    cap = job["request"]["settings"]["analysis"][setting]
    store.conn.execute(f"UPDATE analysis_jobs SET {counter}=? WHERE job_id=?", (cap, job["job_id"]))
    before = store.get_job(job["job_id"])
    with pytest.raises(analysis.AnalysisRejection, match="budget_exhausted"):
        control(store, config, access, "resume", job)
    assert store.get_job(job["job_id"]) == before


def test_s06_controls_and_resume_saved_result_skips_extraction_even_with_exhausted_budget(store, config, access):
    job = stopped_job(store, config, access, "completed")
    cap = job["request"]["settings"]["analysis"]["max_llm_calls"]
    store.conn.execute("UPDATE analysis_jobs SET result_json=?,delivery_json=?,llm_calls=? WHERE job_id=?",
                       ('{"fixture_result":true}', '{"outcome":"failed"}', cap, job["job_id"]))
    result = control(store, config, access, "resume", job)
    persisted = store.get_job(job["job_id"])
    assert result["status"] == "queued" and result["phase"] == "verify"
    assert persisted["result"] == {"fixture_result": True} and persisted["llm_calls"] == cap


def test_s06_controls_and_resume_unknown_delivery_never_auto_repeats(store, config, access):
    job = stopped_job(store, config, access, "completed")
    store.conn.execute("UPDATE analysis_jobs SET result_json=?,delivery_json=? WHERE job_id=?",
                       ('{"fixture_result":true}', '{"outcome":"unknown"}', job["job_id"]))
    with pytest.raises(analysis.AnalysisRejection, match="unknown_delivery"):
        control(store, config, access, "resume", job)
    assert store.get_job(job["job_id"])["status"] == "completed"


@pytest.mark.parametrize("reason", ["expired", "not_resumable", "policy_changed", "queue_full"])
def test_s06_controls_and_resume_requires_valid_checkpoint_and_current_limits(store, config, access, reason):
    if reason == "queue_full":
        config["analysis"]["max_queued_jobs"] = 1
    job = stopped_job(store, config, access, "partial" if reason == "not_resumable" else "interrupted")
    now = NOW
    if reason == "expired":
        now += timedelta(days=8)
    elif reason == "not_resumable":
        store.conn.execute("UPDATE analysis_jobs SET checkpoint_json='{}' WHERE job_id=?", (job["job_id"],))
    elif reason == "policy_changed":
        config["analysis"]["max_llm_calls"] += 1
    else:
        admit(store, config, access, update(43))
    before = store.get_job(job["job_id"])
    with pytest.raises(analysis.AnalysisRejection, match=reason):
        control(store, config, access, "resume", job, now=now)
    assert store.get_job(job["job_id"]) == before


def test_s06_controls_and_resume_live_worker_lock_prevents_requeue(listener, config, access):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        job = stopped_job(repo, config, access)
        with ownership(path, job["job_id"]):
            with pytest.raises(analysis.AnalysisRejection, match="still_running"):
                control(repo, config, access, "resume", job)
    finally:
        conn.close()


@pytest.mark.parametrize("command", ["status", "cancel", "resume"])
def test_s06_controls_and_resume_listener_uses_priority_lane(listener, config, access, command, monkeypatch):
    # Retention must use the same fixed time as this test's admitted job.
    control_job = analysis.control_analysis_job
    def control_at_fixture_time(*args, **kwargs):
        return control_job(*args, **{**kwargs, "now": NOW})
    monkeypatch.setattr(analysis, "control_analysis_job", control_at_fixture_time)
    conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
    try:
        job = stopped_job(AnalysisStore(conn), config, access) if command == "resume" else admit(AnalysisStore(conn), config, access, update())[0]
    finally:
        conn.close()
    listener["events"] = [update(43, f"/{command} {job['job_id']}")]
    monkeypatch.setattr(bridge, "handle_history_command", lambda *a, **kw: pytest.fail("Controls must bypass legacy lane"))
    assert bridge.cmd_listen(listener["args"]) == 0
    assert listener["offsets"] == [44] and len(listener["sent"]) == 1
    text = listener["sent"][0][1]
    assert job["job_id"] in text and "Доставка:" in text and "Статус:" in text


def test_s06_controls_and_resume_status_arrives_while_legacy_is_waiting(listener, config, access, monkeypatch):
    job = seed_job(config, access, event=update(40))
    entered, release = Event(), Event()
    launched = Event()
    status_seen = Event()
    original_send = bridge.send_text_message

    def handler(*a, **kw):
        entered.set()
        assert release.wait(5), "Status must arrive before the long legacy command ends"

    def send(token, chat, text, **kw):
        original_send(token, chat, text, **kw)
        if job["job_id"] in text and "Статус: running" in text:
            status_seen.set()

    runtime = supervisor(config, access, launch=lambda job: (launched.set(), FixtureProcess())[1], legacy_handler=handler)
    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda *a: runtime)
    monkeypatch.setattr(bridge, "send_text_message", send)
    listener["args"].once = False
    count = 0

    def fetch(*a):
        nonlocal count
        count += 1
        if count == 1:
            assert launched.wait(5)
            return [update(text="/update @one 5")]
        if count == 2:
            assert entered.wait(5)
            return [update(43, f"/status {job['job_id']}")]
        assert status_seen.is_set()
        release.set()
        raise KeyboardInterrupt()

    monkeypatch.setattr(bridge, "fetch_updates", fetch)
    try:
        with pytest.raises(KeyboardInterrupt):
            bridge.cmd_listen(listener["args"])
    finally:
        release.set()


@pytest.mark.parametrize("text", ["/status", "/cancel nope", '/resume "bad', "/status " + "a" * 32 + " extra"])
def test_s06_controls_and_resume_malformed_control_is_safe(listener, text, monkeypatch):
    listener["events"] = [update(text=text)]
    monkeypatch.setattr(bridge, "handle_history_command", lambda *a, **kw: pytest.fail("Malformed control is still control input"))
    assert bridge.cmd_listen(listener["args"]) == 0
    assert not listener["jobs"]()
    assert listener["sent"] == [("100", analysis.AnalysisRejection.MESSAGES["invalid_control"])]


def test_s06_controls_and_resume_worker_checks_cancellation_before_executor(listener, config, access):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])
        control(repo, config, access, "cancel", running)
        assert run_worker(repo, path, running, lambda *a: pytest.fail("Cancelled job must not execute")) == 0
        assert repo.get_job(running["job_id"])["status"] == "cancelled"
    finally:
        conn.close()


def test_s06_controls_and_resume_recovery_confirms_stopped_cancelled_worker(listener, config, access):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])
        control(repo, config, access, "cancel", running)
        with ownership(path, running["job_id"]):
            bridge.recover_analysis_jobs(repo, path, now=NOW)
            assert repo.get_job(running["job_id"])["status"] == "running"
        bridge.recover_analysis_jobs(repo, path, now=NOW)
        assert repo.get_job(running["job_id"])["status"] == "cancelled"
    finally:
        conn.close()


@pytest.mark.parametrize("outcome", [{"private": "fixture-secret-token"}, ["unknown"]])
def test_s06_controls_and_resume_malformed_delivery_is_unknown(store, config, access, outcome):
    from telegram_connector.analysis_store import canonical_json
    job = stopped_job(store, config, access, "completed")
    store.conn.execute("UPDATE analysis_jobs SET result_json='{}',delivery_json=? WHERE job_id=?",
                       (canonical_json({"outcome": outcome}), job["job_id"]))
    assert control(store, config, access, "status", job)["delivery"] == "unknown"
    with pytest.raises(analysis.AnalysisRejection, match="unknown_delivery"):
        control(store, config, access, "resume", job)


def test_s06_controls_and_resume_partial_requires_explicit_resumable_boolean(store, config, access):
    job = stopped_job(store, config, access, "partial")
    store.conn.execute("UPDATE analysis_jobs SET checkpoint_json=? WHERE job_id=?",
                       ('{"resumable":"false"}', job["job_id"]))
    with pytest.raises(analysis.AnalysisRejection, match="not_resumable"):
        control(store, config, access, "resume", job)


def test_s06_controls_and_resume_concurrent_replay_enqueues_once(listener, config, access):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        job = stopped_job(AnalysisStore(conn), config, access)
    finally:
        conn.close()
    barrier = Barrier(2)

    def resume():
        connection = common_sqlite.connect_sqlite(path)
        try:
            barrier.wait(timeout=5)
            try:
                return control(AnalysisStore(connection), config, access, "resume", job)["status"]
            except analysis.AnalysisRejection as exc:
                # The OS lock is nonblocking: an overlapping control may receive
                # a busy response. It must not requeue or reset the job a second time.
                assert str(exc) in {"still_running", "state_changed"}
                return str(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resume) for _ in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]
        assert "queued" in outcomes
    persisted = listener["jobs"]()[0]
    assert persisted["generation"] == job["generation"] + 1
    assert persisted["llm_calls"] == job["llm_calls"]


def test_s06_controls_and_resume_real_worker_cooperatively_confirms_cancellation(listener, config, access):
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    repo = AnalysisStore(conn)
    queued, _ = admit(repo, config, access, update())
    running = claim(repo, queued["job_id"])
    code = (
        "import sys,time; from pathlib import Path; from common import sqlite as db;"
        "from telegram_connector.analysis_store import AnalysisStore;"
        "from telegram_connector.telegram_analysis import run_analysis;"
        "\nconn=db.connect_sqlite(sys.argv[1])"
        "\ndef executor(store,job):"
        "\n print('ready',flush=True)"
        "\n while not store.get_job(job['job_id'])['cancel_requested']: time.sleep(0.01)"
        "\n return 'cancelled'"
        "\ntry: code=run_analysis(AnalysisStore(conn),Path(sys.argv[1]),job_id=sys.argv[2],generation=int(sys.argv[3]),executor=executor)"
        "\nfinally: conn.close()"
        "\nraise SystemExit(code)"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(path), running["job_id"], str(running["generation"])],
                             cwd=history.APP_DIR.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5)
        assert child.stdout.readline().strip() == "ready" and child.poll() is None
        result = control(repo, config, access, "cancel", running)
        assert result["cancel_requested"] == 1 and result["status"] == "running"
        child.communicate(timeout=5)
        assert child.returncode == 0
        assert repo.get_job(running["job_id"])["status"] == "cancelled"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
        conn.close()


def test_s05_supervisor_claim_relative_db_path_is_frozen_before_child_cwd(listener, config, access, monkeypatch):
    job = seed_job(config, access)
    path = Path(config["paths"]["history_db"])
    monkeypatch.chdir(path.parent)
    config["paths"]["history_db"] = path.name
    calls = []
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or FixtureProcess())
    runtime = supervisor(config, access, launch=None)
    runtime._launch(job)
    argv = calls[0][0]
    assert argv[argv.index("--db") + 1] == str(path.resolve()), "Child cwd must not select another database"


@pytest.mark.parametrize("status", ["interrupted", "failed", "timed_out"])
def test_s06_controls_and_resume_cancel_racing_terminal_commit_is_not_lost(store, config, access, status):
    queued, _ = admit(store, config, access, update())
    running = claim(store, queued["job_id"])
    # The worker observed its outcome just before cancel intent was committed.
    control(store, config, access, "cancel", running)
    finish(store, running, status)
    result = store.get_job(running["job_id"])
    assert result["status"] == "cancelled" and result["cancel_requested"] == 1


def bounded_worker(store, path, job, *, command=None):
    function = getattr(analysis, "run_analysis_with_ttl", None)
    assert callable(function), "Every worker launch needs an outer TTL independent of inference/cleanup"
    return function(store, path, job_id=job["job_id"], generation=job["generation"], worker_command=command)


def ttl_job(repo, config, access, *, phase="extract", seconds=1.0):
    from telegram_connector.analysis_store import canonical_json
    queued, _ = admit(repo, config, access, update())
    running = claim(repo, queued["job_id"])
    # Short fixture deadlines do not change operator defaults or their validation.
    snapshot = running["request"]
    snapshot["settings"]["bridge"]["worker_process_timeout_seconds"] = seconds
    snapshot["settings"]["process"].update(default_termination_grace_seconds=0.1, poll_interval_seconds=0.02)
    repo.conn.execute("UPDATE analysis_jobs SET request_json=?,phase=?,used_tokens=17,reserved_tokens=80,llm_calls=2,provider_requests=3 WHERE job_id=?",
                      (canonical_json(snapshot), phase, running["job_id"]))
    repo.save_checkpoint(running["job_id"], {"cursor": 90}, generation=running["generation"], max_bytes=1000)
    return repo.get_job(running["job_id"])


def fixture_worker_command(path, job, ready, mode="hang"):
    code = (
        "import os,signal,subprocess,sys,time;from pathlib import Path;from common import sqlite as db;"
        "from common.json_io import write_json_atomic;from telegram_connector.analysis_store import AnalysisStore;"
        "from telegram_connector.telegram_analysis import run_analysis;"
        "\nconn=db.connect_sqlite(sys.argv[1])"
        "\ndef executor(store,job):"
        "\n if sys.argv[5] != 'hang': signal.signal(signal.SIGTERM,signal.SIG_IGN)"
        "\n descendant=None"
        "\n if sys.argv[5] == 'descendant':"
        "\n  marker=Path(sys.argv[4]+'.descendant')"
        "\n  nested=\"import signal,time,sys;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(sys.argv[1]).write_text('ready');time.sleep(30)\""
        "\n  descendant=subprocess.Popen([sys.executable,'-c',nested,str(marker)])"
        "\n  while not marker.exists(): time.sleep(0.01)"
        "\n checkpoint={**job['checkpoint'],'fixture_started':True}"
        "\n store.save_checkpoint(job['job_id'],checkpoint,generation=job['generation'],max_bytes=job['request']['settings']['analysis']['max_intermediate_bytes'])"
        "\n write_json_atomic(Path(sys.argv[4]),{'pid':os.getpid(),'pgid':os.getpgrp(),'descendant':descendant.pid if descendant else None})"
        "\n if sys.argv[5] == 'finish': return 'interrupted'"
        "\n while True: time.sleep(30)"
        "\ntry: status=run_analysis(AnalysisStore(conn),Path(sys.argv[1]),job_id=sys.argv[2],generation=int(sys.argv[3]),executor=executor)"
        "\nfinally: conn.close()"
        "\nraise SystemExit(status)"
    )
    return [sys.executable, "-c", code, str(path), job["job_id"], str(job["generation"]), str(ready), mode]


@pytest.mark.parametrize("phase,mode", [("sync", "hang"), ("extract", "ignore_term"), ("merge", "descendant")])
def test_s07_outer_ttl_bounds_owned_worker_and_preserves_ledger(listener, config, access, tmp_path, phase, mode):
    import json
    from common import ttl_runner
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        job = ttl_job(repo, config, access, phase=phase)
        ready = tmp_path / "worker-ready.json"
        assert bounded_worker(repo, path, job, command=fixture_worker_command(path, job, ready, mode)) == 124
        assert ready.exists(), "TTL must expire during execution, not merely during interpreter startup"
        group = json.loads(ready.read_text())["pgid"]
        assert ttl_runner.wait_for_process_group_exit(group, timeout_seconds=2, poll_interval_seconds=0.02)
        stopped = repo.get_job(job["job_id"])
        assert stopped["status"] == "timed_out" and stopped["phase"] == phase
        assert stopped["checkpoint"]["cursor"] == 90
        assert stopped["checkpoint"]["last_stop"]["reason"] == "process_ttl_expired"
        for key in ("request", "accepted_at", "cutoff_at", "used_tokens", "reserved_tokens", "llm_calls", "provider_requests"):
            assert stopped[key] == job[key], key
        with ownership(path, job["job_id"]) as owned:
            assert owned is not None
    finally:
        conn.close()


def test_s07_outer_ttl_uses_frozen_launch_policy_without_digest_pause(listener, config, access, monkeypatch):
    from common import ttl_runner
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        job = claim(repo, queued["job_id"])
        calls = []
        monkeypatch.setenv("UNRELATED_FIXTURE_SECRET", "never pass")
        monkeypatch.setattr(ttl_runner, "run_with_ttl", lambda command, **options: calls.append((command, options)) or 0)
        assert bounded_worker(repo, path, job) == 0
        command, options = calls[0]
        assert options["timeout_seconds"] == 3600 and options["grace_seconds"] == 10
        assert options["sleep_policy"] == "interrupt" and not options["use_caffeinate"]
        assert options["stop_requested"] is not None
        assert "UNRELATED_FIXTURE_SECRET" not in options["env"]
        assert "--worker-inner" in command and str(path.resolve()) in command
        assert repo.get_job(job["job_id"])["status"] == "interrupted", "A bare exit 0 cannot invent a completed analytic result"
    finally:
        conn.close()


def test_s07_outer_ttl_cancel_kills_uncooperative_worker_after_durable_intent(listener, config, access, tmp_path, monkeypatch):
    from common import ttl_runner
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        job = ttl_job(repo, config, access, seconds=5)
        ready = tmp_path / "cancel-ready.json"
        original = ttl_runner.run_with_ttl
        observed = []

        def run(command, **options):
            observe = options["stop_requested"]

            def stop():
                if ready.exists() and not observed:
                    observed.append(control(repo, config, access, "cancel", job))
                return observe()

            options["stop_requested"] = stop
            return original(command, **options)

        monkeypatch.setattr(ttl_runner, "run_with_ttl", run)
        assert bounded_worker(repo, path, job, command=fixture_worker_command(path, job, ready, "ignore_term")) == 143
        assert observed[0]["status"] == "running" and observed[0]["cancel_requested"] == 1
        assert repo.get_job(job["job_id"])["status"] == "cancelled"
        assert repo.get_job(job["job_id"])["reserved_tokens"] == 80
        with ownership(path, job["job_id"]) as owned:
            assert owned is not None
    finally:
        conn.close()


def start_fixture_outer(path, job, ready, mode="ignore_term"):
    import json
    inner = fixture_worker_command(path, job, ready, mode)
    code = (
        "import json,signal,sys;from pathlib import Path;from common import sqlite as db;"
        "from telegram_connector.analysis_store import AnalysisStore;from telegram_connector import telegram_analysis as a;"
        "\nconn=db.connect_sqlite(sys.argv[1])"
        "\nsignal.signal(signal.SIGTERM,a._worker_sigterm)"
        "\ntry: status=a.run_analysis_with_ttl(AnalysisStore(conn),Path(sys.argv[1]),job_id=sys.argv[2],generation=int(sys.argv[3]),worker_command=json.loads(sys.argv[4]))"
        "\nfinally: conn.close()"
        "\nraise SystemExit(status)"
    )
    return subprocess.Popen([sys.executable, "-c", code, str(path), job["job_id"], str(job["generation"]), json.dumps(inner)],
                            cwd=history.APP_DIR.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=bridge.build_history_client_subprocess_env({"TELEGRAM_BOT_TOKEN": "999:fixture-token"}),
                            start_new_session=True)


def wait_fixture_file(path, child):
    import time
    deadline = time.monotonic() + 5
    while not path.exists() and child.poll() is None and time.monotonic() < deadline:
        Event().wait(0.01)
    assert path.exists() and child.poll() is None, "Fixture executor must report a live owned worker"


def stop_fixture_outer(child, ready):
    import json
    from common import ttl_runner
    try:
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=5)
    finally:
        if ready.exists():
            ttl_runner.send_signal_to_process_group(json.loads(ready.read_text())["pgid"], "KILL")


def test_s07_outer_ttl_external_sigterm_cleans_inner_group_and_retains_checkpoint(listener, config, access, tmp_path):
    import json
    from common import ttl_runner
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    child = None
    ready = tmp_path / "sigterm-ready.json"
    try:
        repo = AnalysisStore(conn)
        job = ttl_job(repo, config, access, seconds=10)
        child = start_fixture_outer(path, job, ready, "descendant")
        wait_fixture_file(ready, child)
        child.terminate()
        child.communicate(timeout=5)
        assert child.returncode == 143
        group = json.loads(ready.read_text())["pgid"]
        assert ttl_runner.wait_for_process_group_exit(group, timeout_seconds=2, poll_interval_seconds=0.02)
        stopped = repo.get_job(job["job_id"])
        assert stopped["status"] == "interrupted" and stopped["reserved_tokens"] == 80
        assert stopped["checkpoint"]["last_stop"]["reason"] == "worker_terminated"
        with ownership(path, job["job_id"]) as owned:
            assert owned is not None
    finally:
        if child is not None:
            stop_fixture_outer(child, ready)
        conn.close()


def test_s07_outer_ttl_resume_gets_fresh_window_with_same_budget(listener, config, access, tmp_path, monkeypatch):
    from common import ttl_runner
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        job = claim(repo, queued["job_id"])
        repo.conn.execute("UPDATE analysis_jobs SET used_tokens=17,reserved_tokens=80,llm_calls=2 WHERE job_id=?", (job["job_id"],))
        before = repo.get_job(job["job_id"])
        original = ttl_runner.run_with_ttl
        windows = []

        def short_fixture_window(command, **options):
            windows.append(options["timeout_seconds"])
            options.update(timeout_seconds=0.8, grace_seconds=0.1, poll_interval_seconds=0.02)
            return original(command, **options)

        monkeypatch.setattr(ttl_runner, "run_with_ttl", short_fixture_window)
        first_ready = tmp_path / "first-window.json"
        assert bounded_worker(repo, path, job, command=fixture_worker_command(path, job, first_ready)) == 124
        assert first_ready.exists()
        assert control(repo, config, access, "resume", job)["status"] == "queued"
        next_job = claim(repo, job["job_id"])
        second_ready = tmp_path / "second-window.json"
        assert bounded_worker(repo, path, next_job, command=fixture_worker_command(path, next_job, second_ready, "finish")) == 0
        assert second_ready.exists() and windows == [3600, 3600]
        after = repo.get_job(job["job_id"])
        for key in ("used_tokens", "reserved_tokens", "llm_calls", "request", "cutoff_at", "accepted_at"):
            assert after[key] == before[key], key
    finally:
        conn.close()


def test_s07_outer_ttl_gate_g2_listener_replay_restart_status_and_cancel(listener, config, access, tmp_path, monkeypatch):
    from dataclasses import replace
    from common import process as common_process
    original_config = common_process.load_process_config
    monkeypatch.setattr(common_process, "load_process_config", lambda *a, **kw: replace(
        original_config(*a, **kw), poll_interval_seconds=0.02, default_termination_grace_seconds=0.1))
    ready = tmp_path / "g2-ready.json"
    children = []
    launched = Event()
    path = Path(config["paths"]["history_db"])

    def launch(job):
        child = start_fixture_outer(path, job, ready)
        children.append(child)
        launched.set()
        return child

    original_send = bridge.send_text_message

    def acknowledge(token, chat, text, **kw):
        original_send(token, chat, text, **kw)
        assert launched.wait(5)
        wait_fixture_file(ready, children[0])

    monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda c, s, a: supervisor(c, a, launch=launch))
    monkeypatch.setattr(bridge, "send_text_message", acknowledge)
    listener["events"] = [update(text="/topics @one"), update(text="/topics @one")]
    try:
        assert bridge.cmd_listen(listener["args"]) == 0
        assert len(children) == 1 and len(listener["jobs"]()) == 1
        job = listener["jobs"]()[0]
        assert job["status"] == "running" and children[0].poll() is None
        assert job["checkpoint"].get("fixture_started") is True, "Gate G2 must carry worker progress durably through restart"
        # A fresh supervisor has no Popen handle for this still-live owned worker.
        monkeypatch.setattr(bridge, "send_text_message", original_send)
        monkeypatch.setattr(bridge, "create_bridge_supervisor", lambda c, s, a: supervisor(
            c, a, launch=lambda job: pytest.fail("Restart must not execute a live old job again")))
        listener["sent"].clear()
        listener["events"] = [update(43, f"/status {job['job_id']}"), update(44, f"/cancel {job['job_id']}")]
        assert bridge.cmd_listen(listener["args"]) == 0
        assert "Статус: running" in listener["sent"][0][1]
        assert "остановка worker ещё не подтверждена" in listener["sent"][1][1]
        children[0].communicate(timeout=5)
        assert children[0].returncode == 143
        stopped = listener["jobs"]()[0]
        assert stopped["status"] == "cancelled" and stopped["cancel_requested"] == 1
        assert stopped["cutoff_at"] == job["cutoff_at"]
        assert stopped["checkpoint"]["fixture_started"] is True
        assert listener["offsets"] == [43, 43, 44, 45]
        with ownership(path, job["job_id"]) as owned:
            assert owned is not None
    finally:
        for child in children:
            stop_fixture_outer(child, ready)


def test_s07_outer_ttl_stop_observer_reads_only_generation_and_intent(store, config, access):
    queued, _ = admit(store, config, access, update())
    running = claim(store, queued["job_id"])
    observe = getattr(store, "worker_stop_requested", None)
    assert callable(observe), "TTL polling must not deserialize request/checkpoint JSON on every tick"
    store.conn.execute("UPDATE analysis_jobs SET checkpoint_json='not-json' WHERE job_id=?", (running["job_id"],))
    assert not observe(running["job_id"], generation=running["generation"])
    finish(store, running, "completed")
    assert not observe(running["job_id"], generation=running["generation"]), "A normal terminal commit must not cancel its own cleanup"
    assert observe(running["job_id"], generation=running["generation"] + 1)
    store.conn.execute("UPDATE analysis_jobs SET cancel_requested=1 WHERE job_id=?", (running["job_id"],))
    assert observe(running["job_id"], generation=running["generation"])
    assert observe("missing", generation=0)


def test_s07_outer_ttl_stop_observer_does_not_wait_on_database_lock(listener, config, access):
    import time
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    blocker = None
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        running = claim(repo, queued["job_id"])
        # DELETE mode makes an exclusive lock block reads. The watcher must
        # remain nonblocking even if the operator changes the SQLite policy.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=2000")
        blocker = sqlite3.connect(path, isolation_level=None)
        blocker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        assert repo.worker_stop_requested(running["job_id"], generation=running["generation"]) is False
        assert time.monotonic() - started < 0.5, "SQLite busy waits must not delay the outer process deadline"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 2000
    finally:
        if blocker is not None:
            blocker.rollback()
            blocker.close()
        conn.close()


@pytest.mark.parametrize("inner", [False, True])
def test_s07_outer_ttl_public_cli_dispatches_through_wrapper(listener, config, access, monkeypatch, inner):
    import signal
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        repo = AnalysisStore(conn)
        queued, _ = admit(repo, config, access, update())
        job = claim(repo, queued["job_id"])
    finally:
        conn.close()
    calls, connections = [], []

    def entry(mode):
        def execute(store, db_path, *, job_id, generation):
            assert Path(db_path) == path and generation == job["generation"]
            assert store.get_job(job_id)["status"] == "running"
            calls.append(mode)
            connections.append(store.conn)
            return 0
        return execute

    monkeypatch.setattr(analysis, "run_analysis_with_ttl", entry("outer"))
    monkeypatch.setattr(analysis, "run_analysis", entry("inner"))
    previous = signal.getsignal(signal.SIGTERM)
    args = ["--db", str(path), "--job-id", job["job_id"], "--generation", str(job["generation"])]
    if inner:
        args.append("--worker-inner")
    assert analysis.main(args) == 0
    assert calls == ["inner" if inner else "outer"]
    assert signal.getsignal(signal.SIGTERM) == previous
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
