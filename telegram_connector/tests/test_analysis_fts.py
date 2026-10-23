from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace
import tomllib
import json
import time
import sys
import asyncio

import pytest

from common import sqlite as common_sqlite
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore
from telegram_connector import telegram_analysis as analysis
from telegram_connector import telegram_bridge as bridge
from telegram_connector.analysis_store import StaleGenerationError, canonical_json


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def db():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def index_exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages_fts'").fetchone() is not None


def write(conn, text, *, peer=11, message_id=1):
    entity = SimpleNamespace(id=peer, title="Fixture", username=f"peer{peer}")
    message = SimpleNamespace(id=message_id, date=NOW, message=text, media=None, edit_date=None, sender_id=None)
    history.upsert_channel(conn, entity)
    history.upsert_message(conn, entity, message, None, None, None, None)


def matches(conn, query):
    assert index_exists(conn), "FTS-capable SQLite needs a derived index maintained by source writers"
    return [tuple(row) for row in conn.execute("""SELECT channel_id,message_id FROM messages_fts
        WHERE messages_fts MATCH ? ORDER BY channel_id,message_id""", (query,))]


def asset(conn, *, ordinal=0, message_id=1, peer=11, path="fixture.png", text=None):
    conn.execute("""INSERT INTO media_assets
        (channel_id,message_id,ordinal,media_kind,local_path,ocr_status,ocr_text,created_at)
        VALUES (?,?,?,'photo',?,'pending',?,'now')""", (peer, message_id, ordinal, path, text))


def test_s11_fts_writes_same_message_ids_in_different_peers_use_source_rowid(db):
    write(db, "Правила residencia y café", peer=11)
    write(db, "Открытие cuenta bancaria", peer=22)
    assert matches(db, "правила") == [(11, 1)]
    assert matches(db, "CAFE") == [(11, 1)]
    assert matches(db, "bancaria") == [(22, 1)]
    assert db.execute("""SELECT COUNT(*) FROM messages m JOIN messages_fts f ON f.rowid=m.rowid
        WHERE m.channel_id=f.channel_id AND m.message_id=f.message_id""").fetchone()[0] == 2


def test_s11_fts_writes_upsert_replaces_old_terms_without_duplicates_or_rowid_change(db):
    write(db, "Старое условие")
    rowid = db.execute("SELECT rowid FROM messages WHERE channel_id=11 AND message_id=1").fetchone()[0]
    write(db, "Новое правило")
    write(db, "Новое правило")
    assert not matches(db, "старое")
    assert matches(db, "правило") == [(11, 1)]
    assert db.execute("SELECT rowid FROM messages WHERE channel_id=11 AND message_id=1").fetchone()[0] == rowid
    assert db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 1


