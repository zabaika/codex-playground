import copy
from datetime import datetime, timezone
import sqlite3
import tomllib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from common import sqlite as common_sqlite
from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_evidence
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore, StaleGenerationError, canonical_json


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def admit_budget_job(conn, *, overrides=None):
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    config["analysis"].update(overrides or {})
    event = {"update_id": 10, "message": {"chat": {"id": 100}, "from": {"id": 7},
              "text": "/topics @fixture since=today until=today"}}
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    store = AnalysisStore(conn)
    queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999",
                                           now=NOW, config_dir=folder)
    return store, store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)


@pytest.fixture
def budget_job():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    try:
        yield admit_budget_job(conn)
    finally:
        conn.close()


def plan(store, job, *, key="synthesize/0", kind="synthesize", payload=None):
    function = getattr(store, "plan_batch", None)
    assert callable(function), "Provider attempts need an immutable, durable step identity"
    return function(job["job_id"], generation=job["generation"], step_key=key, kind=kind,
                    payload=payload or {}, input_digest="a" * 64)


def reserve(store, job, batch, *, attempt="first", kind="inference", input_tokens=10, output_tokens=20):
    function = getattr(store, "reserve_provider_attempt", None)
    assert callable(function), "Each physical request must reserve cumulative budgets before sending"
    return function(job["job_id"], batch["batch_id"], generation=job["generation"], attempt_id=attempt,
                    request_kind=kind, input_tokens=input_tokens, output_tokens=output_tokens)


def settle(store, job, batch, *, attempt="first", outcome="succeeded", usage=None):
    function = getattr(store, "settle_provider_attempt", None)
    assert callable(function), "Usage receipts must reconcile the exact durable attempt reservation"
    return function(job["job_id"], batch["batch_id"], generation=job["generation"], attempt_id=attempt,
                    outcome=outcome, usage=usage)


def setting(store, job, key, value):
    request = copy.deepcopy(store.get_job(job["job_id"])["request"])
    request["settings"]["analysis"][key] = value
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (canonical_json(request), job["job_id"]))


def test_s13_budget_ledger_plans_are_idempotent_and_keep_immutable_identity(budget_job):
    store, job = budget_job
    first = plan(store, job, payload={"parents": []})
    again = plan(store, job, payload={"parents": []})
    assert first == again and first["state"] == "planned"
    assert first["attempts"] == []
    with pytest.raises(ValueError, match="conflict"):
        plan(store, job, payload={"parents": ["different"]})
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches").fetchone()[0] == 1


