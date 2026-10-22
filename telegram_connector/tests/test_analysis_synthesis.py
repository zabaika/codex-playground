import copy
import json

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relations import records_fixture
from telegram_connector.tests.test_analysis_manifest import change_setting, build, resume, NOW
from telegram_connector.analysis_store import BudgetExceededError
from telegram_connector.tests.test_analysis_merge import merge, merge_sender
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response


def synthesis_job(store, job, detail, *, limit=None, sources=3):
    current = store.get_job(job["job_id"])
    request = copy.deepcopy(current["request"])
    profile = request["settings"]["analysis"]["output"]["profiles"][detail]
    request["request"].update(detail=detail, limit=profile["default_limit"] if limit is None else limit, sources=sources)
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(request), job["job_id"]))
    return store.get_job(job["job_id"])


@pytest.mark.parametrize("detail,tokens", [("short", 1000), ("normal", 3000), ("full", 12000)])
def test_s20_detail_synthesis_profile_changes_prompt_and_real_output_budget(manifest_job, detail, tokens):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": "Минимум 1250 EUR.", "conditions": ["Паспорт обязателен"], "exceptions": ["Нерезиденты"]}])
    job = synthesis_job(store, job, detail)
    function = getattr(analysis, "build_synthesis_payload", None)
    assert callable(function), "Detail must affect the request before generation, while extraction stays shared"
    payload = function(job, {"records": original, "partial": False, "reasons": []})
    profile = job["request"]["settings"]["analysis"]["output"]["profiles"][detail]
    assert payload["max_output_tokens"] == tokens
    assert job["request"]["settings"]["analysis_prompts"]["details"][detail].strip() in payload["input"]
    assert all(value in payload["input"] for value in ("1250 EUR", "Паспорт обязателен", "Нерезиденты"))
    assert str(profile["max_chars"]) in payload["input"]
    assert f'"max_output_tokens":{tokens}' in payload["input"], "The model must receive the same output ceiling as the API"
    assert payload["store"] is False and payload["text"]["format"]["strict"] is True


def test_s20_detail_synthesis_limits_are_effective_before_request(manifest_job):
    store, job = manifest_job
    job = synthesis_job(store, job, "short", limit=2, sources=1)
    function = getattr(analysis, "build_synthesis_payload", None)
    assert callable(function)
    payload = function(job, {"records": [], "partial": False, "reasons": []})
    assert '"max_items":2' in payload["input"] and '"sources_per_item":1' in payload["input"]
    invalid = copy.deepcopy(job)
    invalid["request"]["request"]["limit"] = 11
    with pytest.raises(ValueError, match="limit"):
        function(invalid, {"records": []})


@pytest.mark.parametrize("field", ["conditions", "exceptions"])
def test_s36_synthesis_detail_arrays_have_one_application_owner(manifest_job, field):
    store, job = manifest_job
    records = records_fixture(store, job, [{"claim": "Платёж 1250 EUR.", "conditions": ["Нужен паспорт"], "exceptions": ["Студенты освобождены"]}])
    evidence = {"records": records, "partial": False, "reasons": [], "coverage": {}}
    payload = analysis.build_synthesis_payload(job, evidence)
    remote_fields = payload["text"]["format"]["schema"]["properties"]["items"]["items"]["properties"]
    assert remote_fields[field].get("maxItems") == 0, "Model must not create a parallel paraphrased detail list"
    document = {"summary": "", "items": [{"title": "Условия", "text": "Платёж 1250 EUR.", "conditions": [],
        "exceptions": [], "contradictions": [], "evidence_ids": [records[0]["record_id"]]}],
        "limitations": [], "insufficient_information": False, "saturated": False}
    document["items"][0][field] = ["Парафраз исходного ограничения"]
    with pytest.raises(ValueError, match="evidence_array_size"):
        analysis_pipeline._synthesis_result(job, evidence, document)


