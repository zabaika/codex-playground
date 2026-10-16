import copy
from datetime import datetime, timezone
import sqlite3
import tomllib

import pytest

from common import sqlite as common_sqlite
from telegram_connector import telegram_analysis as analysis
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore, StaleGenerationError, canonical_json


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
DATE = "2026-10-12T10:00:00+00:00"


@pytest.fixture
def manifest_job():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    store = AnalysisStore(conn)
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    config["sync"]["batch_size"] = 2
    event = {"update_id": 10, "message": {"chat": {"id": 100}, "from": {"id": 7},
              "text": "/topics @two,@one since=2026-10-10 until=2026-10-12"}}
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999",
                                           now=NOW, config_dir=folder)
    job = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
    checkpoint = {"history": {"refs": {"@two": 22, "@one": 11}, "sources": {
        str(peer): {"mode": "reuse", "state": "complete"} for peer in (22, 11)}}}
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    for peer in (22, 11):
        conn.execute("INSERT INTO channels VALUES (?,NULL,?,'Fixture','Channel','{}','now','now')", (peer, f"peer{peer}"))
    try:
        yield store, job
    finally:
        conn.close()


def seed(store, peer, ids, *, text="Не менее 1250 EUR, срок 30 дней; исключение: нерезиденты.", date=DATE):
    for number in ids:
        store.conn.execute("""INSERT INTO messages
            (channel_id,message_id,date_utc,text,raw_json,content_hash,imported_at)
            VALUES (?,?,?,?,'{}','weak-legacy-hash','now')""", (peer, number, date, text))


def build(store, job):
    function = getattr(analysis, "build_all_messages_manifest", None)
    assert callable(function), "All-message analysis needs a durable, paged input manifest before inference"
    return function(store, job["job_id"], generation=job["generation"])


def messages(store, job):
    function = getattr(analysis, "iter_analysis_manifest_messages", None)
    assert callable(function), "Inference must read the fixed manifest and verify actual source digests"
    return list(function(store, job["job_id"], generation=job["generation"]))


def change_setting(store, job, section, field, value):
    payload = copy.deepcopy(store.get_job(job["job_id"])["request"])
    payload["settings"][section][field] = value
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))


def test_s10_manifest_contract_keyset_ties_composite_ids_and_user_source_order(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 8))
    seed(store, 11, range(1, 4))
    manifest = build(store, job)
    assert manifest["state"] == "complete" and manifest["selected"] == 10
    values = messages(store, job)
    assert [(v["message"]["channel_id"], v["message"]["message_id"]) for v in values] == (
        [(22, i) for i in range(1, 8)] + [(11, i) for i in range(1, 4)])
    assert all(len(v["input"]["content_digest"]) == 64 for v in values)
    assert all("1250 EUR" in v["message"]["text"] for v in values)
    pages = store.conn.execute("""SELECT COUNT(*) FROM analysis_inputs i JOIN analysis_batches b USING(batch_id)
        WHERE b.job_id=? GROUP BY b.batch_id""", (job["job_id"],)).fetchall()
    assert max(row[0] for row in pages) <= 2
    assert not store.conn.in_transaction
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.get_job(job["job_id"])["intermediate_bytes"] == store.storage_usage(job["job_id"])["allocated_bytes"]


@pytest.mark.parametrize("count,partial", [(3, False), (5, True)])
def test_s10_manifest_contract_message_cap_is_explicit_and_exact_end_is_not_partial(manifest_job, count, partial):
    store, job = manifest_job
    seed(store, 22, range(1, count + 1))
    change_setting(store, job, "analysis", "max_messages", 3)
    manifest = build(store, job)
    assert manifest["selected"] == 3 and manifest["partial"] is partial
    assert manifest["known_skipped"] == count - 3
    assert len(messages(store, job)) == 3


def test_s10_manifest_contract_late_insert_does_not_extend_completed_manifest(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1, 2])
    first = build(store, job)
    seed(store, 22, [3])
    assert build(store, job) == first
    assert [v["message"]["message_id"] for v in messages(store, job)] == [1, 2]


def test_s10_manifest_contract_same_length_text_change_interrupts_instead_of_using_weak_hash(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1], text="500 EUR")
    build(store, job)
    store.conn.execute("UPDATE messages SET text='900 EUR' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    current = store.get_job(job["job_id"])
    assert current["status"] == "interrupted"
    assert current["checkpoint"]["last_stop"]["reason"] == "input_changed"


def test_s10_manifest_contract_ocr_is_ordered_and_changes_invalidate_but_views_do_not(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1])
    for ordinal, text in ((2, "300 EUR"), (0, "200 EUR")):
        store.conn.execute("""INSERT INTO media_assets
            (channel_id,message_id,ordinal,media_kind,ocr_status,ocr_text,created_at)
            VALUES (22,1,?,'photo','done',?,'now')""", (ordinal, text))
    build(store, job)
    store.conn.execute("UPDATE messages SET views=1000 WHERE channel_id=22")
    value = messages(store, job)[0]
    assert [m["ocr_text"] for m in value["message"]["media"]] == ["200 EUR", "300 EUR"]
    store.conn.execute("UPDATE media_assets SET ocr_text='900 EUR' WHERE ordinal=0")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)


