"""The evaluator grades explicit reviewed facts; it is not a semantic text matcher."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline
from telegram_connector import analysis_evidence
from telegram_shared import openai_api
from telegram_connector.tests.test_analysis_lifecycle import config
from telegram_connector.tests.test_analysis_evidence import document, model_response, evidence_batch, extract
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_transport import Response

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "tests/fixtures/analysis_quality_gold.json"


@pytest.fixture
def evaluator():
    path = ROOT / "scripts/evaluate_analysis.py"
    assert path.exists(), "Quality acceptance needs a local deterministic annotation/review evaluator"
    spec = importlib.util.spec_from_file_location("analysis_quality_evaluator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_gold():
    return json.loads(GOLD.read_text())


@pytest.mark.parametrize("hang", [False, True])
def test_review_eval_cli_has_outer_hard_ttl(evaluator, config, tmp_path, monkeypatch, hang):
    import fcntl
    import sys
    from common import ttl_runner
    config["processing"]["model"] = "gpt-5.6-luna"
    config["bridge"]["worker_process_timeout_seconds"] = 120
    gold = evaluator.validate_gold(raw_gold())
    scope = evaluator.build_scope(gold, config, config_dir=ROOT / "config", output_root=tmp_path)
    (tmp_path / "scope.json").write_text(json.dumps(scope))
    monkeypatch.setattr(analysis.bridge, "load_runtime_config", lambda: config)
    monkeypatch.setattr(evaluator, "execute_case", lambda *a, **k: pytest.fail("The parent must not run live inference or acquire its evaluation lock"))
    original, seen = ttl_runner.run_with_ttl, []
    lock = tmp_path / "hung.lock"
    def supervise(command, **kwargs):
        seen.append(command)
        assert kwargs["timeout_seconds"] == 120 and kwargs["use_caffeinate"] is False
        assert "--worker-inner" in command and scope["scope_sha256"] in command
        if not hang:
            return 124
        script = "import fcntl,signal,time;f=open(" + repr(str(lock)) + ",'w');fcntl.flock(f,fcntl.LOCK_EX);signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
        return original([sys.executable, "-c", script], **{**kwargs, "timeout_seconds": 0.5, "grace_seconds": 0.1, "poll_interval_seconds": 0.02})
    monkeypatch.setattr(ttl_runner, "run_with_ttl", supervise)
    assert evaluator.main(["--output", str(tmp_path), "run", "--case", "all", "--approved-scope", scope["scope_sha256"]]) == 124
    assert len(seen) == 1
    if hang:
        assert lock.exists()
        with lock.open() as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        audit = json.loads((tmp_path / "evaluation.last_attempt.json").read_text())
        assert audit["status"] == "timed_out"


def test_s36_eval_contract_rejected_extraction_is_captured_without_changing_validation(evaluator, tmp_path, monkeypatch):
    rejected = {"records": [{"source_refs": [{"quote": "исходная цитата"}]}], "saturated": False}
    def validate(*args, **kwargs):
        if kwargs["document"]["records"]:
            raise ValueError("evidence_supporting_span")
        return []
    monkeypatch.setattr(analysis_evidence, "validate_evidence_records", validate)
    path = tmp_path / "case.validation.json"
    with evaluator.capture_extraction_rejections(path):
        with pytest.raises(ValueError, match="^evidence_supporting_span$"):
            analysis_evidence.validate_evidence_records(None, "job", "batch", document=rejected)
        assert analysis_evidence.validate_evidence_records(None, "job", "other", document={"records": []}) == []
    assert analysis_evidence.validate_evidence_records is validate
    assert json.loads(path.read_text()) == [{"job_id": "job", "batch_id": "batch",
        "error": "evidence_supporting_span", "document": rejected}]


def test_s36_eval_contract_captures_stage_outputs_without_transport_credentials(evaluator, tmp_path, monkeypatch):
    result = {"attempt_id": "attempt", "response": model_response({"decisions": [], "saturated": True})}
    def request(store, job_id, batch_id, **kwargs):
        assert kwargs["api_key"] == "fixture-key"
        return result
    monkeypatch.setattr(analysis_pipeline, "request_counted_analysis_payload", request)
    path = tmp_path / "case.stages.json"
    with evaluator.capture_model_responses(path):
        assert analysis_pipeline.request_counted_analysis_payload(None, "job", "batch", api_key="fixture-key") is result
    assert analysis_pipeline.request_counted_analysis_payload is request
    captured = json.loads(path.read_text())
    assert captured == [{"job_id": "job", "batch_id": "batch", "attempt_id": "attempt", "response": result["response"]}]
    assert "fixture-key" not in path.read_text()


def bundle():
    result = {"summary": "", "items": [{"title": "Условия", "text": "План взимать плату отменили до запуска.", "conditions": [], "exceptions": [], "contradictions": [],
        "sources": [{"channel_id": 11, "message_id": 3}]}], "limitations": []}
    return {"case_id": "one", "result": result, "result_sha256": hashlib.sha256(analysis.canonical_json(result).encode()).hexdigest()}


@pytest.mark.parametrize("invalid", [False, True])
def test_capture_wraps_actual_extraction_and_restores_hooks(evaluator, evidence_batch, tmp_path, monkeypatch, invalid):
    store, job, batch = evidence_batch
    data = document(batch)
    if invalid:
        data["records"][0]["source_refs"][0]["quote"] = "Несуществующая цитата"
    calls = []
    def send(request, timeout):
        calls.append(request.full_url)
        return Response({"input_tokens": 100} if request.full_url.endswith("input_tokens") else model_response(data))
    monkeypatch.setattr(openai_api._JSON_OPENER, "open", lambda *a, **k: pytest.fail("Unexpected network request"))
    original_request = analysis_pipeline.request_counted_analysis_payload
    original_validate = analysis_evidence.validate_evidence_records
    stages, rejections = tmp_path / "stages.json", tmp_path / "rejections.json"
    with evaluator.capture_model_responses(stages), evaluator.capture_extraction_rejections(rejections):
        result = extract(store, job, batch, send)
    assert analysis_pipeline.request_counted_analysis_payload is original_request
    assert analysis_evidence.validate_evidence_records is original_validate
    captured = json.loads(stages.read_text())
    assert len(captured) == len([url for url in calls if url.endswith("responses")])
    assert all(entry["job_id"] == job["job_id"] and entry["batch_id"] == batch["batch_id"] for entry in captured)
    attempts = {attempt["attempt_id"] for attempt in store.get_batch(batch["batch_id"])["attempts"]}
    assert all(entry["attempt_id"] in attempts for entry in captured)
    if invalid:
        assert result["outcome"] == "invalid_schema"
        assert len(json.loads(rejections.read_text())) == len(captured)
        assert all(entry["error"] == "evidence_supporting_span" and entry["document"] == data
                   for entry in json.loads(rejections.read_text()))
    else:
        assert result["outcome"] == "succeeded" and not rejections.exists()
    assert "fixture-key" not in stages.read_text()


def review(run):
    item = run["result"]["items"][0]
    return {"result_sha256": run["result_sha256"], "reviewer": {"kind": "assistant", "id": "fixture"}, "complete": True,
        "checks": {"purpose_fit": True}, "claims": [
            {"path": "items.0.title", "start": 0, "end": len(item["title"]), "verdict": "nonfactual", "fact_ids": [], "source_refs": []},
            {"path": "items.0.text", "start": 0, "end": len(item["text"]), "verdict": "supported", "fact_ids": ["fee_cancelled"],
                "source_refs": [{"channel_id": 11, "message_id": 3}]}]}


def task():
    return {"id": "one", "expected_fact_ids": ["fee_cancelled"], "critical_fact_ids": ["fee_cancelled"],
        "allowed_fact_ids": ["fee_cancelled"], "same_item": [], "different_items": [], "review_checks": ["purpose_fit"]}


def test_s36_eval_contract_annotations_and_matrix_cover_all_profiles(evaluator):
    gold = evaluator.validate_gold(raw_gold())
    cases = evaluator.expand_cases(gold)
    assert len(cases) == 12 and len({case["id"] for case in cases}) == 12
    for command in ("topics", "search", "qa"):
        assert {case["detail"] for case in cases if case["command"] == command} == {"short", "normal", "full"}
    assert {case["mode"] for case in cases if case["command"] == "search"} == {"fast", "full"}


def test_s36_eval_contract_optional_event_is_annotated_without_inventing_event_date(evaluator):
    gold = evaluator.validate_gold(raw_gold())
    fact = next((fact for fact in gold["facts"] if fact["id"] == "exhibition_announced"), None)
    assert fact is not None, "A real extra event needs a gold source, not an automatic hallucination label"
    assert {(ref["channel_id"], ref["message_id"]) for ref in fact["sources"]} == {(11, 4)}
    assert "дата" in fact["claim"] and "не указана" in fact["claim"]
    topics = next(task for task in gold["tasks"] if task["command"] == "topics")
    assert fact["id"] in topics["allowed_fact_ids"] and fact["id"] not in topics["expected_fact_ids"]
    assert topics["critical_fact_ids"] == ["fee_conditions", "fee_cancelled", "office"]


def test_s36_eval_contract_retrieval_separates_primary_discovery_from_context(evaluator):
    gold = evaluator.validate_gold(raw_gold())
    case = next(case for case in evaluator.expand_cases(gold) if case["command"] == "search" and case["mode"] == "fast")
    run = {"retrieval": {"original_only_candidates": [[22, 2]], "expanded_candidates": [[22, 2]]},
        "selected_source_refs": [{"channel_id": 22, "message_id": 2}, {"channel_id": 11, "message_id": 1}]}
    measured = evaluator.retrieval_scores(gold, case, run)
    assert measured == {"answer_source_count": 1, "original_candidate_answer_recall": 0.0,
        "expanded_candidate_answer_recall": 0.0, "selected_answer_recall": 1.0}
    run["retrieval"]["expanded_candidates"] += [[11, 1], [11, 1]]
    assert evaluator.retrieval_scores(gold, case, run)["expanded_candidate_answer_recall"] == 1.0


@pytest.mark.parametrize("change", ["duplicate_source", "bad_quote", "unknown_fact", "naive_time", "bad_critical", "bad_group"])
def test_s36_eval_contract_invalid_annotations_fail_before_execution(evaluator, change):
    gold = raw_gold()
    if change == "duplicate_source": gold["messages"].append(copy.deepcopy(gold["messages"][0]))
    if change == "bad_quote": gold["facts"][0]["sources"][0]["quote"] = "Не существующая в источнике цитата"
    if change == "unknown_fact": gold["tasks"][0]["expected_fact_ids"].append("unknown")
    if change == "naive_time": gold["messages"][0]["date"] = "2026-10-10T09:00:00"
    if change == "bad_critical": gold["tasks"][0]["critical_fact_ids"].append("doc_cost")
    if change == "bad_group": gold["tasks"][0]["same_item"].append(["unknown", "fee_plan"])
    with pytest.raises(ValueError): evaluator.validate_gold(gold)


def test_s36_eval_contract_accepts_reviewed_paraphrase_without_literal_gold_match(evaluator):
    gold = evaluator.validate_gold(raw_gold())
    run = bundle()
    metrics = evaluator.score_review(gold, task(), run, review(run))
    assert metrics["fact_precision"] == metrics["fact_recall"] == metrics["source_support"] == 1
    assert metrics["critical_recall"] == 1 and metrics["unsupported_rate"] == 0
    assert metrics["status"] == "scored" and metrics["structural_pass"]


@pytest.mark.parametrize("fault", ["unsupported", "wrong_source", "unreviewed", "missing_critical", "failed_check", "unknown_fact", "overlap", "stale_result"])
def test_s36_eval_contract_faults_cannot_be_scored_as_clean_success(evaluator, fault):
    gold, run, case = evaluator.validate_gold(raw_gold()), bundle(), task()
    judged = review(run)
    atom = judged["claims"][-1]
    if fault == "unsupported": atom.update(verdict="unsupported", fact_ids=[], source_refs=[])
    if fault == "wrong_source": atom["source_refs"] = [{"channel_id": 22, "message_id": 3}]
    if fault == "unreviewed": judged["claims"].pop()
    if fault == "missing_critical":
        case["expected_fact_ids"].append("fee_conditions")
        case["critical_fact_ids"].append("fee_conditions")
        case["allowed_fact_ids"].append("fee_conditions")
    if fault == "failed_check": judged["checks"]["purpose_fit"] = False
    if fault == "unknown_fact": atom["fact_ids"] = ["unknown"]
    if fault == "overlap": judged["claims"].append(copy.deepcopy(atom))
    if fault == "stale_result": judged["result_sha256"] = "0" * 64
    if fault in {"unknown_fact", "overlap", "stale_result"}:
        with pytest.raises(ValueError): evaluator.score_review(gold, case, run, judged)
    else:
        metrics = evaluator.score_review(gold, case, run, judged)
        assert not metrics["structural_pass"] or metrics["critical_recall"] < 1
        if fault == "unreviewed":
            assert metrics["status"] != "scored"


@pytest.mark.parametrize("field", ["conditions", "exceptions", "contradictions", "limitations"])
def test_s36_eval_contract_every_result_field_requires_review(evaluator, field):
    run = bundle()
    target = run["result"] if field == "limitations" else run["result"]["items"][0]
    target[field] = ["Существенное ограничение"]
    run["result_sha256"] = hashlib.sha256(analysis.canonical_json(run["result"]).encode()).hexdigest()
    metrics = evaluator.score_review(evaluator.validate_gold(raw_gold()), task(), run, review(run))
    assert metrics["status"] == "unreviewed" and metrics["unreviewed_characters"] > 0 and not metrics["structural_pass"]


def test_s36_eval_contract_repeated_fact_cannot_inflate_recall(evaluator):
    run = bundle()
    run["result"]["items"].append(copy.deepcopy(run["result"]["items"][0]))
    run["result_sha256"] = hashlib.sha256(analysis.canonical_json(run["result"]).encode()).hexdigest()
    judged = review(run)
    judged["claims"].extend([{**copy.deepcopy(atom), "path": atom["path"].replace("items.0.", "items.1.")} for atom in judged["claims"]])
    case = task()
    for field in ("expected_fact_ids", "critical_fact_ids", "allowed_fact_ids"):
        case[field].append("office")
    metrics = evaluator.score_review(evaluator.validate_gold(raw_gold()), case, run, judged)
    assert metrics["fact_recall"] == metrics["critical_recall"] == 0.5


def test_s36_eval_contract_separate_events_in_one_item_fail_grouping(evaluator):
    run = bundle()
    first = run["result"]["items"][0]["text"]
    second = "В Мадриде появился офис, принимающий по записи."
    run["result"]["items"][0]["text"] += " " + second
    run["result"]["items"][0]["sources"].append({"channel_id": 22, "message_id": 3})
    run["result_sha256"] = hashlib.sha256(analysis.canonical_json(run["result"]).encode()).hexdigest()
    judged = review(run)
    judged["claims"][-1]["end"] = len(first)
    judged["claims"].append({"path": "items.0.text", "start": len(first) + 1, "end": len(first) + 1 + len(second),
        "verdict": "supported", "fact_ids": ["office"], "source_refs": [{"channel_id": 22, "message_id": 3}]})
    case = task()
    for field in ("expected_fact_ids", "critical_fact_ids", "allowed_fact_ids"):
        case[field].append("office")
    case["different_items"] = [["fee_cancelled", "office"]]
    metrics = evaluator.score_review(evaluator.validate_gold(raw_gold()), case, run, judged)
    assert metrics["fact_precision"] == metrics["fact_recall"] == 1
    assert not metrics["grouping_pass"] and not metrics["structural_pass"]


def test_s36_eval_contract_scope_is_secret_free_bounded_and_repeatable(evaluator, config, tmp_path):
    config["processing"]["model"] = "gpt-5.6-luna"
    config["digest_ai"].update(reasoning_effort="low", reasoning_summary="none")
    config["secrets"] = {"openai_api_key": "must-not-enter-scope"}
    gold = evaluator.validate_gold(raw_gold())
    scope = evaluator.build_scope(gold, config, config_dir=ROOT / "config", output_root=tmp_path)
    assert scope == evaluator.build_scope(gold, config, config_dir=ROOT / "config", output_root=tmp_path)
    assert scope["model"] == "gpt-5.6-luna" and scope["reasoning_effort"] == "low"
    assert len(scope["cases"]) == 12 and scope["limits"] == {"max_llm_calls": 24, "max_provider_requests": 96, "max_total_tokens": 200000}
    assert scope["total_limits"] == {"max_llm_calls": 288, "max_provider_requests": 1152, "max_total_tokens": 2400000}
    assert "must-not-enter-scope" not in analysis.canonical_json(scope)


def test_s36_eval_contract_scope_cannot_raise_operator_caps(evaluator, config, tmp_path):
    config["analysis"].update(max_llm_calls=5, max_provider_requests=10, max_total_tokens=10000)
    scope = evaluator.build_scope(evaluator.validate_gold(raw_gold()), config, config_dir=ROOT / "config", output_root=tmp_path)
    assert scope["limits"] == {"max_llm_calls": 5, "max_provider_requests": 10, "max_total_tokens": 10000}


def test_s36_eval_contract_review_template_is_unreviewed_and_covers_surfaces(evaluator):
    run = bundle()
    draft = evaluator.review_template(task(), run)
    assert draft["complete"] is False and all(atom["verdict"] == "uncertain" for atom in draft["claims"])
    assert {atom["path"] for atom in draft["claims"]} == {"items.0.title", "items.0.text"}


def test_s36_eval_contract_execution_requires_explicit_live_or_injected_transport(evaluator, config, tmp_path, monkeypatch):
    monkeypatch.setattr(analysis.bridge, "resolve_secret_value", lambda *a, **k: pytest.fail("Unapproved run cannot resolve credentials"))
    gold = evaluator.validate_gold(raw_gold())
    scope = evaluator.build_scope(gold, config, config_dir=ROOT / "config", output_root=tmp_path)
    with pytest.raises(ValueError, match="live_scope_not_approved"):
        evaluator.execute_case(gold, config, scope, scope["cases"][0]["id"], output_root=tmp_path)


def fixture_api(command, calls):
    def send(req, timeout):
        data = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(data)
        kind = data["text"]["format"]["name"]
        if kind == "telegram_query_terms_v1":
            result = {"terms": ["España", "pasaporte", "residencia"]}
        elif kind == "telegram_evidence_v1":
            fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
            fragment = next(value for value in fragments if value["input"]["role"] == "selected")
            result = document({"messages": [fragment]})
            result["records"][0].update(record_type="answer" if command == "qa" else "claim", claim=fragment["message"]["text"],
                conditions=[], exceptions=[], entities=["Uno"], topic_key="case")
        elif kind == "telegram_relations_v1":
            pairs = json.loads(data["input"].split("Кандидаты: ", 1)[1])["pairs"]
            result = {"decisions": [{"left_id": left, "right_id": right, "relation": "complements", "reason": "Fixture"} for left, right in pairs], "saturated": False}
        elif kind == "telegram_merge_v1":
            values = json.loads(data["input"].split("Записи: ", 1)[1])["inputs"]
            ids = [value["node_id"] for value in values]
            result = {"groups": [{"title": "Условия", "summary": "Данные корпуса", "input_ids": ids}],
                "dispositions": [{"input_id": key, "disposition": "merged", "reason": "Включено"} for key in ids], "saturated": False}
        else:
            assert kind == "telegram_synthesis_v1"
            records = json.loads(data["input"].split("Опорные записи: ", 1)[1])["records"]
            result = {"summary": "Сведения участников", "items": [{"title": "Условия", "text": records[0]["payload"]["claim"],
                "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": [records[0]["record_id"]]}],
                "limitations": [], "insufficient_information": False, "saturated": False}
        return Response(model_response(result))
    return send


@pytest.mark.parametrize("command,mode,detail", [("topics", None, "full"), ("search", "fast", "normal"), ("search", "full", "short"), ("qa", None, "normal")])
def test_s36_eval_contract_isolated_engine_capture_and_replay(evaluator, config, tmp_path, monkeypatch, command, mode, detail):
    config["processing"]["model"] = "gpt-5.6-luna"
    config["digest_ai"].update(reasoning_effort="low", reasoning_summary="none")
    project = tmp_path / "repo/telegram_connector"
    project.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(project))
    monkeypatch.setattr(analysis.bridge, "resolve_secret_value", lambda *a, **k: pytest.fail("Injected transport needs no credentials"))
    gold, calls = evaluator.validate_gold(raw_gold()), []
    output = tmp_path / "evaluation"
    scope = evaluator.build_scope(gold, config, config_dir=ROOT / "config", output_root=output)
    case = next(case for case in scope["cases"] if case["command"] == command and case["mode"] == mode and case["detail"] == detail)
    run = evaluator.execute_case(gold, config, scope, case["id"], output_root=output, config_dir=ROOT / "config",
        api_key="fixture", urlopen_func=fixture_api(command, calls))
    assert run["status"] == "completed" and run["delivery_transport"] == "local_capture"
    assert run["result_sha256"] == hashlib.sha256(analysis.canonical_json(run["result"]).encode()).hexdigest()
    assert run["usage"]["llm_calls"] <= scope["limits"]["max_llm_calls"] and run["prompt_sha256"] == scope["prompt_sha256"]
    assert run["selected_source_refs"] and len(run["local_messages"]) > 0
    if mode == "fast":
        assert [11, 1] not in run["retrieval"]["original_only_candidates"]
        assert [11, 1] in run["retrieval"]["expanded_candidates"]
        assert run["retrieval"]["comparison"] == "primary candidates before context recovery"
    if detail == "full":
        assert run["local_messages"] == [run["full_path"]] and (project.parent / run["full_path"]).exists()
    repeated = evaluator.execute_case(gold, config, scope, case["id"], output_root=output, config_dir=ROOT / "config",
        api_key="fixture", urlopen_func=lambda *a, **k: pytest.fail("A captured case cannot spend the budget again"))
    assert repeated == run


def test_s36_eval_contract_changed_scope_fails_before_credentials(evaluator, config, tmp_path, monkeypatch):
    gold = evaluator.validate_gold(raw_gold())
    scope = evaluator.build_scope(gold, config, config_dir=ROOT / "config", output_root=tmp_path)
    config["digest_ai"]["reasoning_effort"] = "high"
    monkeypatch.setattr(analysis.bridge, "resolve_secret_value", lambda *a, **k: pytest.fail("Changed scope cannot resolve credentials"))
    with pytest.raises(ValueError, match="approved_scope_changed"):
        evaluator.execute_case(gold, config, scope, scope["cases"][0]["id"], output_root=tmp_path, config_dir=ROOT / "config", live=True)


def test_s36_eval_contract_cli_prepares_scope_and_requires_matching_approval(evaluator, config, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(analysis.bridge, "load_runtime_config", lambda: config)
    arguments = ["--gold", str(GOLD), "--output", str(tmp_path), "--config-dir", str(ROOT / "config")]
    assert evaluator.main([*arguments, "prepare"]) == 0
    scope = json.loads((tmp_path / "scope.json").read_text())
    assert scope["total_limits"]["max_llm_calls"] == 288
    capsys.readouterr()
    monkeypatch.setattr(evaluator, "execute_case", lambda *a, **k: pytest.fail("No approved hash, no execution"))
    assert evaluator.main([*arguments, "run", "--case", scope["cases"][0]["id"], "--approved-scope", "wrong"]) == 2
    assert "approved_scope_changed" in capsys.readouterr().err


def test_s36_eval_contract_cli_does_not_overwrite_previous_scope(evaluator, config, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(analysis.bridge, "load_runtime_config", lambda: config)
    arguments = ["--gold", str(GOLD), "--output", str(tmp_path), "--config-dir", str(ROOT / "config"), "prepare"]
    assert evaluator.main(arguments) == 0
    original = (tmp_path / "scope.json").read_bytes()
    config["digest_ai"]["reasoning_effort"] = "high"
    assert evaluator.main(arguments) == 2
    assert "existing_scope_changed" in capsys.readouterr().err and (tmp_path / "scope.json").read_bytes() == original