def test_s13_budget_ledger_unknown_response_and_retry_keep_both_reservations(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    first = reserve(store, job, batch)
    assert first["reserved_tokens"] == 30 and first["state"] == "reserved"
    settle(store, job, batch, outcome="unknown")
    reserve(store, job, batch, attempt="retry")
    settle(store, job, batch, attempt="retry", usage={"input_tokens": 9, "output_tokens": 4})
    current = store.get_job(job["job_id"])
    assert (current["llm_calls"], current["provider_requests"], current["used_tokens"], current["reserved_tokens"]) == (2, 2, 13, 30)
    assert store.get_batch(batch["batch_id"])["attempts"][0]["state"] == "unknown"


def test_s13_budget_ledger_token_count_spends_provider_requests_only(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    attempt = reserve(store, job, batch, kind="token_count", input_tokens=0, output_tokens=0)
    assert attempt["reserved_tokens"] == 0
    settle(store, job, batch)
    current = store.get_job(job["job_id"])
    assert (current["provider_requests"], current["llm_calls"], current["used_tokens"], current["reserved_tokens"]) == (1, 0, 0, 0)


@pytest.mark.parametrize("cap,value", [("max_llm_calls", 1), ("max_provider_requests", 1), ("max_total_tokens", 30)])
def test_s13_budget_ledger_each_attempt_cap_is_atomic(budget_job, cap, value):
    store, job = budget_job
    setting(store, job, cap, value)
    batch = plan(store, job)
    reserve(store, job, batch)
    before = store.get_job(job["job_id"])
    with pytest.raises(ValueError, match=cap):
        reserve(store, job, batch, attempt="extra")
    assert store.get_job(job["job_id"]) == before
    assert len(store.get_batch(batch["batch_id"])["attempts"]) == 1


def test_s13_budget_ledger_cached_input_is_charged_and_receipt_is_idempotent(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch, input_tokens=20, output_tokens=20)
    usage = {"input_tokens": 20, "output_tokens": 10, "cached_input_tokens": 15}
    settle(store, job, batch, usage=usage)
    before = store.get_job(job["job_id"])
    settle(store, job, batch, usage=usage)
    assert store.get_job(job["job_id"]) == before
    assert (before["used_tokens"], before["reserved_tokens"]) == (30, 0)
    with pytest.raises(ValueError, match="conflict"):
        settle(store, job, batch, usage={"input_tokens": 19, "output_tokens": 10})


def test_s13_budget_ledger_journal_failure_rolls_back_all_counters(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    store.conn.execute("""CREATE TRIGGER reject_attempt BEFORE UPDATE OF attempts_json ON analysis_batches
        BEGIN SELECT RAISE(ABORT,'fixture journal failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture journal failure"):
        reserve(store, job, batch)
    current = store.get_job(job["job_id"])
    assert (current["provider_requests"], current["llm_calls"], current["reserved_tokens"]) == (0, 0, 0)
    assert store.get_batch(batch["batch_id"])["attempts"] == [] and not store.conn.in_transaction


@pytest.mark.parametrize("usage", [{}, {"input_tokens": 10}, {"input_tokens": -1, "output_tokens": 0},
                                    {"input_tokens": True, "output_tokens": 0},
                                    {"input_tokens": 10, "output_tokens": 0, "cached_input_tokens": 11}])
def test_s13_budget_ledger_invalid_usage_does_not_release_unknown_reservation(budget_job, usage):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    before = store.get_job(job["job_id"])
    with pytest.raises(ValueError, match="usage|tokens"):
        settle(store, job, batch, usage=usage)
    assert store.get_job(job["job_id"]) == before


def test_s13_budget_ledger_duplicate_attempt_identity_is_not_a_second_send_authorization(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    with pytest.raises(ValueError, match="already_reserved"):
        reserve(store, job, batch)
    assert store.get_job(job["job_id"])["provider_requests"] == 1


def test_s13_budget_ledger_resume_retains_unknown_cost_and_fences_late_receipt(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=NOW)
    store.resume_job(job["job_id"], generation=job["generation"], phase="extract", max_queued_jobs=8, now=NOW)
    running = store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)
    with pytest.raises(StaleGenerationError):
        settle(store, job, batch, usage={"input_tokens": 9, "output_tokens": 4})
    reserve(store, running, batch, attempt="resumed")
    current = store.get_job(job["job_id"])
    assert (current["llm_calls"], current["provider_requests"], current["reserved_tokens"]) == (2, 2, 60)
    assert current["cutoff_at"] == job["cutoff_at"]


def test_s13_budget_ledger_cancel_blocks_sending_but_preserves_authoritative_usage_receipt(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    store.request_cancel(job["job_id"], now=NOW)
    with pytest.raises(InterruptedError):
        reserve(store, job, batch, attempt="after-cancel")
    settle(store, job, batch, usage={"input_tokens": 10, "output_tokens": 5})
    assert store.get_job(job["job_id"])["used_tokens"] == 15


def test_s13_budget_ledger_usage_overrun_is_recorded_before_stopping_future_requests(budget_job):
    store, job = budget_job
    setting(store, job, "max_total_tokens", 35)
    batch = plan(store, job)
    reserve(store, job, batch)
    with pytest.raises(ValueError, match="usage_overrun"):
        settle(store, job, batch, usage={"input_tokens": 20, "output_tokens": 20})
    current = store.get_job(job["job_id"])
    assert (current["used_tokens"], current["reserved_tokens"]) == (40, 0)
    assert current["checkpoint"]["budget_stop"]["reason"] == "usage_overrun"
    with pytest.raises(ValueError, match="usage_overrun"):
        settle(store, job, batch, usage={"input_tokens": 20, "output_tokens": 20})
    assert store.get_job(job["job_id"]) == current
    with pytest.raises(ValueError, match="usage_overrun"):
        reserve(store, job, batch, attempt="must-stop")


def test_s13_budget_ledger_concurrent_connections_cannot_spend_last_call_twice(tmp_path):
    path = tmp_path / "budget.sqlite3"
    conn = common_sqlite.connect_sqlite(path)
    try:
        history.init_db(conn)
        store, job = admit_budget_job(conn, overrides={"max_llm_calls": 1})
        batch = plan(store, job)
        barrier = Barrier(2)

        def try_reserve(index):
            connection = common_sqlite.connect_sqlite(path)
            try:
                barrier.wait(timeout=5)
                try:
                    reserve(AnalysisStore(connection), job, batch, attempt=f"concurrent-{index}")
                    return "reserved"
                except ValueError as exc:
                    return str(exc)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(try_reserve, (1, 2)))
        assert sorted(outcomes) == ["max_llm_calls", "reserved"]
        assert store.get_job(job["job_id"])["llm_calls"] == 1
    finally:
        conn.close()


def test_s13_budget_ledger_extraction_preserves_merge_and_final_call_resources(budget_job):
    store, job = budget_job
    setting(store, job, "max_llm_calls", 2)
    first = plan(store, job, kind="extract", key="extract/0")
    reserve(store, job, first)
    second = plan(store, job, kind="extract", key="extract/1")
    with pytest.raises(ValueError, match="max_llm_calls"):
        reserve(store, job, second, attempt="extra-leaf")
    final = plan(store, job)
    reserve(store, job, final, attempt="final")
    assert store.get_job(job["job_id"])["llm_calls"] == 2


@pytest.mark.parametrize("roots,merges", [(0, 0), (1, 0), (8, 1), (9, 2), (10, 3), (64, 9), (65, 10)])
def test_s13_budget_ledger_completion_plan_follows_fan_in_and_detail(budget_job, roots, merges):
    from telegram_connector import analysis_store as module
    store, job = budget_job
    function = getattr(module, "completion_budget", None)
    assert callable(function), "Completion reserve must follow the actual bounded merge shape and output profile"
    settings = job["request"]["settings"]
    short = function(settings, detail="short", roots=roots)
    full = function(settings, detail="full", roots=roots)
    assert short["merge_calls"] == merges
    assert short["llm_calls"] == merges + bool(roots)
    assert short["provider_requests"] == 2 * short["llm_calls"]
    assert full["tokens"] - short["tokens"] == (11000 if roots else 0)


@pytest.mark.parametrize("tokens,output,reason", [(16001, 20, "max_input_tokens"), (10, 3001, "stage_output_limit")])
def test_s13_budget_ledger_per_request_caps_cannot_be_overridden(budget_job, tokens, output, reason):
    store, job = budget_job
    batch = plan(store, job)
    with pytest.raises(ValueError, match=reason):
        reserve(store, job, batch, input_tokens=tokens, output_tokens=output)
    assert store.get_job(job["job_id"])["provider_requests"] == 0


def test_s13_budget_ledger_known_receipt_fits_preallocated_space_at_byte_cap(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    maximum = store.get_job(job["job_id"])["intermediate_bytes"]
    setting(store, job, "max_intermediate_bytes", maximum)
    settle(store, job, batch, usage={"input_tokens": 10, "output_tokens": 5})
    current = store.get_job(job["job_id"])
    assert (current["used_tokens"], current["reserved_tokens"]) == (15, 0)
    assert current["intermediate_bytes"] <= maximum


def test_s13_budget_ledger_unknown_then_known_receipt_keeps_its_byte_allowance(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    maximum = store.get_job(job["job_id"])["intermediate_bytes"]
    setting(store, job, "max_intermediate_bytes", maximum)
    settle(store, job, batch, outcome="unknown")
    settle(store, job, batch, usage={"input_tokens": 10, "output_tokens": 5})
    assert store.get_job(job["job_id"])["used_tokens"] == 15


def test_s13_budget_ledger_usage_overrun_receipt_is_durable_even_at_byte_cap(budget_job):
    store, job = budget_job
    setting(store, job, "max_total_tokens", 35)
    batch = plan(store, job)
    reserve(store, job, batch)
    maximum = store.get_job(job["job_id"])["intermediate_bytes"]
    setting(store, job, "max_intermediate_bytes", maximum)
    with pytest.raises(ValueError, match="usage_overrun"):
        settle(store, job, batch, usage={"input_tokens": 20, "output_tokens": 20})
    current = store.get_job(job["job_id"])
    assert current["used_tokens"] == 40 and current["reserved_tokens"] == 0
    assert current["intermediate_bytes"] <= maximum


def test_s13_budget_ledger_checkpoint_cannot_bypass_global_cap_with_larger_argument(budget_job):
    store, job = budget_job
    setting(store, job, "max_intermediate_bytes", job["intermediate_bytes"] + 100)
    before = store.get_job(job["job_id"])
    with pytest.raises(ValueError, match="max_intermediate_bytes"):
        store.save_checkpoint(job["job_id"], {"large": "я" * 1000}, generation=job["generation"], max_bytes=100000)
    assert store.get_job(job["job_id"])["checkpoint"] == before["checkpoint"]


def test_s13_budget_ledger_logical_bytes_include_utf8_manifest_steps_and_evidence(budget_job):
    store, job = budget_job
    usage = getattr(store, "storage_usage", None)
    assert callable(usage), "One storage owner must account every job document and child record"
    batch = plan(store, job)
    before = usage(job["job_id"])
    store.conn.execute("""INSERT INTO analysis_inputs
        (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
        VALUES (?,11,1,0,0,'selected',?,'digest')""", (batch["batch_id"], NOW.isoformat()))
    store.conn.execute("""INSERT INTO analysis_evidence
        (record_id,batch_id,record_type,payload_json) VALUES ('fact',?,'claim',?)""",
        (batch["batch_id"], canonical_json({"text": "я" * 100})))
    after = usage(job["job_id"])
    assert after["logical_bytes"] - before["logical_bytes"] > 200
    assert after["allocated_bytes"] >= after["logical_bytes"]
    store.save_checkpoint(job["job_id"], {}, generation=job["generation"], max_bytes=100000)
    assert store.get_job(job["job_id"])["intermediate_bytes"] == usage(job["job_id"])["allocated_bytes"]


def test_s13_budget_ledger_final_status_fits_at_cap_without_invented_sources(budget_job):
    store, job = budget_job
    maximum = job["intermediate_bytes"]
    setting(store, job, "max_intermediate_bytes", maximum)
    function = getattr(store, "record_budget_stop", None)
    assert callable(function), "Metadata headroom must fund a deterministic persisted fallback"
    result = function(job["job_id"], generation=job["generation"], reason="max_intermediate_bytes")
    assert result["kind"] == "budget_stop" and result["status"] == "partial"
    assert result["counts"]["llm_calls"] == 0 and result["source_refs"] == []
    current = store.get_job(job["job_id"])
    assert current["result"] == result and current["intermediate_bytes"] <= maximum


def test_s13_budget_ledger_storage_failure_before_attempt_cannot_spend_provider_quota(budget_job):
    store, job = budget_job
    setting(store, job, "max_intermediate_bytes", job["intermediate_bytes"] + 10)
    with pytest.raises(ValueError, match="max_intermediate_bytes"):
        plan(store, job, payload={"large": "x" * 1000})
    current = store.get_job(job["job_id"])
    assert (current["llm_calls"], current["provider_requests"], current["reserved_tokens"]) == (0, 0, 0)
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches").fetchone()[0] == 0


def test_s13_budget_ledger_other_steps_cannot_consume_reserved_receipt_capacity(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    maximum = store.get_job(job["job_id"])["intermediate_bytes"]
    setting(store, job, "max_intermediate_bytes", maximum)
    with pytest.raises(ValueError, match="max_intermediate_bytes"):
        plan(store, job, key="different-step", payload={"large": "x" * 1000})
    settle(store, job, batch, usage={"input_tokens": 10, "output_tokens": 5})
    assert store.get_job(job["job_id"])["used_tokens"] == 15


def test_s13_budget_ledger_budget_status_preserves_an_existing_canonical_result(budget_job):
    store, job = budget_job
    result = {"items": [{"claim": "Содержательный результат", "sources": []}]}
    store.conn.execute("UPDATE analysis_jobs SET result_json=? WHERE job_id=?", (canonical_json(result), job["job_id"]))
    store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_total_tokens")
    assert store.get_job(job["job_id"])["result"] == result


def test_s13_budget_ledger_fallback_sources_are_actual_processed_manifest_refs(budget_job):
    store, job = budget_job
    store.conn.execute("INSERT INTO channels VALUES (11,NULL,'fixture','Fixture','Channel','{}','now','now')")
    store.conn.execute("""INSERT INTO messages (channel_id,message_id,date_utc,text,raw_json,content_hash,imported_at)
        VALUES (11,1,?,'Источник','{}','legacy','now')""", (NOW.isoformat(),))
    batch = plan(store, job, kind="extract", key="extract/cached-empty")
    _, digest = analysis_evidence._analysis_message_snapshot(store, 11, 1, 100000)
    store.conn.execute("""INSERT INTO analysis_inputs
        (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
        VALUES (?,11,1,0,0,'selected',?,?)""", (batch["batch_id"], NOW.isoformat(), digest))
    store.conn.execute("UPDATE analysis_batches SET state='succeeded' WHERE batch_id=?", (batch["batch_id"],))
    result = store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_llm_calls")
    assert result["source_refs"] == [{"channel_id": 11, "message_id": 1, "content_digest": digest}]


def complete(store, job, batch, *, records=None, inputs=None, attempt=None, usage=None):
    function = getattr(store, "complete_batch", None)
    assert callable(function), "Validated outputs, lineage, completion and usage need one atomic owner"
    return function(job["job_id"], batch["batch_id"], generation=job["generation"],
                    records=records or [], inputs=inputs or [], attempt_id=attempt, usage=usage)


def record(record_id="fact"):
    return {"record_id": record_id, "record_type": "claim", "topic_key": "fixture", "payload": {"claim": "Факт"}}


def test_s13_budget_ledger_completion_commits_records_lineage_and_usage_together(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    source = {"channel_id": 11, "message_id": 1, "fragment_start": 0, "fragment_end": 0,
              "role": "selected", "date_utc": NOW.isoformat(), "content_digest": "a" * 64}
    complete(store, job, batch, records=[record()], inputs=[source], attempt="first",
             usage={"input_tokens": 10, "output_tokens": 5})
    saved = store.get_job(job["job_id"])
    assert (saved["evidence_records"], saved["used_tokens"], saved["reserved_tokens"]) == (1, 15, 0)
    assert store.get_batch(batch["batch_id"])["state"] == "succeeded"
    assert len(store.manifest_batch_inputs(batch["batch_id"])) == 1
    assert saved["intermediate_bytes"] == store.storage_usage(job["job_id"])["allocated_bytes"]


def test_s13_budget_ledger_record_cap_is_cumulative_and_completion_is_idempotent(budget_job):
    store, job = budget_job
    setting(store, job, "max_evidence_records", 1)
    batch = plan(store, job)
    complete(store, job, batch, records=[record()])  # Validated cached output: no physical attempt.
    complete(store, job, batch, records=[record()])
    other = plan(store, job, key="synthesize/1")
    with pytest.raises(ValueError, match="max_evidence_records"):
        complete(store, job, other, records=[record("other")])
    assert store.get_batch(other["batch_id"])["state"] == "planned"
    assert store.get_job(job["job_id"])["evidence_records"] == 1


def test_s13_budget_ledger_failed_output_commit_keeps_cost_without_partial_facts(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    store.conn.execute("""CREATE TEMP TRIGGER reject_evidence BEFORE INSERT ON analysis_evidence
                          BEGIN SELECT RAISE(ABORT,'fixture output failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture output failure"):
        complete(store, job, batch, records=[record()], attempt="first", usage={"input_tokens": 10, "output_tokens": 5})
    saved = store.get_job(job["job_id"])
    assert (saved["evidence_records"], saved["used_tokens"], saved["reserved_tokens"]) == (0, 15, 0)
    assert store.get_batch(batch["batch_id"])["state"] == "running"
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_evidence").fetchone()[0] == 0


def test_s13_budget_ledger_completion_cannot_bypass_byte_cap(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    setting(store, job, "max_intermediate_bytes", store.storage_usage(job["job_id"])["allocated_bytes"])
    large = record()
    large["payload"]["claim"] = "x" * 2000
    with pytest.raises(ValueError, match="max_intermediate_bytes"):
        complete(store, job, batch, records=[large])
    assert store.get_job(job["job_id"])["evidence_records"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_evidence").fetchone()[0] == 0


def test_s13_budget_ledger_count_response_cannot_complete_inference_output(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch, kind="token_count", input_tokens=0, output_tokens=0)
    before = store.get_batch(batch["batch_id"])
    with pytest.raises(ValueError, match="inference attempt"):
        complete(store, job, batch, records=[record()], attempt="first")
    assert store.get_batch(batch["batch_id"]) == before


def test_s13_budget_ledger_output_overrun_records_cost_but_cannot_commit_facts(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch)
    with pytest.raises(ValueError, match="usage_overrun"):
        complete(store, job, batch, records=[record()], attempt="first", usage={"input_tokens": 10, "output_tokens": 21})
    saved = store.get_job(job["job_id"])
    assert (saved["evidence_records"], saved["used_tokens"], saved["reserved_tokens"]) == (0, 31, 0)
    assert saved["checkpoint"]["budget_stop"]["reason"] == "usage_overrun"
    assert store.get_batch(batch["batch_id"])["state"] == "running"


def test_s13_budget_ledger_concurrent_completions_cannot_spend_last_record_twice(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    conn = common_sqlite.connect_sqlite(path)
    try:
        history.init_db(conn)
        store, job = admit_budget_job(conn, overrides={"max_evidence_records": 1})
        batches = [plan(store, job, key=f"extract/{i}", kind="extract") for i in range(2)]
        barrier = Barrier(2)

        def try_complete(index):
            connection = common_sqlite.connect_sqlite(path)
            try:
                barrier.wait(timeout=5)
                try:
                    complete(AnalysisStore(connection), job, batches[index], records=[record(f"fact-{index}")])
                    return "completed"
                except ValueError as exc:
                    return str(exc)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(try_complete, (0, 1))) == ["completed", "max_evidence_records"]
        assert store.get_job(job["job_id"])["evidence_records"] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_s13_budget_ledger_validated_cache_output_after_count_needs_no_inference_receipt(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    reserve(store, job, batch, kind="token_count", input_tokens=0, output_tokens=0)
    settle(store, job, batch)
    complete(store, job, batch, records=[record()])
    assert store.get_job(job["job_id"])["evidence_records"] == 1
    assert store.get_job(job["job_id"])["llm_calls"] == 0


@pytest.mark.parametrize('kind', ['extract', 'link'])
def test_s38_completion_reserve_counts_facts_not_extraction_batches(budget_job, kind):
    store, job = budget_job
    batch = plan(store, job, key='extract/many', kind='extract')
    store.complete_batch(job['job_id'], batch['batch_id'], generation=job['generation'],
        records=[{'record_id':f'owned-{i}', 'record_type':'claim', 'payload':{}} for i in range(9)], inputs=[])
    setting(store, job, 'max_llm_calls', 3)
    step = plan(store, job, key=kind+'/new', kind=kind)
    with pytest.raises(ValueError, match='max_llm_calls'):
        reserve(store, job, step, attempt='must-preserve-reduction')
    assert store.get_job(job['job_id'])['llm_calls'] == 0


@pytest.mark.parametrize('kind,roots', [('extract',10),('link',9)])
def test_s38_completion_receipt_reserves_each_existing_leaf_fact(budget_job, kind, roots):
    store, job = budget_job
    batch = plan(store, job, key='extract/many', kind='extract')
    store.complete_batch(job['job_id'], batch['batch_id'], generation=job['generation'],
        records=[{'record_id':f'owned-{i}', 'record_type':'claim', 'payload':{}} for i in range(9)], inputs=[])
    step = plan(store, job, key=kind+'/new', kind=kind)
    attempt = reserve(store, job, step)
    assert attempt['protected_tail']['roots'] == roots
