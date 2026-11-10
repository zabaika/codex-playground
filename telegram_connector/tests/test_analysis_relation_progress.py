import copy
import json
from urllib.error import URLError

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector import analysis_telemetry
from telegram_connector import telegram_analysis as analysis
from telegram_connector.analysis_store import StaleGenerationError
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting
from telegram_connector.tests.test_analysis_relations import records_fixture, link
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response


def scope(count=48):
    return {f"p{i:03}": [f"left-{i}", f"right-{i}"] for i in range(1, count + 1)}


def decision(pair_id, relation="unrelated", reason="Разные события."):
    return {"pair_id": pair_id, "relation": relation, "reason": reason}


def test_pair_format_limits_addresses_to_application_owned_scope():
    pairs = scope()
    value = evidence.relation_format(pairs)
    assert value["name"] == "telegram_relations_v2" and value["strict"]
    props = value["schema"]["properties"]["decisions"]["items"]["properties"]
    assert props["pair_id"]["enum"] == list(pairs)
    assert "left_id" not in props and "right_id" not in props


@pytest.mark.parametrize("repair,accepted,missing", [(False, 47, 1), (True, 48, 0)])
def test_scope_error_preserves_requested_decisions(repair, accepted, missing):
    pairs = scope()
    values = [decision(key) for key in list(pairs)[int(not repair):]]
    values.append(decision('foreign', reason='Незапрошенная комбинация.'))
    result = evidence.classify_relation_document({"decisions": values, "saturated": False}, pairs,
                                                 max_bytes=100000)
    assert len(result["accepted_decisions"]) == accepted
    assert len(result["unresolved_pair_ids"]) == missing
    assert result["diagnostics"]["extra"] == 1


def test_unknown_pair_duplicate_and_conflict_are_classified_without_last_write_wins():
    pairs = scope(3)
    values = [decision("p001"), decision("p001"), decision("p002"),
              decision("p002", "contradicts"), decision("foreign")]
    result = evidence.classify_relation_document({"decisions": values, "saturated": False}, pairs,
                                                 max_bytes=100000)
    assert list(result["accepted_decisions"]) == ["p001"]
    assert result["unresolved_pair_ids"] == ["p002", "p003"]
    assert {key: result["diagnostics"][key] for key in ("accepted", "missing", "extra", "duplicate", "conflicting")} == {
        "accepted": 1, "missing": 1, "extra": 1, "duplicate": 1, "conflicting": 1}
    assert result["accepted_decisions"]["p001"]["left_id"] == "left-1"


def test_same_relation_with_different_reason_is_an_unresolved_conflict():
    pairs = scope(1)
    result = evidence.classify_relation_document({"decisions": [decision("p001"),
        decision("p001", reason="Другое объяснение.")], "saturated": False}, pairs, max_bytes=100000)
    assert not result["accepted_decisions"] and result["unresolved_pair_ids"] == ["p001"]


@pytest.mark.parametrize("document", [{"wrong": "shape"}, {"decisions": [decision("p001", "invented")], "saturated": False}])
def test_invalid_structure_is_not_partial_success(document):
    with pytest.raises(ValueError):
        evidence.classify_relation_document(document, scope(1), max_bytes=100000)


def test_classifier_preserves_saturation_and_enforces_size():
    value = {"decisions": [decision("p001")], "saturated": True}
    original = copy.deepcopy(value)
    result = evidence.classify_relation_document(value, scope(1), max_bytes=100000)
    assert result["saturated"] is True and value == original
    with pytest.raises(ValueError, match="relation_output_limit"):
        evidence.classify_relation_document(value, scope(1), max_bytes=1)


