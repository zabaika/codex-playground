import copy
from datetime import datetime, timezone
from datetime import timedelta
import json
import hashlib

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_evidence import evidence_batch, extract, document, model_response
from telegram_connector.tests.test_analysis_manifest import manifest_job, build, seed, change_setting
from telegram_connector.tests.test_analysis_batching import batches
from telegram_connector.tests.test_analysis_transport import Response


def key(job, batch):
    function = getattr(analysis, "extraction_cache_key", None)
    assert callable(function), "Leaf cache identity must cover semantic inputs and principal scope, not transient job IDs"
    return function(job, batch)


def test_s17_cache_lifecycle_identity_ignores_job_ids_and_final_detail(evidence_batch):
    store, job, batch = evidence_batch
    job = store.get_job(job["job_id"])
    other, fragments = copy.deepcopy(job), copy.deepcopy(batch)
    other["job_id"] = "another-job"
    other["request"]["request"]["detail"] = "full"
    fragments["batch_id"] = "another-batch"
    for fragment in fragments["messages"] + fragments["context"]:
        fragment["input"]["batch_id"] = "another-manifest-page"
    assert key(job, batch) == key(other, fragments)


@pytest.mark.parametrize("change", ["principal", "namespace", "model", "prompt", "profile", "query", "source", "membership"])
def test_s17_cache_lifecycle_identity_changes_for_incompatible_contract(evidence_batch, change):
    store, job, batch = evidence_batch
    original = store.get_job(job["job_id"])
    other, fragments = copy.deepcopy(original), copy.deepcopy(batch)
    if change == "principal": other["requester_id"] = "another-user"
    if change == "namespace": other["request"]["principal"]["namespace"] = "another-bot"
    if change == "model": other["request"]["settings"]["processing"]["model"] = "another-model"
    if change == "prompt": other["request"]["settings"]["analysis_prompts"]["hash"] = "changed"
    if change == "profile": other["command"] = "qa"
    if change == "query": other["request"]["request"]["query"] = "Новый вопрос"
    if change == "source": fragments["messages"][0]["input"]["content_digest"] = "f" * 64
    if change == "membership": fragments["messages"].append(copy.deepcopy(fragments["messages"][0]));fragments["messages"][-1]["input"]["message_id"] = 99
    assert key(original, batch) != key(other, fragments)


def finish_origin(store, job, batch, *, empty=False, data=None):
    def send(req, timeout):
        return Response({"input_tokens": 100} if req.full_url.endswith("input_tokens") else
                        {**model_response(data if data is not None else {"records": [], "saturated": False} if empty else document(batch)), "model": "fixture-snapshot"})
    assert extract(store, job, batch, send)["outcome"] == "succeeded"
    store.conn.execute("UPDATE analysis_jobs SET result_json=? WHERE job_id=?",
        (analysis.canonical_json({"items": [{"claim": "1250 EUR", "sources": [{"channel_id": 22, "message_id": 1}]}]}), job["job_id"]))
    store.finish_job(job["job_id"], generation=job["generation"], status="completed", now=datetime.now(timezone.utc))