def test_s11_fts_writes_index_failure_rolls_back_canonical_source(db):
    write(db, "Первое правило")
    assert matches(db, "первое") == [(11, 1)]

    def deny_insert(action, table, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "messages_fts" else sqlite3.SQLITE_OK

    db.set_authorizer(deny_insert)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            write(db, "Второе правило")
    finally:
        db.set_authorizer(None)
    assert db.execute("SELECT text FROM messages WHERE channel_id=11 AND message_id=1").fetchone()[0] == "Первое правило"
    assert matches(db, "первое") == [(11, 1)] and not matches(db, "второе")
    assert not db.in_transaction


def test_s11_fts_writes_ocr_processor_indexes_all_assets_in_ordinal_order(db, monkeypatch):
    write(db, "Документы")
    asset(db, ordinal=2, path="second.png")
    asset(db, ordinal=0, path="first.png")

    def recognize(binary, path, **kwargs):
        assert not db.in_transaction, "External OCR must run outside SQLite write transactions"
        return "Сумма EUR" if path == "first.png" else "Срок месяц"

    monkeypatch.setattr(history, "run_tesseract", recognize)
    assert history.process_pending_ocr(db, "fixture", limit=None) == 2
    assert matches(db, "месяц") == [(11, 1)]
    assert db.execute("SELECT ocr_text FROM messages_fts").fetchone()[0] == "Сумма EUR\nСрок месяц"


def test_s11_fts_writes_delete_and_reinsert_do_not_leave_old_rowid_or_terms(db):
    write(db, "Старое условие")
    remove = getattr(history, "delete_history_message", None)
    assert callable(remove), "History deletion must own both the canonical row and its derived index"
    assert remove(db, 11, 1) is True
    assert remove(db, 11, 1) is False
    write(db, "Другой источник", peer=22, message_id=5)
    write(db, "Исправление", peer=11)
    assert not matches(db, "старое")
    assert matches(db, "источник") == [(22, 5)]
    assert matches(db, "исправление") == [(11, 1)]
    assert db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 2


def test_s11_fts_writes_unavailable_capability_keeps_ordinary_schema_and_writes(monkeypatch):
    monkeypatch.setattr(history.history_store, "fts5_available", lambda conn: False, raising=False)
    conn = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(conn)
        assert not index_exists(conn)
        write(conn, "Обычная история")
        assert conn.execute("SELECT text FROM messages").fetchone()[0] == "Обычная история"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='analysis_inputs'").fetchone()
    finally:
        conn.close()


def test_s11_fts_writes_ocr_index_failure_rolls_back_without_relabelling_as_ocr_error(db, monkeypatch):
    write(db, "Документ")
    asset(db)
    monkeypatch.setattr(history, "run_tesseract", lambda *args, **kwargs: "Распознанный срок")

    def deny_insert(action, table, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "messages_fts" else sqlite3.SQLITE_OK

    db.set_authorizer(deny_insert)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            history.process_pending_ocr(db, "fixture", limit=None)
    finally:
        db.set_authorizer(None)
    row = db.execute("SELECT ocr_status,ocr_text,ocr_error FROM media_assets").fetchone()
    assert tuple(row) == ("pending", None, None)
    assert matches(db, "документ") == [(11, 1)] and not matches(db, "срок")
    assert not db.in_transaction


def test_s11_fts_writes_corrected_and_cleared_ocr_replace_old_terms(db):
    write(db, "Документ")
    asset(db)
    history.save_media_ocr_result(db, 11, 1, 0, status="done", text="Сумма 200 EUR")
    assert matches(db, "200") == [(11, 1)]
    history.save_media_ocr_result(db, 11, 1, 0, status="done", text="Сумма 500 EUR")
    assert not matches(db, "200") and matches(db, "500") == [(11, 1)]
    history.save_media_ocr_result(db, 11, 1, 0, status="done", text=None)
    assert not matches(db, "500") and matches(db, "документ") == [(11, 1)]


def test_s11_fts_writes_delete_failure_keeps_message_and_media(db):
    write(db, "Исходное сообщение")
    asset(db)

    def deny_delete(action, table, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE and table == "messages_fts" else sqlite3.SQLITE_OK

    db.set_authorizer(deny_delete)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            history.delete_history_message(db, 11, 1)
    finally:
        db.set_authorizer(None)
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 1
    assert matches(db, "исходное") == [(11, 1)]


def test_s11_fts_writes_media_deletion_removes_its_ocr_from_index(db):
    write(db, "Документ")
    asset(db, ordinal=0)
    asset(db, ordinal=2)
    history.save_media_ocr_result(db, 11, 1, 0, status="done", text="Первый")
    history.save_media_ocr_result(db, 11, 1, 2, status="done", text="Второй")
    remove = getattr(history, "delete_media_asset", None)
    assert callable(remove), "A media deletion must update its message's aggregate FTS row"
    assert remove(db, 11, 1, 0) is True
    assert remove(db, 11, 1, 0) is False
    assert not matches(db, "первый") and matches(db, "второй") == [(11, 1)]
    assert db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 1


def test_s11_fts_writes_initialization_is_idempotent_and_does_not_scan_old_history(monkeypatch):
    monkeypatch.setattr(history.history_store, "fts5_available", lambda conn: False)
    conn = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(conn)
        write(conn, "Архивная запись")
        assert not index_exists(conn)
        monkeypatch.undo()
        history.init_db(conn)
        history.init_db(conn)
        assert index_exists(conn)
        assert conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 0
        write(conn, "Новая версия архивной записи")
        assert matches(conn, "архивной") == [(11, 1)]
        history.init_db(conn)
        assert matches(conn, "версия") == [(11, 1)]
    finally:
        conn.close()


def test_s11_fts_writes_addressing_uses_source_primary_key_and_fts_rowid(db):
    write(db, "Адресация")
    statements = []
    db.set_trace_callback(statements.append)
    try:
        write(db, "Исправленная адресация")
        history.delete_history_message(db, 11, 1)
    finally:
        db.set_trace_callback(None)
    replace = next(sql for sql in statements if sql.startswith("INSERT OR REPLACE INTO messages_fts"))
    remove = next(sql for sql in statements if sql.startswith("DELETE FROM messages_fts"))
    replace_plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + replace)]
    remove_plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + remove)]
    assert any("SEARCH m USING INDEX" in step for step in replace_plan)
    assert any("SEARCH messages USING" in step for step in remove_plan)
    assert any("messages_fts VIRTUAL TABLE INDEX" in step and "=" in step for step in remove_plan)