def fixture_pairs(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Событие {i}", "topic_key": "общее"} for i in range(3)])
    return store, job


def request_pairs(req):
    payload = json.loads(req.data)
    data = json.loads(payload["input"].split("Кандидаты: ", 1)[1])
    return payload, data["pairs"]


def test_link_repairs_only_missing_pair_and_commits_partial_progress(manifest_job):
    store, job = fixture_pairs(manifest_job)
    calls = []
    def send(req, timeout):
        payload, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(pairs)
        assert payload["text"]["format"]["name"] == "telegram_relations_v2"
        if len(calls) == 1:
            values = [decision(pair["pair_id"]) for pair in pairs[:-1]]
        else:
            assert len(pairs) == 1
            assert len(list(store.iter_link_decisions(job["job_id"]))) == 2
            values = [decision(pairs[0]["pair_id"]), decision("foreign")]
        return Response(model_response({"decisions": values, "saturated": False}))
    assert not link(store, job, send)["partial"]
    assert [len(values) for values in calls] == [3, 1]
    assert len(list(store.iter_link_decisions(job["job_id"]))) == 3
    batch = store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='link'").fetchone()
    payload = json.loads(batch[0])
    assert payload["repair_attempts"] == 1
    assert payload["link_progress"]["diagnostics"][-1]["extra"] == 1
    before = store.get_job(job["job_id"])["used_tokens"]
    assert not link(store, job, lambda *a, **k: pytest.fail("No second inference after completion"))["partial"]
    assert store.get_job(job["job_id"])["used_tokens"] == before


def test_partial_progress_survives_stop_before_repair_and_resume(manifest_job, monkeypatch):
    store, job = fixture_pairs(manifest_job)
    calls = []
    def send(req, timeout):
        payload, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(pairs)
        return Response(model_response({"decisions": [decision(v["pair_id"]) for v in
            (pairs[:-1] if len(calls) == 1 else pairs)], "saturated": False}))
    original = store.begin_link_request
    stopped = []
    def stop_after_claim(*args, **kwargs):
        value = original(*args, **kwargs)
        if value and value["payload"].get("repair_attempts") and not stopped:
            stopped.append(True)
            raise InterruptedError("After durable repair claim, before physical request")
        return value
    monkeypatch.setattr(store, "begin_link_request", stop_after_claim)
    with pytest.raises(InterruptedError):
        link(store, job, send)
    assert len(list(store.iter_link_decisions(job["job_id"]))) == 2
    assert not link(store, job, send)["partial"]
    assert [len(v) for v in calls] == [3, 1]
    assert json.loads(store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='link'").fetchone()[0])["repair_attempts"] == 1


def test_exhausted_repair_keeps_accepted_subset_visible(manifest_job):
    store, job = fixture_pairs(manifest_job)
    change_setting(store, job, "analysis", "max_repair_attempts", 0)
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        return Response(model_response({"decisions": [decision(pairs[0]["pair_id"])], "saturated": False}))
    assert link(store, job, send)["partial"]
    assert len(list(store.iter_link_decisions(job["job_id"]))) == 1
    assert link(store, job, lambda *a, **k: pytest.fail("Exhausted repair stays exhausted"))["partial"]
    report = analysis_telemetry.summarize_job(store, job["job_id"])
    assert {key: report["relations"][key] for key in ("planned_pairs", "accepted_pairs", "unresolved_pairs")} == {
        "planned_pairs": 3, "accepted_pairs": 1, "unresolved_pairs": 2}
    diagnostic = report["relations"]["batches"][0]["diagnostics"][0]
    assert len(diagnostic["scope_sha256"]) == 64 and diagnostic["attempt_id"]


def test_receipt_without_repair_observation_does_not_replay_paid_request(manifest_job, monkeypatch):
    store, job = fixture_pairs(manifest_job)
    calls = []
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(pairs)
        return Response(model_response({"decisions": [decision(v["pair_id"]) for v in
            (pairs[:-1] if len(calls) == 1 else pairs)], "saturated": False}))
    original = store.save_link_progress
    def lose_observation(*args, **kwargs):
        if kwargs["progress"]["diagnostics"][-1]["repair_index"]:
            raise InterruptedError("Receipt retained; repair response lost before commit")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "save_link_progress", lose_observation)
    with pytest.raises(InterruptedError):
        link(store, job, send)
    usage = store.get_job(job["job_id"])["used_tokens"]
    monkeypatch.setattr(store, "save_link_progress", original)
    assert link(store, job, lambda *a, **k: pytest.fail("Do not replay an observed physical attempt"))["partial"]
    assert len(calls) == 2 and len(list(store.iter_link_decisions(job["job_id"]))) == 2
    assert store.get_job(job["job_id"])["used_tokens"] == usage
    report = analysis_telemetry.summarize_job(store, job["job_id"])
    assert "relation_observation_missing" in report["relations"]["batches"][0]["diagnostics"][-1]["codes"]


def test_link_progress_rejects_stale_generation_before_write(manifest_job):
    store, job = manifest_job
    with pytest.raises(StaleGenerationError):
        store.begin_link_request(job["job_id"], "irrelevant", generation=job["generation"] + 1,
                                 expected=None, progress={})


def test_pair_schema_stays_within_provider_enum_limit():
    evidence.relation_format(scope(evidence.RELATION_PAIR_ENUM_LIMIT))
    with pytest.raises(ValueError, match="relation_pair_enum_limit"):
        evidence.relation_format(scope(evidence.RELATION_PAIR_ENUM_LIMIT + 1))


def test_saturated_parent_retains_accepted_decision_without_child_duplicates(manifest_job):
    store, job = fixture_pairs(manifest_job)
    calls = []
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(pairs)
        if len(calls) == 1:
            return Response(model_response({"decisions": [decision(pairs[0]["pair_id"])], "saturated": True}))
        assert len(pairs) == 1
        return Response(model_response({"decisions": [decision(pairs[0]["pair_id"])], "saturated": False}))
    assert not link(store, job, send)["partial"]
    decisions = list(store.iter_link_decisions(job["job_id"]))
    assert len(decisions) == len({(v["left_id"], v["right_id"]) for v in decisions}) == 3
    assert [len(v) for v in calls] == [3, 1, 1]
    assert not link(store, job, lambda *a, **k: pytest.fail("Resume must reuse split children"))["partial"]
    assert not store.incomplete_evidence_reasons(job["job_id"])


def test_budget_stop_before_repair_retains_subset_and_original_repair_count(manifest_job, monkeypatch):
    store, job = fixture_pairs(manifest_job)
    original = store.reserve_provider_attempt
    def limited(*args, **kwargs):
        if kwargs["request_kind"] == "inference" and store.get_job(job["job_id"])["llm_calls"]:
            raise analysis.BudgetExceededError("max_total_tokens")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "reserve_provider_attempt", limited)
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        return Response(model_response({"decisions": [decision(p["pair_id"]) for p in pairs[:-1]], "saturated": False}))
    assert link(store, job, send)["partial"]
    assert len(list(store.iter_link_decisions(job["job_id"]))) == 2
    before = store.get_job(job["job_id"])
    assert before["checkpoint"]["budget_stop"]["reason"] == "max_total_tokens"
    assert link(store, job, send)["partial"]
    payload = json.loads(store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='link'").fetchone()[0])
    assert payload["repair_attempts"] == 1 and store.get_job(job["job_id"])["llm_calls"] == 1


@pytest.mark.parametrize('version', [7, 8])
def test_frozen_old_policy_is_rejected_before_execution_or_phase_writes(manifest_job, version):
    store, job = manifest_job
    frozen = copy.deepcopy(job["request"])
    frozen["settings"]["algorithm_version"] = version
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (json.dumps(frozen), job["job_id"]))
    current = store.get_job(job["job_id"])
    phases = store.phase_runs(job["job_id"])
    with pytest.raises(ValueError, match="analysis_policy_incompatible"):
        analysis.execute_analysis_job(store, current,
            access_provider=lambda: pytest.fail("Reject old policy before access or secret resolution"))
    assert store.phase_runs(job["job_id"]) == phases
    assert store.get_job(job["job_id"])["provider_requests"] == 0
    with pytest.raises(ValueError, match="analysis_policy_incompatible"):
        pipeline._link_pair_group(store, job["job_id"], [], generation=job["generation"],
                                  urlopen_func=lambda *a, **k: pytest.fail("Old policy must not request inference"))