def test_s10_manifest_contract_source_removed_after_selection_is_detected(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1])
    build(store, job)
    store.conn.execute("DELETE FROM messages WHERE channel_id=22")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    assert store.get_job(job["job_id"])["status"] == "interrupted"


def test_s10_manifest_contract_restart_keeps_committed_pages_and_excludes_late_old_date_insert(manifest_job, monkeypatch):
    store, job = manifest_job
    seed(store, 22, range(1, 7))
    original = store.commit_manifest_page

    def crash_after_commit(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fixture crash after page commit")

    with monkeypatch.context() as patch:
        patch.setattr(store, "commit_manifest_page", crash_after_commit)
        with pytest.raises(RuntimeError, match="after page commit"):
            build(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_inputs").fetchone()[0] == 2
    with pytest.raises(ValueError, match="not ready"):
        messages(store, job)
    seed(store, 22, [99])  # Same date, above the frozen Telegram message-ID ceiling.
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=NOW)
    store.resume_job(job["job_id"], generation=job["generation"], phase="select", max_queued_jobs=8, now=NOW)
    resumed = store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)
    assert build(store, resumed)["selected"] == 6
    assert [m["message"]["message_id"] for m in messages(store, resumed)] == list(range(1, 7))


def test_s10_manifest_contract_failed_page_transaction_does_not_advance_cursor(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 6))
    store.conn.execute("""CREATE TRIGGER reject_inputs BEFORE INSERT ON analysis_inputs
        BEGIN SELECT RAISE(ABORT,'fixture input failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture input failure"):
        build(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches").fetchone()[0] == 0
    assert store.get_job(job["job_id"])["checkpoint"]["manifest"]["selected"] == 0
    assert not store.conn.in_transaction
    store.conn.execute("DROP TRIGGER reject_inputs")
    assert build(store, job)["selected"] == 5


def test_s10_manifest_contract_stale_generation_and_cancellation_cannot_write(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1])
    with pytest.raises(StaleGenerationError):
        build(store, {**job, "generation": job["generation"] - 1})
    store.request_cancel(job["job_id"], now=NOW)
    with pytest.raises(InterruptedError):
        build(store, job)
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_inputs").fetchone()[0] == 0


def test_s10_manifest_contract_failed_source_is_excluded_and_partial_source_is_visible(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1])
    seed(store, 11, [1])
    checkpoint = store.get_job(job["job_id"])["checkpoint"]
    checkpoint["history"]["sources"]["22"]["state"] = "failed"
    checkpoint["history"]["sources"]["11"]["state"] = "partial"
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    manifest = build(store, job)
    assert manifest["partial"] and "history_partial" in manifest["reasons"]
    assert [v["message"]["channel_id"] for v in messages(store, job)] == [11]


def test_s10_manifest_contract_zero_flush_is_one_and_scope_respects_cutoff(manifest_job):
    store, job = manifest_job
    change_setting(store, job, "sync", "batch_size", 0)
    seed(store, 22, [1, 2])
    seed(store, 22, [3], date="2026-10-13T00:00:00+00:00")
    seed(store, 11, [4], date="2026-10-09T23:59:59+00:00")
    assert build(store, job)["selected"] == 2
    assert len(messages(store, job)) == 2
    assert all(r[0] <= 1 for r in store.conn.execute("SELECT COUNT(*) FROM analysis_inputs GROUP BY batch_id"))


def test_s10_manifest_contract_manifest_byte_budget_stops_with_visible_partial(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 41))
    maximum = job["intermediate_bytes"] + 2400
    change_setting(store, job, "analysis", "max_intermediate_bytes", maximum)
    manifest = build(store, job)
    assert manifest["partial"] and "manifest_byte_cap" in manifest["reasons"]
    assert 0 <= manifest["selected"] < 40
    assert manifest["known_skipped"] == 40 - manifest["selected"]
    assert store.get_job(job["job_id"])["intermediate_bytes"] <= maximum


def test_s10_manifest_contract_oversize_source_is_partial_without_materializing_it(manifest_job):
    store, job = manifest_job
    byte_cap = 2 * store.storage_usage(job["job_id"])["allocated_bytes"]
    seed(store, 22, [1], text="x" * (2 * byte_cap))
    change_setting(store, job, "analysis", "max_intermediate_bytes", byte_cap)
    manifest = build(store, job)
    assert manifest["selected"] == 0 and manifest["partial"]
    assert "manifest_byte_cap" in manifest["reasons"]


def resume(store, job):
    store.resume_job(job["job_id"], generation=job["generation"], phase="select", max_queued_jobs=8, now=NOW)
    return store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)


def test_s10_manifest_contract_resume_refreshes_changed_pages_only_without_resetting_budget_or_adding_inputs(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 6), text="500 EUR")
    build(store, job)
    before = {r[0]: r[1] for r in store.conn.execute("SELECT batch_id,input_digest FROM analysis_batches")}
    store.conn.execute("UPDATE analysis_jobs SET llm_calls=2,used_tokens=100,reserved_tokens=200 WHERE job_id=?", (job["job_id"],))
    store.conn.execute("UPDATE messages SET text='900 EUR' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    seed(store, 22, [99])
    running = resume(store, job)
    result = build(store, running)
    assert "input_changed" not in result
    values = messages(store, running)
    assert [v["message"]["message_id"] for v in values] == list(range(1, 6))
    assert values[0]["message"]["text"] == "900 EUR"
    after = {r[0]: r[1] for r in store.conn.execute("SELECT batch_id,input_digest FROM analysis_batches")}
    assert sum(before[key] != after[key] for key in before) == 1
    current = store.get_job(job["job_id"])
    assert (current["llm_calls"], current["used_tokens"], current["reserved_tokens"]) == (2, 100, 200)
    assert current["cutoff_at"] == job["cutoff_at"]


def test_s10_manifest_contract_resume_rechecks_all_pages_and_removes_deleted_inputs(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 6), text="500 EUR")
    build(store, job)
    store.conn.execute("UPDATE messages SET text='900 EUR' WHERE channel_id=22 AND message_id IN (1,5)")
    store.conn.execute("DELETE FROM messages WHERE channel_id=22 AND message_id=3")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    running = resume(store, job)
    result = build(store, running)
    assert result["selected"] == 4 and result["partial"]
    assert result["known_skipped"] == 1 and "input_removed" in result["reasons"]
    values = messages(store, running)
    assert [v["message"]["message_id"] for v in values] == [1, 2, 4, 5]
    assert values[0]["message"]["text"] == values[-1]["message"]["text"] == "900 EUR"


def test_s10_manifest_contract_resume_preserves_date_order_when_local_date_is_corrected(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 6))
    build(store, job)
    store.conn.execute("UPDATE messages SET date_utc='2026-10-12T11:00:00+00:00' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    running = resume(store, job)
    build(store, running)
    assert [v["message"]["message_id"] for v in messages(store, running)] == [2, 3, 4, 5, 1]


def test_s10_manifest_contract_oversize_edit_after_selection_is_input_changed(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1])
    # Reserve setup headroom relative to the frozen bundle, independently of
    # prompt wording. The edited source must still exceed the entire byte cap.
    byte_cap = 2 * store.storage_usage(job["job_id"])["allocated_bytes"]
    change_setting(store, job, "analysis", "max_intermediate_bytes", byte_cap)
    build(store, job)
    assert [value["message"]["message_id"] for value in messages(store, job)] == [1]
    store.conn.execute("UPDATE messages SET text=? WHERE channel_id=22", ("x" * (2 * byte_cap),))
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    assert store.get_job(job["job_id"])["status"] == "interrupted"


def test_s10_manifest_contract_repair_restart_keeps_its_cursor_and_invalidates_changed_select_pages(manifest_job, monkeypatch):
    import json
    store, job = manifest_job
    seed(store, 22, range(1, 6), text="500 EUR")
    build(store, job)
    store.conn.execute("UPDATE messages SET text='900 EUR' WHERE message_id IN (1,5)")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    running = resume(store, job)
    original = store.repair_manifest_batch
    repaired = []

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        repaired.append(kwargs["batch"]["batch_id"])
        raise RuntimeError("fixture crash after repair commit")

    with monkeypatch.context() as patch:
        patch.setattr(store, "repair_manifest_batch", crash)
        with pytest.raises(RuntimeError, match="after repair commit"):
            build(store, running)
    assert store.get_job(job["job_id"])["checkpoint"]["manifest"]["repair_after_step"]
    store.finish_job(job["job_id"], generation=running["generation"], status="interrupted", now=NOW)
    next_run = resume(store, running)
    build(store, next_run)
    values = messages(store, next_run)
    assert values[0]["message"]["text"] == values[-1]["message"]["text"] == "900 EUR"
    invalidated = [r[0] for r in store.conn.execute("SELECT payload_json FROM analysis_batches") if json.loads(r[0]).get("invalidated")]
    assert len(invalidated) == 2  # Parent-invalidation marker for the later lineage owner.


def test_s10_manifest_contract_cancellation_between_yields_stops_immediately(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 5))
    build(store, job)
    iterator = analysis.iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"])
    assert next(iterator)["message"]["message_id"] == 1
    store.request_cancel(job["job_id"], now=NOW)
    with pytest.raises(InterruptedError):
        next(iterator)


def test_s10_manifest_contract_actual_admission_cutoff_bounds_input(manifest_job):
    store, job = manifest_job
    payload = copy.deepcopy(job["request"])
    payload["request"]["until"] = "2026-10-16T23:59:59+00:00"
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(payload), job["job_id"]))
    seed(store, 22, [1], date="2026-10-16T11:00:00+00:00")
    seed(store, 22, [2], date="2026-10-16T13:00:00+00:00")
    assert build(store, job)["selected"] == 1
    assert [v["message"]["message_id"] for v in messages(store, job)] == [1]


def test_s10_manifest_contract_large_input_uses_bounded_python_buffers(manifest_job, monkeypatch):
    import tracemalloc
    store, job = manifest_job
    count, text = 2500, "Условие 123 EUR; " * 250
    seed(store, 22, range(1, count + 1), text=text)
    change_setting(store, job, "sync", "batch_size", 31)
    page_sizes = []
    original = store.history_page

    def page(*args, **kwargs):
        rows = original(*args, **kwargs)
        page_sizes.append(len(rows))
        return rows

    monkeypatch.setattr(store, "history_page", page)
    tracemalloc.start()
    try:
        manifest = build(store, job)
        processed = sum(1 for _ in analysis.iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"]))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert manifest["selected"] == processed == count
    assert max(page_sizes) <= 31
    # A relative bound against the fixture's source volume, not a machine RSS SLA.
    assert peak < count * len(text.encode("utf-8")) // 3


@pytest.mark.parametrize("mutation", ["delete", "move_outside_scope"])
def test_s10_manifest_contract_input_changed_during_selection_interrupts_and_resumes_building(manifest_job, monkeypatch, mutation):
    store, job = manifest_job
    seed(store, 22, [1, 2])
    original = store.history_page
    changed = []

    def page(*args, **kwargs):
        rows = original(*args, **kwargs)
        if rows and not changed:
            changed.append(True)
            if mutation == "delete":
                store.conn.execute("DELETE FROM messages WHERE channel_id=22 AND message_id=1")
            else:
                store.conn.execute("UPDATE messages SET date_utc='2026-10-13T10:00:00+00:00' WHERE channel_id=22 AND message_id=1")
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(store, "history_page", page)
        with pytest.raises(ValueError, match="input_changed"):
            build(store, job)
    assert store.get_job(job["job_id"])["status"] == "interrupted"
    running = resume(store, job)
    manifest = build(store, running)
    assert manifest["state"] == "complete" and manifest["selected"] == 1
    assert manifest["partial"] and manifest["known_skipped"] == 1
    assert [v["message"]["message_id"] for v in messages(store, running)] == [2]


def test_s10_manifest_contract_keeps_long_text_numbers_negation_conditions_and_urls(manifest_job):
    store, job = manifest_job
    text = "Контекст обсуждения. " * 200 + "Не для нерезидентов: 1250 EUR, 30 дней; исключение до 01.11. https://t.me/example/123"
    seed(store, 22, [1], text=text)
    build(store, job)
    assert messages(store, job)[0]["message"]["text"] == text


def test_s10_manifest_contract_aliases_do_not_count_one_source_twice(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1, 2, 3])
    checkpoint = store.get_job(job["job_id"])["checkpoint"]
    checkpoint["history"]["refs"]["@one"] = 22
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    manifest = build(store, job)
    assert manifest["channels"] == [22] and manifest["selected"] == 3
    assert len(messages(store, job)) == 3


def test_s10_manifest_contract_rowid_reassignment_during_restart_does_not_admit_late_message(manifest_job, monkeypatch):
    store, job = manifest_job
    seed(store, 11, range(100, 190), date="2026-09-01T10:00:00+00:00")
    seed(store, 22, range(1, 7))
    original = store.commit_manifest_page

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fixture stopped after page")

    with monkeypatch.context() as patch:
        patch.setattr(store, "commit_manifest_page", crash)
        with pytest.raises(RuntimeError):
            build(store, job)
    store.conn.execute("DELETE FROM messages WHERE channel_id=11")
    store.conn.execute("VACUUM")
    # VACUUM may preserve rowids on a particular build. Model the allowed
    # reassignment explicitly; Telegram source identities remain unchanged.
    store.conn.execute("UPDATE messages SET rowid=message_id")
    seed(store, 22, [99])
    manifest = build(store, job)
    assert manifest["selected"] == 6
    assert [v["message"]["message_id"] for v in messages(store, job)] == list(range(1, 7))