def test_s36_synthesis_preserves_native_details_and_only_deduplicates_format_variants(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Платёж 1250 EUR.", "conditions": [
        "Нужен паспорт.", " Нужен  паспорт ", "Паспорт не нужен", "Минимум 1250 EUR", "Максимум 1250 EUR"],
        "exceptions": ["Студенты освобождены.", "Студенты освобождены"]}])
    merge(store, job, merge_sender())
    result = synthesize(store, job, synthesis_sender())
    assert result["items"][0]["conditions"] == ["Нужен паспорт.", "Паспорт не нужен", "Минимум 1250 EUR", "Максимум 1250 EUR"]
    assert result["items"][0]["exceptions"] == ["Студенты освобождены."]
    assert result["partial"] is False


def test_s20_detail_synthesis_closing_reserve_remains_usable_after_new_work_stop(manifest_job):
    store, job = manifest_job
    change_setting(store, job, "analysis", "max_llm_calls", 1)
    extraction = store.plan_batch(job["job_id"], generation=job["generation"], step_key="extract/new", kind="extract", payload={})
    with pytest.raises(BudgetExceededError, match="max_llm_calls"):
        store.reserve_provider_attempt(job["job_id"], extraction["batch_id"], generation=job["generation"],
            attempt_id="blocked-new-work", request_kind="inference", input_tokens=100, output_tokens=1000)
    store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_llm_calls")
    final = store.plan_batch(job["job_id"], generation=job["generation"], step_key="synthesize/closing", kind="synthesize", payload={})
    receipt = store.reserve_provider_attempt(job["job_id"], final["batch_id"], generation=job["generation"],
        attempt_id="allowed-closing-work", request_kind="inference", input_tokens=100, output_tokens=1000)
    assert receipt["state"] == "reserved" and store.get_job(job["job_id"])["llm_calls"] == 1
    with pytest.raises(BudgetExceededError, match="max_llm_calls"):
        store.reserve_provider_attempt(job["job_id"], final["batch_id"], generation=job["generation"],
            attempt_id="blocked-exhausted-cap", request_kind="inference", input_tokens=100, output_tokens=1000)


def synthesize(store, job, sender):
    function = getattr(analysis, "synthesize_analysis_result", None)
    assert callable(function), "Final synthesis must consume saved reducer lineage and persist validated output"
    return function(store, job["job_id"], generation=job["generation"], api_key="fixture-key",
        capabilities={"model": "fixture-model", "context_window": 32768, "max_output_tokens": 16000, "source": "fixture"},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=sender, sleep_func=lambda _: None)


def synthesis_sender(*, forge=False, sent=None):
    def send(req, timeout):
        request = json.loads(req.data)
        if sent is not None: sent.append((req.full_url, request))
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        data = json.loads(request["input"].split("Опорные записи: ", 1)[1])
        return Response(model_response({"summary": "Сведения из корпуса.", "items": [{"title": "Условия", "text": record["payload"]["claim"],
            "conditions": [], "exceptions": [], "contradictions": [],
            "evidence_ids": ["forged" if forge else record["record_id"]]} for record in data["records"]],
            "limitations": [], "insufficient_information": not data["records"], "saturated": False}))
    return send


@pytest.mark.parametrize("detail,tokens", [("short", 1000), ("normal", 3000), ("full", 12000)])
def test_s20_detail_synthesis_runs_with_profile_cap_preserves_conditions_and_saves_result(manifest_job, detail, tokens):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Минимум 1250 EUR.", "conditions": ["Паспорт обязателен"], "exceptions": ["Нерезиденты"]}])
    job = synthesis_job(store, job, detail)
    merge(store, job, merge_sender())
    sent = []
    result = synthesize(store, job, synthesis_sender(sent=sent))
    assert result["detail"] == detail and result["partial"] is False
    assert result["items"][0]["conditions"] == ["Паспорт обязателен"]
    assert result["items"][0]["exceptions"] == ["Нерезиденты"]
    assert [body["max_output_tokens"] for url, body in sent if not url.endswith("input_tokens")] == [tokens]
    assert store.get_job(job["job_id"])["result"] == result
    before = store.get_job(job["job_id"])["provider_requests"]
    assert synthesize(store, job, lambda *a, **k: pytest.fail("Saved final synthesis must resume without new inference")) == result
    assert store.get_job(job["job_id"])["provider_requests"] == before


