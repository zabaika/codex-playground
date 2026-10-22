import copy
import json

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting, build, resume, seed
from telegram_connector.tests.test_analysis_relations import records_fixture
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_evidence import extract, document
from telegram_connector.tests.test_analysis_batching import batches
from telegram_connector.tests.test_analysis_transport import Response


def merge(store, job, sender):
    function = getattr(analysis, "merge_analysis_records", None)
    assert callable(function), "A bounded reducer must preserve every leaf and persist its own lineage"
    return function(store, job["job_id"], generation=job["generation"], api_key="fixture-key",
        capabilities={"model": "fixture-model", "context_window": 16000, "max_output_tokens": 1200, "source": "fixture"},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=sender, sleep_func=lambda _: None)


def merge_sender(*, bad_ref=False, no_progress=False):
    def send(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        request = json.loads(req.data)
        values = json.loads(request["input"].split("Записи: ", 1)[1])["inputs"]
        ids = [value["node_id"] for value in values]
        groups = [{"title": "Условия", "summary": "Сроки и условия сохранены в исходных фактах.", "input_ids": ["forged"] if bad_ref else ids}]
        if no_progress:
            groups = [{"title": f"Событие {n}", "summary": "Самостоятельное событие.", "input_ids": [key]} for n, key in enumerate(ids)]
        return Response(model_response({"groups": groups,
            "dispositions": [{"input_id": key, "disposition": "merged", "reason": "Включено в группу."} for key in ids],
            "saturated": False}))
    return send


def leafs(store, job, reference):
    function = getattr(analysis, "iter_merge_leaf_records", None)
    assert callable(function), "Merged groups must resolve to exact job-owned leaf facts"
    return list(function(store, job["job_id"], reference, generation=job["generation"]))


def test_s19_hierarchical_merge_retains_first_middle_last_across_three_layers(manifest_job):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": f"Факт {n}: сумма {n * 100} EUR.", "conditions": [f"Условие {n}"]} for n in range(8)])
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    result = merge(store, job, merge_sender())
    assert result["state"] == "ready" and result["partial"] is False
    restored = [record for root in result["frontier"] for record in leafs(store, job, root)]
    assert {record["record_id"] for record in restored} == {record["record_id"] for record in original}
    assert {record["payload"]["claim"] for record in restored} == {record["payload"]["claim"] for record in original}
    assert max(json.loads(row[0])["plan"]["depth"] for row in store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='merge'")) >= 3
    assert all(json.loads(row[0]).get("disposition") for row in store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='extract'"))
    before = store.get_job(job["job_id"])["provider_requests"]
    assert merge(store, job, lambda *a, **k: pytest.fail("Completed reducer must resume without inference"))["state"] == "ready"
    assert store.get_job(job["job_id"])["provider_requests"] == before


def test_s19_hierarchical_merge_unknown_lineage_is_rejected(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    change_setting(store, job, "analysis", "max_repair_attempts", 0)
    result = merge(store, job, merge_sender(bad_ref=True))
    assert result["partial"] is True
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='merge' AND state='succeeded'").fetchone()[0] == 0


def test_s19_hierarchical_merge_no_progress_preserves_all_leafs_as_partial(manifest_job):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": f"Разное событие {n}"} for n in range(4)])
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    result = merge(store, job, merge_sender(no_progress=True))
    assert result["partial"] is True and "merge_no_progress" in result["reasons"]
    restored = [record for root in result["frontier"] for record in leafs(store, job, root)]
    assert {record["record_id"] for record in restored} == {record["record_id"] for record in original}


def test_s19_hierarchical_merge_final_rank_does_not_force_distinct_events_together(manifest_job):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": "Первое событие", "conditions": ["Испания"]}, {"claim": "Другое событие", "conditions": ["Франция"]}])
    result = merge(store, job, merge_sender(no_progress=True))
    assert result["state"] == "ready" and result["partial"] is False
    assert len(result["frontier"]) == 2
    assert {record["record_id"] for ref in result["frontier"] for record in leafs(store, job, ref)} == {r["record_id"] for r in original}


def test_s19_hierarchical_merge_depth_cap_is_partial_without_new_calls(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Факт {n}"} for n in range(8)])
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    change_setting(store, job, "analysis", "max_merge_depth", 1)
    result = merge(store, job, merge_sender())
    assert result["partial"] is True and "max_merge_depth" in result["reasons"]
    calls = store.get_job(job["job_id"])["llm_calls"]
    assert merge(store, job, lambda *a, **k: pytest.fail("Known depth stop must not loop"))["partial"] is True
    assert store.get_job(job["job_id"])["llm_calls"] == calls


def test_s19_hierarchical_merge_reserves_actual_remaining_frontier(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Факт {n}"} for n in range(8)])
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    change_setting(store, job, "analysis", "max_llm_calls", 4)
    before = store.get_job(job["job_id"])["provider_requests"]
    result = merge(store, job, lambda *a, **k: pytest.fail("Do not start when known merge tail cannot fit"))
    assert result["partial"] is True and "max_llm_calls" in result["reasons"]
    assert store.get_job(job["job_id"])["provider_requests"] == before


def test_s19_hierarchical_merge_link_decisions_are_visible_to_reducer(manifest_job):
    from telegram_connector.tests.test_analysis_relations import link, link_sender
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    link(store, job, link_sender("contradicts"))
    delegate = merge_sender()
    def sender(req, timeout):
        if not req.full_url.endswith("input_tokens"):
            data = json.loads(json.loads(req.data)["input"].split("Записи: ", 1)[1])
            assert any(item["relation"] == "contradicts" for item in data.get("relations", []))
        return delegate(req, timeout)
    assert merge(store, job, sender)["partial"] is False


@pytest.mark.parametrize("count,fan_in,calls", [(5, 2, 4), (9, 8, 2)])
def test_s19_hierarchical_merge_carries_unary_remainder_without_inference(manifest_job, count, fan_in, calls):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Факт {n}"} for n in range(count)])
    change_setting(store, job, "analysis", "merge_fan_in", fan_in)
    assert merge(store, job, merge_sender())["partial"] is False
    assert store.get_job(job["job_id"])["llm_calls"] == calls


def test_s19_hierarchical_merge_token_limit_reduces_fan_in_and_recounts(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Факт {n}"} for n in range(4)])
    delegate = merge_sender()
    def sender(req, timeout):
        data = json.loads(json.loads(req.data)["input"].split("Записи: ", 1)[1])
        if req.full_url.endswith("input_tokens") and len(data["inputs"]) > 2:
            return Response({"input_tokens": 20000})
        return delegate(req, timeout)
    assert merge(store, job, sender)["state"] == "ready"
    assert store.get_job(job["job_id"])["llm_calls"] == 3


def test_s19_hierarchical_merge_atomic_checkpoint_failure_keeps_usage(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    delegate = merge_sender()
    def sender(req, timeout):
        response = delegate(req, timeout)
        if not req.full_url.endswith("input_tokens"):
            store.conn.execute("""CREATE TEMP TRIGGER refuse_merge_commit BEFORE UPDATE ON analysis_jobs
                WHEN json_extract(NEW.checkpoint_json,'$.merge.state')='ready'
                BEGIN SELECT RAISE(ABORT,'fixture-merge-checkpoint'); END""")
        return response
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="fixture-merge-checkpoint"):
        merge(store, job, sender)
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='merge' AND state='succeeded'").fetchone()[0] == 0
    saved = store.get_job(job["job_id"])
    assert saved["checkpoint"]["merge"]["cursor"] == 0 and saved["used_tokens"] > 0
    assert saved["llm_calls"] == 1 and saved["reserved_tokens"] == 0


def test_s19_hierarchical_merge_manifest_repair_invalidates_only_affected_leafs_and_ancestors(manifest_job):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": f"Сумма {n} EUR."} for n in range(4)])
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    result = merge(store, job, merge_sender())
    changed = next(record for record in original if record["payload"]["source_refs"][0]["message_id"] == 1)
    preserved = next(record for record in original if record["payload"]["source_refs"][0]["message_id"] == 4)
    changed = store.leaf_evidence(job["job_id"], changed["record_id"])
    preserved = store.leaf_evidence(job["job_id"], preserved["record_id"])
    before = store.get_job(job["job_id"])
    store.conn.execute("UPDATE messages SET text='Сумма 9 EUR.' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        merge(store, job, lambda *a, **k: pytest.fail("Edited source must stop before a new request"))
    current = resume(store, job)
    build(store, current)
    assert store.get_batch(changed["batch_id"])["state"] == "skipped"
    assert store.get_batch(changed["batch_id"])["payload"]["invalidated"] is True
    assert store.leaf_evidence(job["job_id"], changed["record_id"]) is None
    assert store.leaf_evidence(job["job_id"], preserved["record_id"]) is not None
    for root in result["frontier"]:
        assert store.get_batch(root["batch_id"])["payload"]["invalidated"] is True
    after = store.get_job(job["job_id"])
    assert "merge" not in after["checkpoint"]
    assert all(after[key] == before[key] for key in ("llm_calls", "provider_requests", "used_tokens", "reserved_tokens"))
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def extract_ready(store, job, ready):
    data = document(ready)
    data["records"][0]["claim"] = ready["messages"][0]["message"]["text"]
    return extract(store, job, ready, lambda req, timeout: Response({"input_tokens": 100})
        if req.full_url.endswith("input_tokens") else Response(model_response(data)))


@pytest.mark.parametrize("mutation", ["delete", "date_order"])
def test_s19_hierarchical_merge_real_batch_composition_after_repair_reuses_valid_extraction(manifest_job, mutation):
    store, job = manifest_job
    for number in range(1, 4):
        seed(store, 22, [number], text=f"Факт {number}")
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 1)
    build(store, job)
    for ready in batches(store, job):
        assert extract_ready(store, job, ready)["outcome"] == "succeeded"
    previous = list(store.iter_leaf_evidence(job["job_id"], page_size=2))
    merge(store, job, merge_sender())
    if mutation == "delete":
        store.conn.execute("DELETE FROM messages WHERE channel_id=22 AND message_id=1")
    else:
        store.conn.execute("UPDATE messages SET date_utc='2026-10-12T11:00:00+00:00' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        merge(store, job, merge_sender())
    current = resume(store, job)
    build(store, current)
    pending = list(batches(store, current))
    assert len(pending) == (0 if mutation == "delete" else 1)
    for ready in pending:
        assert extract_ready(store, current, ready)["outcome"] == "succeeded"
    result = merge(store, current, merge_sender())
    restored = {record["record_id"]: record for root in result["frontier"] for record in leafs(store, current, root)}
    assert {ref["message_id"] for record in restored.values() for ref in record["payload"]["source_refs"]} == ({2, 3} if mutation == "delete" else {1, 2, 3})
    assert all(record["record_id"] in restored for record in previous if record["payload"]["source_refs"][0]["message_id"] in {2, 3})


def test_s19_hierarchical_merge_rebuild_adopts_compatible_saved_subtrees_without_new_calls(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Факт {n}"} for n in range(8)])
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    original = merge(store, job, merge_sender())
    current = store.get_job(job["job_id"])
    checkpoint = copy.deepcopy(current["checkpoint"])
    checkpoint.pop("merge")
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=67108864)
    result = merge(store, job, lambda *a, **k: pytest.fail("Valid same-job subtrees must be adopted after rebuilding frontier"))
    assert result["frontier"] == original["frontier"]
    after = store.get_job(job["job_id"])
    assert all(after[key] == current[key] for key in ("llm_calls", "provider_requests", "used_tokens", "reserved_tokens"))


def test_s19_hierarchical_merge_explicit_invalidation_closure_is_independent_of_sql_row_order(manifest_job):
    from telegram_connector.tests.test_analysis_relations import link, link_sender
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    link(store, job, link_sender("contradicts"))
    changed = store.leaf_evidence(job["job_id"], original[0]["record_id"])
    store.conn.execute("PRAGMA reverse_unordered_selects=ON")
    store.invalidate_batch_dependencies(job["job_id"], changed["batch_id"], generation=job["generation"])
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='link' AND state='succeeded'").fetchone()[0] == 0


def test_s19_hierarchical_merge_failed_extraction_cannot_be_reported_as_complete_empty(manifest_job):
    store, job = manifest_job
    failed = store.plan_batch(job["job_id"], generation=job["generation"], step_key="extract/failed", kind="extract", payload={})
    store.set_batch_outcome(job["job_id"], failed["batch_id"], generation=job["generation"], outcome="refused")
    result = merge(store, job, lambda *a, **k: pytest.fail("There is no validated evidence to merge"))
    assert result["partial"] is True and "extraction_incomplete" in result["reasons"]
    assert store.get_batch(failed["batch_id"])["payload"]["disposition"] == "excluded"


def test_s19_hierarchical_merge_valid_empty_stays_distinct_from_failed_input(manifest_job):
    store, job = manifest_job
    empty = store.plan_batch(job["job_id"], generation=job["generation"], step_key="extract/empty", kind="extract", payload={})
    store.complete_batch(job["job_id"], empty["batch_id"], generation=job["generation"], records=[], inputs=[])
    result = merge(store, job, lambda *a, **k: pytest.fail("A valid empty extraction requires no merge call"))
    assert result["state"] == "ready" and result["partial"] is False
    assert store.get_batch(empty["batch_id"])["payload"]["disposition"] == "irrelevant"


def test_s19_hierarchical_merge_initial_frontier_byte_cap_is_explicit_partial(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    change_setting(store, job, "analysis", "max_intermediate_bytes", store.storage_usage(job["job_id"])["allocated_bytes"])
    result = merge(store, job, lambda *a, **k: pytest.fail("Byte cap must be checked before any provider request"))
    assert result["partial"] is True and "max_intermediate_bytes" in result["reasons"]
    assert store.get_job(job["job_id"])["checkpoint"]["budget_stop"]["reason"] == "max_intermediate_bytes"
    reader = getattr(store, "iter_leaf_dispositions", None)
    assert callable(reader), "Every leaf needs a readable disposition even when there is no space for another write"
    values = list(reader(job["job_id"]))
    assert len(values) == 2 and {value["disposition"] for value in values} == {"excluded"}


def test_s19_hierarchical_merge_cycle_is_rejected_even_with_valid_local_node_digests(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    result = merge(store, job, merge_sender())
    root = result["frontier"][0]
    saved = store.get_batch(root["batch_id"])["payload"]
    node = saved["output"]["groups"][0]
    node["input_ids"] = [node["node_id"]]
    root = analysis_pipeline._merge_reference("merge", root["batch_id"], root["node_id"], node)
    saved["plan"]["inputs"] = [root]
    store.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?", (analysis.canonical_json(saved), root["batch_id"]))
    with pytest.raises(ValueError, match="merge_lineage_cycle"):
        leafs(store, job, root)


@pytest.mark.parametrize("change", ["missing_disposition", "duplicate_disposition", "unused_support", "model_id"])
def test_s19_hierarchical_merge_rejects_inconsistent_dispositions_and_model_ids(manifest_job, change):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    refs = [analysis_pipeline._merge_reference("evidence", store.leaf_evidence(job["job_id"], value["record_id"])["batch_id"], value["record_id"], value["payload"]) for value in original]
    data = {"groups": [{"title": "Тема", "summary": "Факты", "input_ids": [ref["node_id"] for ref in refs]}],
        "dispositions": [{"input_id": ref["node_id"], "disposition": "merged", "reason": "Включено"} for ref in refs], "saturated": False}
    if change == "missing_disposition": data["dispositions"].pop()
    if change == "duplicate_disposition": data["dispositions"].append(copy.deepcopy(data["dispositions"][0]))
    if change == "unused_support": data["dispositions"][0]["disposition"] = "irrelevant"
    if change == "model_id": data["groups"][0]["node_id"] = "forged"
    with pytest.raises(ValueError):
        analysis_pipeline._validate_merge_document(data, refs, max_bytes=67108864)


@pytest.mark.parametrize("disposition,partial", [("irrelevant", False), ("excluded", True)])
def test_s19_hierarchical_merge_discarded_input_is_explicit_and_leaf_is_retained(manifest_job, disposition, partial):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        data = json.loads(json.loads(req.data)["input"].split("Записи: ", 1)[1])
        return Response(model_response({"groups": [], "dispositions": [{"input_id": value["node_id"],
            "disposition": disposition, "reason": "Явное исключение"} for value in data["inputs"]], "saturated": False}))
    result = merge(store, job, sender)
    assert result["state"] == "ready" and result["partial"] is partial and result["frontier"] == []
    assert store.get_job(job["job_id"])["evidence_records"] == 2
    assert {json.loads(row[0])["disposition"] for row in store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='extract'")} == {disposition}


def test_s19_hierarchical_merge_manifest_and_invalidation_roll_back_together(manifest_job):
    import sqlite3
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    merge(store, job, merge_sender())
    store.conn.execute("UPDATE messages SET text='Изменено' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        merge(store, job, merge_sender())
    current = resume(store, job)
    before = store.get_job(job["job_id"])
    inputs = [tuple(row) for row in store.conn.execute("SELECT * FROM analysis_inputs ORDER BY batch_id,message_id")]
    store.conn.execute("""CREATE TEMP TRIGGER refuse_invalidation BEFORE UPDATE ON analysis_batches
        WHEN NEW.kind='merge' AND NEW.state='skipped' BEGIN SELECT RAISE(ABORT,'fixture-invalidation'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture-invalidation"):
        build(store, current)
    assert [tuple(row) for row in store.conn.execute("SELECT * FROM analysis_inputs ORDER BY batch_id,message_id")] == inputs
    after = store.get_job(job["job_id"])
    assert after["checkpoint"] == before["checkpoint"] and after["evidence_records"] == 2
    assert all(after[key] == before[key] for key in ("llm_calls", "provider_requests", "used_tokens", "reserved_tokens"))


def test_s19_hierarchical_merge_one_leaf_is_forwarded_without_a_redundant_rank_call(manifest_job):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": "Единственный факт"}])
    result = merge(store, job, lambda *a, **k: pytest.fail("One input has no ordering or grouping decision"))
    assert result["state"] == "ready" and result["partial"] is False
    assert result["frontier"][0]["node_id"] == original[0]["record_id"]
    assert {value["disposition"] for value in store.iter_leaf_dispositions(job["job_id"])} == {"used"}


def test_s19_hierarchical_merge_disposition_reader_distinguishes_unprocessed_from_empty(manifest_job):
    store, job = manifest_job
    pending = store.plan_batch(job["job_id"], generation=job["generation"], step_key="extract/pending", kind="extract", payload={"inputs": [{"message_id": 1}]})
    assert list(store.iter_leaf_dispositions(job["job_id"])) == [{"batch_id": pending["batch_id"], "disposition": "excluded"}]