def test_partial_link_commit_guards_saved_progress_and_accepted_values(manifest_job, monkeypatch):
    store, job = fixture_pairs(manifest_job)
    original = store.begin_link_request
    def stop(*args, **kwargs):
        if kwargs["progress"]["diagnostics"]:
            raise InterruptedError("Before repair claim")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "begin_link_request", stop)
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        return Response(model_response({"decisions": [decision(p["pair_id"]) for p in pairs[:-1]], "saturated": False}))
    with pytest.raises(InterruptedError):
        link(store, job, send)
    batch_id = store.conn.execute("SELECT batch_id FROM analysis_batches WHERE kind='link'").fetchone()[0]
    saved = store.get_batch(batch_id)["payload"]["link_progress"]
    changed = copy.deepcopy(saved)
    next(iter(changed["accepted_decisions"].values()))["reason"] = "Подмена принятого решения"
    with pytest.raises(StaleGenerationError, match="Link progress changed"):
        original(job["job_id"], batch_id, generation=job["generation"], expected=None, progress=saved)
    with pytest.raises(ValueError, match="saved progress"):
        original(job["job_id"], batch_id, generation=job["generation"], expected=saved, progress=changed)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        store.save_link_progress(job["job_id"], batch_id, generation=job["generation"], expected=saved, progress=changed)
    assert store.get_batch(batch_id)["payload"]["link_progress"] == saved