def test_s11_fts_writes_ordinary_ocr_and_delete_still_work_without_fts_capability(monkeypatch):
    monkeypatch.setattr(history.history_store, "fts5_available", lambda conn: False)
    conn = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(conn)
        write(conn, "Обычный режим")
        asset(conn)
        monkeypatch.setattr(history, "run_tesseract", lambda *args, **kwargs: "Срок 30 дней")
        assert history.process_pending_ocr(conn, "fixture", limit=None) == 1
        assert conn.execute("SELECT ocr_text FROM media_assets").fetchone()[0] == "Срок 30 дней"
        assert not index_exists(conn)
        assert history.delete_history_message(conn, 11, 1)
        assert conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
    finally:
        conn.close()


def test_s11_fts_writes_existing_date_index_is_upgraded_once_for_keyset_ties(db):
    write(db, "Сохраняемый текст")
    original_rowid = db.execute("SELECT rowid FROM messages").fetchone()[0]
    db.execute("DROP INDEX idx_messages_channel_date")
    db.execute("CREATE INDEX idx_messages_channel_date ON messages(channel_id,date_utc DESC)")
    history.init_db(db)
    statements = []
    db.set_trace_callback(statements.append)
    try:
        AnalysisStore(db).history_page(11, since="2026-10-01T00:00:00+00:00", until="2026-10-31T23:59:59+00:00",
                                       after=None, limit=10, max_message_id=100)
    finally:
        db.set_trace_callback(None)
    plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + statements[-1])]
    assert not any("TEMP B-TREE" in step for step in plan), "Date ties must be covered by the existing index"
    assert db.execute("SELECT rowid FROM messages").fetchone()[0] == original_rowid
    assert matches(db, "сохраняемый") == [(11, 1)]

    def forbid_rebuild(action, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_INDEX else sqlite3.SQLITE_OK

    db.set_authorizer(forbid_rebuild)
    try:
        history.init_db(db)
    finally:
        db.set_authorizer(None)


def test_s11_fts_writes_upsert_without_removed_media_drops_obsolete_ocr_but_keeps_local_file(db, tmp_path):
    entity = SimpleNamespace(id=11, title="Fixture", username="fixture")
    message = SimpleNamespace(id=1, date=NOW, message="Подпись", media=SimpleNamespace(), photo=True,
                              edit_date=None, sender_id=None)
    path = tmp_path / "original.png"
    path.write_bytes(b"fixture")
    history.upsert_channel(db, entity)
    history.upsert_message(db, entity, message, None, None, str(path), len(b"fixture"))
    history.save_media_ocr_result(db, 11, 1, 0, status="done", text="Прежние условия")
    assert matches(db, "прежние") == [(11, 1)]
    message.media, message.photo, message.message = None, None, "Исправленный текст"
    history.upsert_message(db, entity, message, None, None, None, None)
    assert not matches(db, "прежние")
    assert matches(db, "исправленный") == [(11, 1)]
    assert db.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
    assert path.exists()


@pytest.mark.parametrize("command", ["topics", "qa", "search"])
def test_s11_fts_writes_shared_manifest_and_digest_read_without_fts(command, monkeypatch):
    monkeypatch.syspath_prepend(str(history.APP_DIR))
    from telegram_connector import telegram_digest as digest

    monkeypatch.setattr(history.history_store, "fts5_available", lambda conn: False)
    conn = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(conn)
        write(conn, "Практический срок 30 дней")
        folder = history.APP_DIR / "config"
        config = tomllib.loads((folder / "runtime.example.toml").read_text())
        config["processing"]["model"] = "fixture-model"
        query = ' "Практический срок" --mode full' if command == "search" else ""
        event = {"update_id": 10, "message": {"chat": {"id": 100}, "from": {"id": 7},
                  "text": f"/{command} @fixture since=today until=today{query}"}}
        access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
        store = AnalysisStore(conn)
        queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999",
                                               now=NOW, config_dir=folder)
        job = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
        checkpoint = {"history": {"refs": {"@fixture": 11}, "sources": {"11": {"state": "complete", "mode": "reuse"}}}}
        store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
        manifest = analysis.build_all_messages_manifest(store, job["job_id"], generation=job["generation"])
        assert manifest["selected"] == 1 and not manifest["partial"]
        values = list(analysis.iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"]))
        assert values[0]["message"]["text"] == "Практический срок 30 дней"
        legacy = list(digest.iter_channel_messages(conn, channel="11", since="2026-10-16", until="2026-10-16"))
        assert legacy[0]["text"] == "Практический срок 30 дней"
        assert not index_exists(conn)
    finally:
        conn.close()


def test_s11_fts_writes_existing_index_with_missing_backend_does_not_block_ordinary_runtime(tmp_path, monkeypatch):
    path = tmp_path / "unsupported-index.sqlite3"
    conn = common_sqlite.connect_sqlite(path)
    try:
        history.init_db(conn)
        write(conn, "Исходная история")
        definition = conn.execute("SELECT sql FROM sqlite_master WHERE name='messages_fts'").fetchone()[0]
        # Fixture-only catalog edit emulates opening an indexed database with a
        # SQLite build that cannot instantiate the virtual-table backend.
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=? WHERE name='messages_fts'",
                     (definition.replace("USING fts5(", "USING fts5_unavailable("),))
        conn.execute("PRAGMA writable_schema=OFF")
    finally:
        conn.close()
    monkeypatch.setattr(history.history_store, "fts5_available", lambda conn: False)
    reopened = common_sqlite.connect_sqlite(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such module"):
            reopened.execute("SELECT * FROM messages_fts").fetchall()
        history.init_db(reopened)
        write(reopened, "Исправленная история")
        assert reopened.execute("SELECT text FROM messages").fetchone()[0] == "Исправленная история"
        assert history.delete_history_message(reopened, 11, 1)
    finally:
        reopened.close()


def queue_index(conn, *, rebuild=False, batch=2, ttl=None):
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["sync"]["batch_size"] = batch
    if ttl is not None:
        config["bridge"]["worker_process_timeout_seconds"] = str(ttl)
    # An internal index task must not require a model, prompts or Telegram secrets.
    config["processing"]["model"] = ""
    config["analysis_prompts"]["file"] = "not-present-index-prompts.toml"
    function = getattr(analysis, "enqueue_fts_backfill", None)
    assert callable(function), "Index construction needs a durable internal job in the shared queue"
    job = function(AnalysisStore(conn), config, rebuild=rebuild, now=NOW)
    if ttl is not None:
        # Legacy config clamps tiny TTLs; shorten only this isolated fixture's
        # frozen settings, as the outer-runner tests do.
        job["request"]["settings"]["bridge"]["worker_process_timeout_seconds"] = ttl
        job["request"]["settings"]["process"]["poll_interval_seconds"] = 0.02
        job["request"]["settings"]["process"]["default_termination_grace_seconds"] = 0.1
        conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(job["request"]), job["job_id"]))
    return job