def new_target(store, origin, *, detail="normal", cache=True):
    original = store.get_job(origin["job_id"])
    data = copy.deepcopy(original["request"])
    data["request"]["detail"] = detail
    data["settings"]["analysis"]["cache"]["enabled"] = cache
    queued, _ = store.admit_job(data, command=original["command"], requester_id=original["requester_id"],
        destination=original["destination"], admission_key="fixture:cache-target", request_hash="fixture-target",
        accepted_at=original["accepted_at"], cutoff_at=original["cutoff_at"], max_queued_jobs=8, max_bytes=67108864)
    target = store.claim_job(queued["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))
    store.save_checkpoint(target["job_id"], {"history": copy.deepcopy(original["checkpoint"]["history"])},
                          generation=target["generation"], max_bytes=67108864)
    build(store, target)
    return target, next(batches(store, target))


def reuse(store, job, batch):
    function = getattr(analysis, "try_reuse_extraction", None)
    assert callable(function), "A cache hit must copy validated facts and lineage into the requesting job"
    return function(store, job["job_id"], batch, generation=job["generation"], now=datetime.now(timezone.utc))


def test_s17_cache_lifecycle_hit_materializes_local_ids_and_survives_origin_deletion(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    origin_id = store.conn.execute("SELECT record_id FROM analysis_evidence WHERE batch_id=?", (batch["batch_id"],)).fetchone()[0]
    target, ready = new_target(store, origin, detail="full")
    before = store.get_job(target["job_id"])["llm_calls"]
    assert reuse(store, target, ready) is True
    row = store.conn.execute("SELECT record_id,payload_json FROM analysis_evidence WHERE batch_id=?", (ready["batch_id"],)).fetchone()
    assert row[0] != origin_id and "1250 EUR" in row[1]
    assert store.get_job(target["job_id"])["llm_calls"] == before
    store.conn.execute("DELETE FROM analysis_jobs WHERE job_id=?", (origin["job_id"],))
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_evidence WHERE batch_id=?", (ready["batch_id"],)).fetchone()[0] == 1
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("empty", [False, True])
def test_s17_cache_lifecycle_valid_empty_hit_and_disabled_cache(evidence_batch, empty):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch, empty=empty)
    target, ready = new_target(store, origin, cache=empty)
    assert reuse(store, target, ready) is empty
    assert store.get_batch(ready["batch_id"])["state"] == ("succeeded" if empty else "running")


def test_s17_cache_lifecycle_actual_model_and_original_inference_age_survive_copy(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    provenance = store.get_batch(batch["batch_id"])["payload"].get("provenance")
    assert provenance and provenance["actual_model"] == "fixture-snapshot"
    target, ready = new_target(store, origin)
    assert reuse(store, target, ready) is True
    copied = store.get_batch(ready["batch_id"])["payload"]["provenance"]
    assert copied["actual_model"] == provenance["actual_model"]
    assert copied["inferred_at"] == provenance["inferred_at"]


def test_s17_cache_lifecycle_expired_inference_is_not_refreshed_by_recent_job_activity(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    saved = store.get_batch(batch["batch_id"])["payload"]
    saved["provenance"] = {"actual_model": "fixture-snapshot", "inferred_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()}
    store.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?", (analysis.canonical_json(saved), batch["batch_id"]))
    target, ready = new_target(store, origin)
    assert reuse(store, target, ready) is False


def test_s17_cache_lifecycle_extractor_uses_hit_without_a_new_provider_request(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    target, ready = new_target(store, origin)
    calls = store.get_job(target["job_id"])["provider_requests"]
    result = extract(store, target, ready, lambda *a, **k: pytest.fail("A compatible cache hit must avoid inference"))
    assert result["outcome"] == "succeeded"
    assert store.get_job(target["job_id"])["provider_requests"] == calls


def test_s17_cache_lifecycle_cross_command_requires_identical_extraction_contract(evidence_batch):
    store, job, batch = evidence_batch
    original = store.get_job(job["job_id"])
    compatible = copy.deepcopy(original)
    compatible["command"] = "qa"
    compatible["request"]["settings"]["analysis_prompts"]["commands"]["qa"] = original["request"]["settings"]["analysis_prompts"]["commands"]["topics"]
    assert key(original, batch) == key(compatible, batch)
    compatible["request"]["settings"]["auth"]["default_mode"] = "bot" if original["request"]["settings"]["auth"]["default_mode"] == "user" else "user"
    assert key(original, batch) != key(compatible, batch)


@pytest.mark.parametrize("mutation", ["same_length", "ocr", "membership"])
def test_s17_cache_lifecycle_new_manifest_mutations_invalidate_matching_origin(evidence_batch, mutation):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    if mutation == "same_length":
        store.conn.execute("UPDATE messages SET text=replace(text,'1250','9999') WHERE channel_id=22 AND message_id=1")
    elif mutation == "ocr":
        store.conn.execute("""INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,ocr_status,ocr_text,created_at)
                              VALUES (22,1,0,'photo','done','100 EUR','now')""")
    else:
        seed(store, 22, [2])
    target, ready = new_target(store, origin)
    assert reuse(store, target, ready) is False
    assert store.get_job(target["job_id"])["evidence_records"] == 0


def test_s17_cache_lifecycle_copy_obeys_target_byte_cap_atomically(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    target, ready = new_target(store, origin)
    change_setting(store, target, "analysis", "max_intermediate_bytes", store.storage_usage(target["job_id"])["allocated_bytes"])
    with pytest.raises(ValueError, match="max_intermediate_bytes"):
        reuse(store, target, ready)
    assert store.get_job(target["job_id"])["evidence_records"] == 0
    assert store.get_batch(ready["batch_id"])["state"] == "running"


def test_s17_cache_lifecycle_local_relations_are_remapped_and_foreign_graph_is_not_copied(evidence_batch):
    store, origin, batch = evidence_batch
    data = document(batch)
    data["records"].append(copy.deepcopy(data["records"][0]))
    data["records"][1]["claim"] = "Исключение: нерезиденты."
    finish_origin(store, origin, batch, data=data)
    rows = store.conn.execute("SELECT record_id,payload_json FROM analysis_evidence WHERE batch_id=? ORDER BY record_id", (batch["batch_id"],)).fetchall()
    value = json.loads(rows[0][1])
    value["related_record_ids"] = [rows[1][0], "foreign-merge-node"]
    store.conn.execute("UPDATE analysis_evidence SET payload_json=? WHERE record_id=?", (analysis.canonical_json(value), rows[0][0]))
    target, ready = new_target(store, origin)
    assert reuse(store, target, ready) is True
    copied = {row[0]: json.loads(row[1]) for row in store.conn.execute("SELECT record_id,payload_json FROM analysis_evidence WHERE batch_id=?", (ready["batch_id"],))}
    linked = next(record for record in copied.values() if record["related_record_ids"])
    assert len(linked["related_record_ids"]) == 1
    assert linked["related_record_ids"][0] in copied
    assert all(row[0] not in copied for row in rows)


def test_s17_cache_lifecycle_copy_obeys_target_record_cap_atomically(evidence_batch):
    store, origin, batch = evidence_batch
    data = document(batch)
    data["records"].append(copy.deepcopy(data["records"][0]))
    finish_origin(store, origin, batch, data=data)
    target, ready = new_target(store, origin)
    change_setting(store, target, "analysis", "max_evidence_records", 1)
    # An entry larger than the target's admissible extraction is a cache miss.
    assert reuse(store, target, ready) is False
    assert store.get_job(target["job_id"])["evidence_records"] == 0
    assert store.get_batch(ready["batch_id"])["state"] == "running"


def maintain(store, *, cap=1):
    function = getattr(store, "maintain_analysis_storage", None)
    assert callable(function), "Bounded cache eviction must preserve active data, final results and receipts"
    return function(now=datetime.now(timezone.utc), max_cache_bytes=cap)


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_review_cache_eviction_preserves_resumable_result_provenance(manifest_job, tmp_path, monkeypatch, status):
    from telegram_connector.tests.test_analysis_artifacts import full_result
    store, job = manifest_job
    source = tmp_path / "repo/telegram_connector"
    source.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(source))
    result = full_result(store, job)
    assert analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
    path, _ = analysis.resolve_analysis_artifact(store.get_job(job["job_id"]))
    original = path.read_bytes()
    path.unlink()
    store.conn.execute("UPDATE analysis_batches SET cache_key=batch_id WHERE job_id=? AND kind='extract'", (job["job_id"],))
    store.finish_job(job["job_id"], generation=job["generation"], status=status, now=datetime.now(timezone.utc))
    assert maintain(store)["cache_bytes"] <= 1
    store.resume_job(job["job_id"], generation=job["generation"], phase="verify", max_queued_jobs=8,
        now=datetime.now(timezone.utc), restore_artifact=True)
    resumed = store.claim_job(job["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))
    assert analysis.verify_analysis_sources(store, job["job_id"], resumed["result"], generation=resumed["generation"])
    assert store.get_job(job["job_id"])["evidence_records"] == 2
    analysis.write_full_analysis_artifact(store, job["job_id"], generation=resumed["generation"], restore_missing=True)
    assert path.read_bytes() == original


def test_s17_cache_lifecycle_eviction_preserves_final_result_and_receipts(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    delivery = {"state": "sent", "message_ids": [123]}
    store.conn.execute("UPDATE analysis_jobs SET delivery_json=? WHERE job_id=?", (analysis.canonical_json(delivery), origin["job_id"]))
    before = store.get_job(origin["job_id"])
    attempts = store.get_batch(batch["batch_id"])["attempts"]
    result = maintain(store)
    assert result["evicted_batches"] == 1 and result["cache_bytes"] <= 1
    after = store.get_job(origin["job_id"])
    assert after["result"] == before["result"] and after["delivery"] == delivery
    assert store.get_batch(batch["batch_id"])["attempts"] == attempts
    assert store.get_batch(batch["batch_id"])["cache_key"] is None


@pytest.mark.parametrize("status", ["queued", "running", "interrupted", "timed_out", "partial"])
def test_s17_cache_lifecycle_eviction_keeps_active_and_resumable_dependencies(evidence_batch, status):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    store.conn.execute("UPDATE analysis_jobs SET status=? WHERE job_id=?", (status, origin["job_id"]))
    assert maintain(store)["evicted_batches"] == 0
    assert store.get_job(origin["job_id"])["evidence_records"] == 1


def test_s17_cache_lifecycle_retention_uses_last_finished_worker_and_keeps_full_file(evidence_batch, tmp_path):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    report = tmp_path / "report.md"
    report.write_text("Пользовательский отчёт", encoding="utf-8")
    store.conn.execute("UPDATE analysis_jobs SET finished_at=?,full_path=?,full_hash=?,updated_at=? WHERE job_id=?",
        ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), str(report), hashlib.sha256(report.read_bytes()).hexdigest(),
         datetime.now(timezone.utc).isoformat(), origin["job_id"]))
    result = maintain(store)
    assert result["pruned_jobs"] == 1 and store.get_job(origin["job_id"]) is None
    assert report.read_text(encoding="utf-8") == "Пользовательский отчёт"


def test_s17_cache_lifecycle_large_valid_retention_does_not_overflow_datetime(evidence_batch):
    store, origin, batch = evidence_batch
    change_setting(store, origin, "analysis", "retention_days", 10**12)
    finish_origin(store, origin, batch)
    assert maintain(store, cap=67108864)["pruned_jobs"] == 0
    target, ready = new_target(store, origin)
    assert reuse(store, target, ready) is True


def test_s17_cache_lifecycle_explicit_retention_cutoff_uses_last_worker_finish(evidence_batch):
    store, origin, batch = evidence_batch
    finish_origin(store, origin, batch)
    now = datetime.now(timezone.utc)
    store.conn.execute("UPDATE analysis_jobs SET finished_at=?,updated_at=? WHERE job_id=?",
        ((now - timedelta(days=8)).isoformat(), now.isoformat(), origin["job_id"]))
    assert store.prune_expired_analysis_jobs(before=now - timedelta(days=7)) == 1


@pytest.mark.parametrize("valid", [True, False])
def test_s17_cache_lifecycle_query_expansion_slot_revalidates_and_owns_json(evidence_batch, valid):
    function = getattr(analysis, "try_reuse_query_expansion", None)
    assert callable(function), "The cache owner must support validated query JSON before S27 wires its producer"
    store, origin, batch = evidence_batch
    query = store.plan_batch(origin["job_id"], generation=origin["generation"], step_key="expand_query/0",
                             kind="expand_query", payload={"query": "ВНЖ"}, cache_key="fixture-query-key")
    output = {"terms": ["ВНЖ", "резиденция"]} if valid else {"wrong": "shape"}
    store.complete_batch(origin["job_id"], query["batch_id"], generation=origin["generation"], records=[], inputs=[],
        json_output=output, provenance={"actual_model": "fixture-snapshot", "inferred_at": datetime.now(timezone.utc).isoformat(), "cache_hit": False})
    finish_origin(store, origin, batch)
    target, _ = new_target(store, origin)
    current = store.plan_batch(target["job_id"], generation=target["generation"], step_key="expand_query/0",
                               kind="expand_query", payload={"query": "ВНЖ"}, cache_key="fixture-query-key")
    def validate_terms(value):
        if set(value) != {"terms"} or not isinstance(value["terms"], list) or any(not isinstance(term, str) for term in value["terms"]):
            raise ValueError("invalid query fixture")
        return value
    assert function(store, target["job_id"], current["batch_id"], generation=target["generation"],
                    validator=validate_terms, now=datetime.now(timezone.utc)) is valid
    if valid:
        store.conn.execute("DELETE FROM analysis_jobs WHERE job_id=?", (origin["job_id"],))
        assert store.get_batch(current["batch_id"])["payload"]["output"] == output
    else:
        assert store.get_batch(current["batch_id"])["state"] == "planned"