def test_s20_detail_synthesis_rejects_unknown_evidence_ids(manifest_job, capsys):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    change_setting(store, job, "analysis", "max_repair_attempts", 0)
    result = synthesize(store, job, synthesis_sender(forge=True))
    assert result["partial"] is True and "invalid_schema" in result["reasons"]
    assert result["items"] == []
    diagnostic = capsys.readouterr().err
    assert "synthesis_evidence_scope" in diagnostic and job["job_id"] in diagnostic
    assert "Факт с источником" not in diagnostic


def test_s38_synthesis_rejection_diagnostic_never_logs_exception_content(manifest_job, monkeypatch, capsys):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Приватное исходное сообщение"}])
    merge(store, job, merge_sender())
    change_setting(store, job, "analysis", "max_repair_attempts", 0)
    original = analysis_pipeline._synthesis_result
    def reject(current, evidence, document, **kwargs):
        if document["items"]:
            raise ValueError("sensitive-exception-text")
        return original(current, evidence, document, **kwargs)
    monkeypatch.setattr(analysis_pipeline, "_synthesis_result", reject)
    result = synthesize(store, job, synthesis_sender())
    assert result["partial"] and result["items"] == []
    diagnostic = capsys.readouterr().err
    assert "invalid_schema" in diagnostic and job["job_id"] in diagnostic
    assert "sensitive-exception-text" not in diagnostic and "Приватное" not in diagnostic


@pytest.mark.parametrize("oversized", [False, True])
def test_s20_detail_synthesis_source_edit_outside_detail_pack_interrupts_after_inference(manifest_job, oversized):
    store, job = manifest_job
    original = records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    merge(store, job, merge_sender())
    if oversized:
        change_setting(store, job, "analysis", "max_intermediate_bytes", 100000)
    evidence = analysis_pipeline._synthesis_evidence(store, store.get_job(job["job_id"]))
    guard = len(analysis.canonical_json(evidence["groups"])) + len(analysis.canonical_json(evidence["records"][0]))
    change_setting(store, job, "digest_ai", "message_block_max_chars", guard)
    delegate = synthesis_sender()
    def sender(req, timeout):
        response = delegate(req, timeout)
        if not req.full_url.endswith("input_tokens"):
            data = json.loads(json.loads(req.data)["input"].split("Опорные записи: ", 1)[1])
            assert len(data["records"]) == 1
            selected = data["records"][0]["record_id"]
            omitted = next(record for record in original if record["record_id"] != selected)
            ref = omitted["payload"]["source_refs"][0]
            store.conn.execute("UPDATE messages SET text=? WHERE channel_id=? AND message_id=?",
                ("x" * 100001 if oversized else "Изменено", ref["channel_id"], ref["message_id"]))
        return response
    before = store.get_job(job["job_id"])["used_tokens"]
    with pytest.raises(ValueError, match="input_changed"):
        synthesize(store, job, sender)
    saved = store.get_job(job["job_id"])
    assert saved["status"] == "interrupted" and saved["result"] is None and saved["used_tokens"] > before