def index_page(conn, job):
    function = getattr(history, "backfill_fts_page", None)
    assert callable(function), "Index pages and their resume cursor must commit atomically"
    return function(AnalysisStore(conn), job["job_id"], generation=job["generation"])


def require_ready(conn):
    function = getattr(history, "require_fts_ready", None)
    assert callable(function), "Fast retrieval must distinguish an existing table from a completed index"
    return function(conn)


def old_history(conn, count=5):
    conn.execute("INSERT INTO channels VALUES (11,NULL,'fixture','Fixture','Channel','{}','now','now')")
    for number in range(1, count + 1):
        conn.execute("""INSERT INTO messages
            (channel_id,message_id,date_utc,text,raw_json,content_hash,imported_at)
            VALUES (11,?,?,'Архивный документ','{}','old','now')""", (number, NOW.isoformat()))


def complete_index(conn, job):
    for _ in range(20):
        state = index_page(conn, job)
        if state["state"] == "ready":
            return state
    pytest.fail("Fixture backfill failed to finish within its bounded page count")


def test_s12_fts_backfill_table_existence_is_not_readiness(db):
    assert index_exists(db)
    with pytest.raises(ValueError, match="fts_not_ready"):
        require_ready(db)


def test_s12_fts_backfill_shared_queue_resumes_pages_before_becoming_ready(db):
    old_history(db)
    queued = queue_index(db)
    assert queued["kind"] == "fts_backfill" and queued["status"] == "queued"
    assert queue_index(db)["job_id"] == queued["job_id"]
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    first = index_page(db, running)
    assert first["cursor"] > 0 and first["indexed_messages"] == 2
    assert db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 2
    with pytest.raises(ValueError, match="fts_not_ready"):
        require_ready(db)
    store.finish_job(running["job_id"], generation=running["generation"], status="interrupted", now=NOW)
    queued = queue_index(db)
    assert queued["checkpoint"]["fts"]["cursor"] == first["cursor"]
    resumed = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, resumed)
    assert require_ready(db)["state"] == "ready"
    assert store.get_job(resumed["job_id"])["status"] == "completed"
    assert len(matches(db, "архивный")) == 5