def test_preflight_stop_is_not_reported_as_lost_inference_observation(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первое событие", "topic_key": "общая"},
                                {"claim": "Второе событие", "topic_key": "общая"}])
    def send(req, timeout):
        assert req.full_url.endswith("input_tokens")
        return Response({"input_tokens": job["request"]["settings"]["analysis"]["max_input_tokens"] + 1})
    assert link(store, job, send)["partial"]
    assert store.get_job(job["job_id"])["llm_calls"] == 0
    diagnostic = analysis_telemetry.summarize_job(store, job["job_id"])["relations"]["batches"][0]["diagnostics"][-1]
    assert "relation_preflight_stop" in diagnostic["codes"]
    assert "max_input_tokens" in diagnostic["codes"]
    assert "relation_observation_missing" not in diagnostic["codes"]


def test_unknown_repair_preserves_reservation_and_does_not_replay(manifest_job):
    store, job = fixture_pairs(manifest_job)
    inferences = []
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        inferences.append(pairs)
        if len(inferences) > 1:
            raise URLError("offline fixture: unknown outcome")
        return Response(model_response({"decisions": [decision(p["pair_id"]) for p in pairs[:-1]], "saturated": False}))
    with pytest.raises(pipeline.openai_api.OpenAIRequestError):
        link(store, job, send)
    before = store.get_job(job["job_id"])
    assert before["reserved_tokens"] > 0 and len(inferences) == 3
    assert link(store, job, lambda *a, **k: pytest.fail("Do not repeat unknown repair"))["partial"]
    after = store.get_job(job["job_id"])
    assert after["reserved_tokens"] == before["reserved_tokens"]
    assert after["provider_requests"] == before["provider_requests"]
    assert len(list(store.iter_link_decisions(job["job_id"]))) == 2
    report = analysis_telemetry.summarize_job(store, job["job_id"])
    assert "relation_outcome_unknown" in report["relations"]["batches"][0]["diagnostics"][-1]["codes"]
    assert report["usage"]["missing_inference_receipts"] == 2