def test_s20_detail_synthesis_uses_tail_budget_and_keeps_partial_reason(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Минимум 1250 EUR."}])
    merge(store, job, merge_sender())
    change_setting(store, job, "analysis", "max_llm_calls", 1)
    store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_llm_calls")
    result = synthesize(store, job, synthesis_sender())
    assert result["items"] and result["partial"] is True and "max_llm_calls" in result["reasons"]
    assert store.get_job(job["job_id"])["llm_calls"] == 1


def test_s20_detail_synthesis_recounts_smaller_evidence_pack_and_labels_limited_detail(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    merge(store, job, merge_sender())
    delegate = synthesis_sender()
    def sender(req, timeout):
        data = json.loads(json.loads(req.data)["input"].split("Опорные записи: ", 1)[1])
        if req.full_url.endswith("input_tokens") and len(data["records"]) > 1:
            return Response({"input_tokens": 20000})
        return delegate(req, timeout)
    result = synthesize(store, job, sender)
    assert result["partial"] is True and "synthesis_evidence_limited" in result["reasons"]
    assert result["coverage"]["processed_messages"] == 2
    assert result["coverage"]["ranked_evidence_records"] == 2 and result["coverage"]["synthesis_evidence_records"] == 1


def test_s20_detail_synthesis_atomic_result_commit_failure_preserves_receipt(manifest_job):
    import sqlite3
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    store.conn.execute("""CREATE TEMP TRIGGER refuse_final_result BEFORE UPDATE ON analysis_jobs
        WHEN NEW.result_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'fixture-final-result'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture-final-result"):
        synthesize(store, job, synthesis_sender())
    saved = store.get_job(job["job_id"])
    assert saved["result"] is None and saved["llm_calls"] == 1 and saved["used_tokens"] > 0
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='synthesize' AND state='succeeded'").fetchone()[0] == 0


def test_s20_detail_synthesis_verified_contradictions_survive_model_omission(manifest_job):
    from telegram_connector.tests.test_analysis_relations import link, link_sender
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "conditions": [], "exceptions": [], "topic_key": "срок"},
                               {"claim": "Срок 60 дней.", "conditions": [], "exceptions": [], "topic_key": "срок"}])
    link(store, job, link_sender("contradicts"))
    merge(store, job, merge_sender())
    result = synthesize(store, job, synthesis_sender())
    assert any("30 дней" in text and "60 дней" in " ".join(item["contradictions"])
               for item in result["items"] for text in item["contradictions"])
    assert all(len(item["evidence_ids"]) == 2 for item in result["items"])


def test_s20_detail_synthesis_output_limit_does_not_reduce_extraction_coverage(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первый факт"}, {"claim": "Второй факт"}])
    job = synthesis_job(store, job, "short", limit=1, sources=1)
    merge(store, job, merge_sender())
    result = synthesize(store, job, synthesis_sender())
    assert len(result["items"]) == 1 and result["partial"] is False
    assert result["coverage"]["processed_messages"] == 2 and result["coverage"]["synthesis_evidence_records"] == 2


def test_s20_detail_synthesis_valid_empty_corpus_reports_insufficiency_without_invention(manifest_job):
    store, job = manifest_job
    build(store, job)
    merge(store, job, merge_sender())
    result = synthesize(store, job, lambda *a, **k: pytest.fail("An empty corpus cannot yield sourced facts"))
    assert result["items"] == [] and result["insufficient_information"] is True and result["partial"] is False
    assert store.get_job(job["job_id"])["result"] == result


@pytest.mark.parametrize("outcome", ["refused", "incomplete", "saturated"])
def test_s20_detail_synthesis_partial_output_does_not_trigger_full_regeneration(manifest_job, outcome):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    delegate = synthesis_sender()
    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        if outcome == "refused":
            return Response({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]})
        if outcome == "incomplete":
            return Response({"status": "incomplete", "output": []})
        request = json.loads(req.data)
        data = json.loads(request["input"].split("Опорные записи: ", 1)[1])
        record = data["records"][0]
        return Response(model_response({"summary": "", "items": [{"title": "Факт", "text": record["payload"]["claim"],
            "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": [record["record_id"]]}],
            "limitations": [], "insufficient_information": False, "saturated": True}))
    result = synthesize(store, job, sender)
    assert result["partial"] is True and ("output_saturated" if outcome == "saturated" else outcome) in result["reasons"]
    assert store.get_job(job["job_id"])["llm_calls"] == 1


def test_s20_detail_synthesis_repair_budget_is_bounded(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    result = synthesize(store, job, lambda req, timeout: Response({"input_tokens": 100})
        if req.full_url.endswith("input_tokens") else Response(model_response({"wrong": "shape"})))
    assert result["partial"] is True and "invalid_schema" in result["reasons"]
    assert store.get_job(job["job_id"])["llm_calls"] == 2 and store.get_job(job["job_id"])["provider_requests"] == 4


def test_s36_synthesis_repair_retains_original_task_purpose_and_detail(manifest_job):
    store, job = manifest_job
    records = records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    requests = []
    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        requests.append(json.loads(req.data))
        result = {"wrong": "shape"} if len(requests) == 1 else {
            "summary": "", "items": [{"title": "Факт", "text": "Факт с источником", "conditions": [],
                "exceptions": [], "contradictions": [], "evidence_ids": [records[0]["record_id"]]}],
            "limitations": [], "insufficient_information": False, "saturated": False}
        return Response(model_response(result))
    assert synthesize(store, job, sender)["partial"] is False
    repair = json.loads(requests[1]["input"].split("Некорректный результат: ", 1)[1].split("\nОшибки валидации:", 1)[0])
    assert repair.get("original_task") == requests[0]["input"]
    assert "evidence" not in repair  # Original task already contains evidence.


def test_s20_detail_synthesis_oversized_repair_remains_partial_without_another_inference(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    def sender(req, timeout):
        request = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 20000 if "Некорректный результат:" in request["input"] else 100})
        return Response(model_response({"wrong": "shape"}))
    result = synthesize(store, job, sender)
    assert result["partial"] is True and "max_input_tokens" in result["reasons"]
    assert store.get_job(job["job_id"])["llm_calls"] == 1


def test_s20_detail_synthesis_char_cap_drops_complete_items_without_cutting_conditions(manifest_job):
    store, job = manifest_job
    condition = "Только с паспортом и заявлением"
    records_fixture(store, job, [{"claim": "Первый факт", "conditions": [condition], "exceptions": []},
                               {"claim": "Второй факт", "conditions": [condition], "exceptions": []}])
    frozen = copy.deepcopy(store.get_job(job["job_id"])["request"])
    frozen["settings"]["analysis"]["output"]["profiles"]["normal"]["max_chars"] = 90
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(frozen), job["job_id"]))
    merge(store, job, merge_sender())
    result = synthesize(store, job, synthesis_sender())
    assert len(result["items"]) == 1 and result["items"][0]["conditions"] == [condition]
    assert result["partial"] is True and "max_chars" in result["reasons"]
    assert store.get_job(job["job_id"])["llm_calls"] == 2  # One merge and one synthesis.


def test_s20_detail_synthesis_restart_after_failure_marker_does_not_retry_terminal_step(manifest_job, monkeypatch):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    change_setting(store, job, "analysis", "max_repair_attempts", 0)
    def crash(*a, **kw):
        raise RuntimeError("fixture crash after failure marker")
    with monkeypatch.context() as patch:
        patch.setattr(store, "save_analysis_result", crash)
        with pytest.raises(RuntimeError, match="after failure marker"):
            synthesize(store, job, synthesis_sender(forge=True))
    before = store.get_job(job["job_id"])["provider_requests"]
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=NOW)
    current = resume(store, job)
    result = synthesize(store, current, lambda *a, **kw: pytest.fail("A terminal failed synthesis keeps its bounded outcome"))
    assert result["partial"] is True and "invalid_schema" in result["reasons"]
    assert store.get_job(job["job_id"])["provider_requests"] == before


@pytest.mark.parametrize("counter,reason", [("llm_calls", "max_llm_calls"), ("used_tokens", "max_total_tokens")])
def test_s20_detail_synthesis_exhausted_actual_quota_stops_before_even_token_count(manifest_job, counter, reason):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Факт с источником"}])
    merge(store, job, merge_sender())
    cap = store.get_job(job["job_id"])["request"]["settings"]["analysis"][reason]
    store.conn.execute(f"UPDATE analysis_jobs SET {counter}=? WHERE job_id=?", (cap, job["job_id"]))
    before = store.get_job(job["job_id"])["provider_requests"]
    result = synthesize(store, job, lambda *a, **kw: pytest.fail("An exhausted actual quota cannot authorize another physical request"))
    assert result["kind"] == "budget_stop" and result["reason"] == reason
    assert store.get_job(job["job_id"])["provider_requests"] == before


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
def test_s20_detail_synthesis_same_adapter_uses_each_command_rubric(manifest_job, command):
    store, job = manifest_job
    records_fixture(store, job, [{"record_type": "answer" if command == "qa" else "claim", "claim": "Принесите паспорт."}])
    frozen = copy.deepcopy(store.get_job(job["job_id"])["request"])
    frozen["request"].update(command=command, query="Какие условия?" if command == "search" else None)
    store.conn.execute("UPDATE analysis_jobs SET command=?,request_json=? WHERE job_id=?", (command, analysis.canonical_json(frozen), job["job_id"]))
    merge(store, job, merge_sender())
    sent = []
    result = synthesize(store, job, synthesis_sender(sent=sent))
    prompt = next(body["input"] for url, body in sent if not url.endswith("input_tokens"))
    assert frozen["settings"]["analysis_prompts"]["commands"][command].strip() in prompt
    assert result["command"] == command and result["query"] == frozen["request"]["query"]


def test_s38_synthesis_smaller_pack_keeps_distinct_ranked_groups(manifest_job):
    store, job = manifest_job
    original = records_fixture(store, job, [{'claim':f'Банковский случай {i}.','topic_key':'bank'} for i in range(7)] +
        [{'claim':'Участник включён в ROI; клиент в VIES.','topic_key':'roi'}])
    def merger(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens':100})
        inputs = json.loads(json.loads(req.data)['input'].split('Записи: ',1)[1])['inputs']
        groups = [{'title':key,'summary':'Конкретные факты сохранены.', 'input_ids':[x['node_id'] for x in inputs if x['payload']['topic_key']==key]} for key in ['bank','roi']]
        return Response(model_response({'groups':groups,'dispositions':[{'input_id':x['node_id'],'disposition':'merged','reason':'В тематической группе.'} for x in inputs], 'saturated':False}))
    merge(store, job, merger)
    delegate = synthesis_sender()
    seen = []
    def sender(req, timeout):
        data = json.loads(json.loads(req.data)['input'].split('Опорные записи: ',1)[1])
        if req.full_url.endswith('input_tokens') and len(data['records']) > 2:
            return Response({'input_tokens':20000})
        if not req.full_url.endswith('input_tokens'):
            seen.append({x['payload']['topic_key'] for x in data['records']})
        return delegate(req, timeout)
    result = synthesize(store, job, sender)
    assert seen == [{'bank','roi'}], 'A large first group must not displace the other ranked group when context is reduced'
    assert result['coverage']['synthesis_evidence_records'] == 2
    assert result['coverage']['ranked_evidence_records'] == len(original)
    assert result['partial'] and 'synthesis_evidence_limited' in result['reasons']


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
def test_s38_synthesis_keeps_source_facts_when_link_diagnostics_are_large(manifest_job, command):
    from telegram_connector.tests.test_analysis_relations import link

    store, job = manifest_job
    originals = records_fixture(store, job, [
        {"record_type": "answer" if command == "qa" else "experience",
         "claim": "Участник сам спросил банк о документах.", "topic_key": "перевод"},
        {"record_type": "answer" if command == "qa" else "experience",
         "claim": "Банк ответил, что возможна последующая проверка.", "topic_key": "перевод"},
    ])
    frozen = copy.deepcopy(store.get_job(job["job_id"])["request"])
    frozen["request"].update(command=command, query="Кто спрашивал и что ответил банк?" if command == "search" else None)
    store.conn.execute("UPDATE analysis_jobs SET command=?,request_json=? WHERE job_id=?",
                       (command, analysis.canonical_json(frozen), job["job_id"]))

    def linker(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        pair = json.loads(json.loads(req.data)["input"].split("Кандидаты: ", 1)[1])["pairs"][0]
        return Response(model_response({"decisions": [{"left_id": pair[0], "right_id": pair[1],
            "relation": "complements", "reason": "Объяснение решения классификатора. " * 1000}], "saturated": False}))

    link(store, job, linker)
    saved_links = list(store.iter_link_decisions(job["job_id"]))
    assert saved_links
    merge(store, job, merge_sender())
    sent = []
    delegate = synthesis_sender(sent=sent)

    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            # Deterministic fixture units expose avoidable context pressure.
            return Response({"input_tokens": len(json.loads(req.data)["input"])})
        return delegate(req, timeout)

    result = synthesize(store, job, sender)
    assert {key for item in result["items"] for key in item["evidence_ids"]} == {r["record_id"] for r in originals}
    assert "synthesis_evidence_limited" not in result["reasons"]
    wire = json.loads(sent[0][1]["input"].split("Опорные записи: ", 1)[1])
    assert [(r["left_id"], r["right_id"], r["relation"]) for r in wire["relations"]] == [
        (r["left_id"], r["right_id"], r["relation"]) for r in saved_links]
    assert {r["record_id"]: r for r in wire["records"]} == {
        r["record_id"]: {k: r[k] for k in ("record_id", "record_type", "payload")} for r in originals}
    assert list(store.iter_link_decisions(job["job_id"])) == saved_links