def test_s12_fts_backfill_index_failure_rolls_back_page_and_cursor(db):
    old_history(db)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)

    def deny_insert(action, table, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "messages_fts" else sqlite3.SQLITE_OK

    db.set_authorizer(deny_insert)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            index_page(db, running)
    finally:
        db.set_authorizer(None)
    current = store.get_job(running["job_id"])
    assert current["checkpoint"]["fts"]["cursor"] == 0
    assert db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 0
    assert not db.in_transaction


def test_s12_fts_backfill_rebuild_invalidates_ready_before_replacing_old_index(db):
    old_history(db, 1)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    assert require_ready(db)["state"] == "ready"
    db.execute("UPDATE messages SET text='Изменённое правило' WHERE message_id=1")  # Old writer requires rebuild.
    queued = queue_index(db, rebuild=True)
    with pytest.raises(ValueError, match="fts_not_ready"):
        require_ready(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    assert not matches(db, "архивный") and matches(db, "правило") == [(11, 1)]


def test_s12_fts_backfill_vacuum_requires_new_index_generation(db):
    old_history(db, 1)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    db.execute("VACUUM")
    with pytest.raises(ValueError, match="fts_rebuild_required"):
        require_ready(db)
    with pytest.raises(ValueError, match="fts_rebuild_required"):
        queue_index(db)
    queued = queue_index(db, rebuild=True)
    assert queued["generation"] > running["generation"]
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    assert require_ready(db)["state"] == "ready"
    assert db.execute("""SELECT COUNT(*) FROM messages m JOIN messages_fts f ON m.rowid=f.rowid
        AND m.channel_id=f.channel_id AND m.message_id=f.message_id""").fetchone()[0] == 1


def test_s12_fts_backfill_unsupported_writer_invalidates_retained_readiness(db, monkeypatch):
    old_history(db, 1)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    with monkeypatch.context() as patch:
        patch.setattr(history.history_store, "fts5_available", lambda conn: False)
        write(db, "Исправленное условие")
    with pytest.raises(ValueError, match="fts_rebuild_required"):
        require_ready(db)
    assert store.get_job(running["job_id"])["checkpoint"]["fts"]["state"] == "invalid"


def test_s12_fts_backfill_rebuild_fences_a_running_old_generation(db):
    old_history(db)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    index_page(db, running)
    rebuilt = queue_index(db, rebuild=True)
    with pytest.raises(StaleGenerationError):
        index_page(db, running)
    current = store.get_job(rebuilt["job_id"])
    assert current["status"] == "queued" and current["checkpoint"]["fts"]["cursor"] == 0
    next_run = store.claim_job(rebuilt["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, next_run)
    assert len(matches(db, "архивный")) == 5


def test_s12_fts_backfill_current_updates_deletes_and_new_writes_survive_prefix_resume(db):
    old_history(db)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    index_page(db, running)
    write(db, "Новое условие", message_id=1)
    history.delete_history_message(db, 11, 2)
    write(db, "Новая запись", message_id=99)
    complete_index(db, running)
    assert matches(db, "условие") == [(11, 1)]
    assert matches(db, "новая") == [(11, 99)]
    assert matches(db, "архивный") == [(11, 3), (11, 4), (11, 5)]
    assert require_ready(db)["state"] == "ready"


def test_s12_fts_backfill_source_read_and_replace_are_atomic_against_another_writer(tmp_path, monkeypatch):
    path = tmp_path / "concurrent.sqlite3"
    conn, other = common_sqlite.connect_sqlite(path), common_sqlite.connect_sqlite(path)
    try:
        history.init_db(conn)
        old_history(conn, 1)
        queued = queue_index(conn)
        store = AnalysisStore(conn)
        running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
        original = history._refresh_message_fts_no_tx
        observed = []
        other.execute("PRAGMA busy_timeout=0")

        def refresh(active, peer, message_id):
            assert active.in_transaction
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                write(other, "Конкурентное правило")
            observed.append(True)
            original(active, peer, message_id)

        with monkeypatch.context() as patch:
            patch.setattr(history.history_store, "_refresh_message_fts_no_tx", refresh)
            complete_index(conn, running)
        assert observed
        write(other, "Конкурентное правило")
        assert matches(conn, "конкурентное") == [(11, 1)] and not matches(conn, "архивный")
    finally:
        other.close()
        conn.close()


def test_s12_fts_backfill_ready_checkpoint_survives_ordinary_retention(db):
    old_history(db, 1)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    db.execute("""INSERT INTO analysis_jobs
        (job_id,kind,command,request_json,request_hash,accepted_at,cutoff_at,created_at,updated_at,status)
        VALUES ('expired','analysis','topics','{}','hash',?,?,?,?,'completed')""", (NOW.isoformat(),) * 4)
    prune = getattr(store, "prune_expired_analysis_jobs", None)
    assert callable(prune), "Ordinary retention must preserve the durable service readiness record"
    assert prune(before=NOW + timedelta(days=30)) == 1
    assert require_ready(db)["state"] == "ready"


def test_s12_fts_backfill_production_worker_dispatch_does_not_require_analysis_executor(db, tmp_path):
    old_history(db)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    code = analysis.run_analysis(store, tmp_path / "worker.sqlite3", job_id=running["job_id"], generation=running["generation"])
    assert code == 0
    assert store.get_job(running["job_id"])["status"] == "completed"
    assert require_ready(db)["state"] == "ready"


def test_s12_fts_backfill_cli_queues_without_resolving_telegram_or_model_secrets(tmp_path, monkeypatch, capsys):
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["paths"]["history_db"] = str(tmp_path / "cli.sqlite3")
    config["processing"]["model"] = ""
    monkeypatch.setattr(history.history_settings, "load_runtime_config", lambda: config)
    monkeypatch.setattr(history.history_settings, "resolve_runtime", lambda: pytest.fail("Local indexing must not resolve Telegram runtime"))
    parser = history.build_parser()
    assert "index-history" in parser._subparsers._group_actions[0].choices, "Index rebuild needs its planned CLI route"
    args = parser.parse_args(["index-history", "--rebuild"])
    assert args.func(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "queued" and response["job_id"]
    conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
    try:
        job = AnalysisStore(conn).get_job(response["job_id"])
        assert job["kind"] == "fts_backfill" and "processing" not in job["request"]["settings"]
    finally:
        conn.close()


def index_supervisor_config(path):
    values = tomllib.loads((history.APP_DIR / "config" / "runtime.example.toml").read_text())
    values["paths"]["history_db"] = str(path)
    values["processing"]["model"] = ""
    return values


def test_s12_fts_backfill_common_supervisor_claims_internal_job_without_telegram_principal(db, tmp_path):
    old_history(db, 1)
    queued = queue_index(db)
    launched = []
    supervisor = bridge.BridgeSupervisor(index_supervisor_config(tmp_path / "owner.sqlite3"),
        {"TELEGRAM_BOT_TOKEN": "999:fixture-token"}, {"chats": frozenset(), "users": frozenset(), "usernames": frozenset()},
        launch=lambda job: (launched.append(job) or SimpleNamespace(poll=lambda: None)))
    supervisor._tick(AnalysisStore(db))
    assert len(launched) == 1 and launched[0]["job_id"] == queued["job_id"]
    assert launched[0]["kind"] == "fts_backfill" and launched[0]["status"] == "running"


@pytest.mark.parametrize("mutation", ["version", "principal"])
def test_s12_fts_backfill_supervisor_rejects_invalid_internal_contract(db, tmp_path, mutation):
    old_history(db, 1)
    queued = queue_index(db)
    payload = queued["request"]
    if mutation == "version":
        payload["internal"]["version"] = 999
        db.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), queued["job_id"]))
    else:
        db.execute("UPDATE analysis_jobs SET requester_id='7',destination='100' WHERE job_id=?", (queued["job_id"],))
    launched = []
    supervisor = bridge.BridgeSupervisor(index_supervisor_config(tmp_path / "owner.sqlite3"),
        {"TELEGRAM_BOT_TOKEN": "999:fixture-token"}, {"chats": frozenset(), "users": frozenset(), "usernames": frozenset()},
        launch=lambda job: launched.append(job))
    supervisor._tick(AnalysisStore(db))
    assert not launched
    assert AnalysisStore(db).get_job(queued["job_id"])["status"] == "failed"


def test_s12_fts_backfill_supervisor_runs_real_outer_and_inner_workers_to_readiness(tmp_path):
    path = tmp_path / "worker.sqlite3"
    conn = common_sqlite.connect_sqlite(path)
    supervisor = None
    try:
        history.init_db(conn)
        old_history(conn)
        queued = queue_index(conn, ttl=5)
        supervisor = bridge.BridgeSupervisor(index_supervisor_config(path), {"TELEGRAM_BOT_TOKEN": "999:fixture-token"},
            {"chats": frozenset(), "users": frozenset(), "usernames": frozenset()})
        supervisor.interval = 0.02  # Fixture cadence; product defaults are unchanged.
        with supervisor:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                job = AnalysisStore(conn).get_job(queued["job_id"])
                if job["status"] == "completed" and not supervisor.active:
                    break
                if job["status"] in {"failed", "timed_out"}:
                    break
                time.sleep(0.02)
            assert job["status"] == "completed"
            assert not supervisor.active
            assert require_ready(conn)["state"] == "ready"
            assert len(matches(conn, "архивный")) == 5
    finally:
        if supervisor is not None:
            for _, process in list(supervisor.active.values()):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=8)
        conn.close()


def test_s12_fts_backfill_writes_while_queued_before_initial_clear_are_not_lost(db):
    queued = queue_index(db)
    write(db, "Поступившая запись", message_id=99)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    assert matches(db, "поступившая") == [(11, 99)]
    assert require_ready(db)["state"] == "ready"


def test_s12_fts_backfill_outer_ttl_preserves_cursor_for_explicit_resume(db, tmp_path):
    old_history(db)
    queued = queue_index(db, ttl=1)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    prefix = index_page(db, running)
    code = analysis.run_analysis_with_ttl(store, tmp_path / "ttl.sqlite3", job_id=running["job_id"],
        generation=running["generation"], worker_command=[sys.executable, "-c", "import time; time.sleep(30)"])
    assert code == 124
    stopped = store.get_job(running["job_id"])
    assert stopped["status"] == "timed_out" and stopped["checkpoint"]["fts"]["cursor"] == prefix["cursor"]
    with pytest.raises(ValueError, match="fts_not_ready"):
        require_ready(db)
    queued = queue_index(db)
    resumed = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, resumed)
    assert require_ready(db)["state"] == "ready"


def test_g3_history_manifest_and_fts_after_restart_limit_ocr_and_delete(db, tmp_path, monkeypatch):
    index_job = queue_index(db)  # Empty index job waits while the history writer runs.
    config = tomllib.loads((history.APP_DIR / "config" / "runtime.example.toml").read_text())
    config["processing"].update(model="fixture-model", ocr=False)
    config["sync"].update(sync_limit=0, batch_size=1)
    config["analysis"].update(max_messages=2, history_page_size=2)
    event = {"update_id": 100, "message": {"chat": {"id": 100}, "from": {"id": 7},
              "text": "/topics @fixture since=today until=today"}}
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    store = AnalysisStore(db)
    queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999",
                                           now=NOW, config_dir=history.APP_DIR / "config")
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    entity = SimpleNamespace(id=11, username="fixture", title="Fixture")
    posts = [SimpleNamespace(id=i, date=NOW, message=f"Практика {i}", sender_id=None, post=True,
              media=SimpleNamespace() if i == 5 else None, photo=i == 5, edit_date=None) for i in (5, 4, 3)]

    class Client:
        calls = 0

        async def get_entity(self, ref):
            assert ref == "@fixture"
            return entity

        async def get_messages(self, resolved, **options):
            self.calls += 1
            return [m for m in posts if not options["offset_id"] or m.id < options["offset_id"]][:options["limit"]]

    client = Client()
    runtime = history.RuntimeConfig(db_path=tmp_path / "history.sqlite3", media_root=tmp_path / "media",
        user_session_name=str(tmp_path / "user"), bot_session_name=str(tmp_path / "bot"),
        api_id="fixture", api_hash="fixture", phone="fixture", bot_token="fixture", user_password="",
        tesseract_binary="fixture", vision_prompt="", sync_batch_size=1, default_auth_mode="user",
        public_auth_mode="bot", private_auth_mode="user", default_channels=[])
    original = history.drain_analysis_history_page

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fixture stopped after first flush")

    with monkeypatch.context() as patch:
        patch.setattr(history.analysis_history, "drain_analysis_history_page", crash)
        with pytest.raises(RuntimeError, match="first flush"):
            asyncio.run(history.ensure_analysis_history(store, running["job_id"], runtime,
                generation=running["generation"], client=client))
    store.finish_job(running["job_id"], generation=running["generation"], status="interrupted", now=NOW)
    store.resume_job(running["job_id"], generation=running["generation"], phase="sync", max_queued_jobs=8, now=NOW)
    running = store.claim_job(running["job_id"], max_running_jobs=1, now=NOW)
    history_state = asyncio.run(history.ensure_analysis_history(store, running["job_id"], runtime,
        generation=running["generation"], client=client))
    assert client.calls == 1 and history_state["sources"]["11"]["reason"] == "download_cap"
    assert store.get_job(running["job_id"])["downloaded_messages"] == 2
    history.save_media_ocr_result(db, 11, 5, 0, status="done", text="Практический срок 30 дней")
    assert history.delete_history_message(db, 11, 4)
    manifest = analysis.build_all_messages_manifest(store, running["job_id"], generation=running["generation"])
    assert manifest["selected"] == 1 and manifest["partial"]
    write(db, "Позднее сообщение", message_id=6)
    assert store.claim_job(index_job["job_id"], max_running_jobs=1, now=NOW) is None
    store.finish_job(running["job_id"], generation=running["generation"], status="interrupted", now=NOW)
    index_running = store.claim_job(index_job["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, index_running)
    assert require_ready(db)["state"] == "ready"
    assert matches(db, "срок") == [(11, 5)] and matches(db, "позднее") == [(11, 6)]
    store.resume_job(running["job_id"], generation=running["generation"], phase="select", max_queued_jobs=8, now=NOW)
    running = store.claim_job(running["job_id"], max_running_jobs=1, now=NOW)
    values = list(analysis.iter_analysis_manifest_messages(store, running["job_id"], generation=running["generation"]))
    assert [v["message"]["message_id"] for v in values] == [5]  # Completed manifest is unchanged.
    assert values[0]["message"]["media"][0]["ocr_text"] == "Практический срок 30 дней"


def test_s12_fts_backfill_legacy_table_migration_invalidates_readiness_and_preserves_history(db):
    old_history(db, 1)
    queued = queue_index(db)
    store = AnalysisStore(db)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    complete_index(db, running)
    db.execute("ALTER TABLE messages ADD COLUMN post_author TEXT")  # Legacy migration fixture.
    history.init_db(db)
    assert db.execute("SELECT text FROM messages").fetchone()[0] == "Архивный документ"
    assert store.get_job(queued["job_id"])["checkpoint"]["fts"]["state"] == "invalid"
    with pytest.raises(ValueError, match="fts_rebuild_required"):
        require_ready(db)


def test_s12_fts_backfill_unavailable_backend_does_not_enqueue_internal_work(db, monkeypatch):
    monkeypatch.setattr(history.history_store, "fts5_available", lambda conn: False)
    with pytest.raises(ValueError, match="fts_unavailable"):
        queue_index(db)
    assert db.execute("SELECT COUNT(*) FROM analysis_jobs WHERE kind='fts_backfill'").fetchone()[0] == 0


def test_s12_fts_backfill_cli_respects_shared_queue_cap_and_reports_queue_full(tmp_path, monkeypatch, capsys):
    path = tmp_path / "queue.sqlite3"
    config = index_supervisor_config(path)
    config["analysis"]["max_queued_jobs"] = 1
    conn = common_sqlite.connect_sqlite(path)
    try:
        history.init_db(conn)
        conn.execute("""INSERT INTO analysis_jobs
            (job_id,kind,command,request_json,request_hash,accepted_at,cutoff_at,created_at,updated_at)
            VALUES ('occupied','analysis','topics','{}','hash',?,?,?,?)""", (NOW.isoformat(),) * 4)
    finally:
        conn.close()
    monkeypatch.setattr(history.history_settings, "load_runtime_config", lambda: config)
    args = history.build_parser().parse_args(["index-history"])
    assert args.func(args) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "queue_full"


def test_s12_fts_backfill_internal_identity_collision_does_not_rewrite_user_job(db):
    db.execute("""INSERT INTO analysis_jobs
        (job_id,kind,command,admission_key,request_json,request_hash,accepted_at,cutoff_at,created_at,updated_at,status)
        VALUES ('user-job','analysis','topics',?,'{}','hash',?,?,?,?,'completed')""",
        (history.FTS_INDEX_ADMISSION_KEY, *([NOW.isoformat()] * 4)))
    with pytest.raises(ValueError, match="identity"):
        queue_index(db, rebuild=True)
    assert AnalysisStore(db).get_job("user-job")["request"] == {}
