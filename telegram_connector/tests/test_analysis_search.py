import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting, seed, messages
from telegram_connector.tests.test_analysis_evidence import model_response, document
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector import telegram_history_client as history
from telegram_connector.tests.test_analysis_history import runtime
from telegram_connector.tests.test_analysis_engine import EngineHistoryClient, engine_provider, engine_access
from telegram_connector.tests.test_analysis_lifecycle import config, access, listener, update, supervisor, FixtureProcess
from telegram_connector.tests.test_analysis_history import posts
from telegram_connector.tests.test_analysis_fts import queue_index, complete_index
from telegram_connector.analysis_store import AnalysisStore
from telegram_connector import telegram_bridge as bridge
from common import sqlite as common_sqlite


@pytest.fixture
def search_job(manifest_job):
    store, job = manifest_job
    value = copy.deepcopy(job["request"])
    value["request"].update(command="search", query='Какие банки Испании открывают счёт без ВНЖ с 2026-09-01 при сумме -10 EUR?', mode="fast")
    store.conn.execute("UPDATE analysis_jobs SET command='search',request_json=? WHERE job_id=?",
        (analysis.canonical_json(value), job["job_id"]))
    return store, store.get_job(job["job_id"])


def query_request(store, job, **values):
    current = copy.deepcopy(store.get_job(job["job_id"])["request"])
    current["request"].update(values)
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(current), job["job_id"]))
    return store.get_job(job["job_id"])


def prepare_query(store, job, sender):
    function = getattr(analysis, "prepare_search_query", None)
    assert callable(function), "Fast search needs one durable bounded query-preparation owner"
    return function(store, job["job_id"], generation=job["generation"], api_key="fixture",
        capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
        deadline=100, monotonic_func=lambda: 0, sleep_func=lambda _: None, urlopen_func=sender)


def expansion_sender(output, calls):
    def send(req, timeout):
        data = json.loads(req.data)
        calls.append((req.full_url, data))
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        return Response(model_response(output))
    return send


def test_s27_query_preparation_preserves_original_constraints_and_normalizes_unicode(search_job):
    store, job = search_job
    raw = '  Испания\u00a0«Banco Uno»\tNO residentes\nбез ВНЖ с 2026-09-01, -10 EUR; cafe\u0301 '
    job = query_request(store, job, query=raw)
    calls = []
    result = prepare_query(store, job, expansion_sender({"terms": ["residencia", "NIE", " permiso de residencia ", "nie"]}, calls))
    assert result["query"] == raw and store.get_job(job["job_id"])["request"]["request"]["query"] == raw
    assert result["normalized_query"] == 'Испания «Banco Uno» NO residentes без ВНЖ с 2026-09-01, -10 EUR; café'
    assert {"Banco Uno", "NO", "residentes", "без", "ВНЖ", "2026-09-01", "-10", "EUR", "café"} <= set(result["original_terms"])
    assert result["expanded_terms"] == ["residencia", "NIE", "permiso de residencia"]
    assert result["expansion_status"] == "completed" and result["reasons"] == []
    sent = [data for url, data in calls if url.endswith("responses")]
    assert len(sent) == 1 and sent[0]["text"]["format"]["strict"] is True
    assert "Banco Uno" in sent[0]["input"] and "2026-09-01" in sent[0]["input"] and "-10 EUR" in sent[0]["input"]
    assert store.get_job(job["job_id"])["llm_calls"] == 1
    assert prepare_query(store, job, lambda *a, **k: pytest.fail("Resume must reuse the completed query step")) == result


@pytest.mark.parametrize("output", [
    {"answer": "Все банки открывают счета без ВНЖ"},
    {"terms": "residencia"}, {"terms": [42]}, {"terms": [""]},
    {"terms": ["x" * 129]}, {"terms": [f"term-{n}" for n in range(25)]},
    {"terms": ["NIE"], "answer": "Готовый неподдержанный ответ"},
])
def test_s27_query_preparation_invalid_expansion_falls_back_without_changing_question(search_job, output):
    store, job = search_job
    calls = []
    result = prepare_query(store, job, expansion_sender(output, calls))
    assert result["query"] == job["request"]["request"]["query"]
    assert result["original_terms"] and result["expanded_terms"] == []
    assert result["expansion_status"] == "fallback" and "query_expansion_invalid_schema" in result["reasons"]
    assert len([url for url, _ in calls if url.endswith("responses")]) == 1
    assert prepare_query(store, job, lambda *a, **k: pytest.fail("Failed expansion must not restart as a new logical step")) == result


def test_s27_query_preparation_full_mode_uses_no_provider_or_expansion_slot(search_job):
    store, job = search_job
    job = query_request(store, job, mode="full")
    result = prepare_query(store, job, lambda *a, **k: pytest.fail("Full search needs no expansion"))
    assert result["query"] == job["request"]["request"]["query"] and result["expanded_terms"] == []
    assert result["expansion_status"] == "not_requested"
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='expand_query'").fetchone()[0] == 0
    assert store.get_job(job["job_id"])["provider_requests"] == 0


@pytest.mark.parametrize("kind", ["refusal", "incomplete", "network"])
def test_s27_query_preparation_failed_provider_keeps_bounded_original_fallback(search_job, kind):
    store, job = search_job
    calls = []
    def send(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(1)
        if kind == "network":
            raise TimeoutError("fixture secret and query must not become a reason")
        return Response({"status": "incomplete" if kind == "incomplete" else "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "fixture refusal"}]}] if kind == "refusal" else [],
            "usage": {"input_tokens": 100, "output_tokens": 10}})
    result = prepare_query(store, job, send)
    assert result["expanded_terms"] == [] and result["expansion_status"] == "fallback"
    assert result["original_terms"] and all("secret" not in reason for reason in result["reasons"])
    assert len(calls) == (3 if kind == "network" else 1)
    assert store.get_job(job["job_id"])["llm_calls"] == len(calls)


def test_s27_query_preparation_configuration_failure_is_not_invalid_model_output(search_job):
    store, job = search_job
    with pytest.raises(ValueError, match="model_capability_unverified"):
        analysis.prepare_search_query(store, job["job_id"], generation=job["generation"], api_key="fixture",
            capabilities={"model": "unverified"},
            urlopen_func=lambda *a, **k: pytest.fail("Invalid capability must not count or infer"))
    assert store.get_job(job["job_id"])["provider_requests"] == 0


def test_s27_query_preparation_minimum_payload_too_large_uses_explicit_original_fallback(search_job):
    store, job = search_job
    calls = []
    result = prepare_query(store, job, lambda req, **kw: calls.append(req.full_url) or Response({"input_tokens": 16001}))
    assert result["expansion_status"] == "fallback" and result["expanded_terms"] == []
    assert result["reasons"] == ["query_expansion_saturated"]
    assert len(calls) == 1 and store.get_job(job["job_id"])["llm_calls"] == 0


def clone_search_job(store, origin, *, mutate=None):
    origin = store.get_job(origin["job_id"])
    value = copy.deepcopy(origin["request"])
    if mutate is not None:
        mutate(value)
    queued, _ = store.admit_job(value, command="search", requester_id=origin["requester_id"], destination=origin["destination"],
        admission_key="fixture:second-search", request_hash="fixture-second", accepted_at=origin["accepted_at"],
        cutoff_at=origin["cutoff_at"], max_queued_jobs=8, max_bytes=67108864)
    return store.claim_job(queued["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))


@pytest.mark.parametrize("change", [None, "query", "reasoning", "prompt", "model", "limits", "disabled"])
def test_s27_query_preparation_cache_revalidates_compatible_leaf_and_policy(search_job, change):
    store, origin = search_job
    first = prepare_query(store, origin, expansion_sender({"terms": ["residencia", "NIE"]}, []))
    store.save_analysis_result(origin["job_id"], {"items": []}, generation=origin["generation"])
    store.finish_job(origin["job_id"], generation=origin["generation"], status="completed", now=datetime.now(timezone.utc))
    def mutate(value):
        if change == "query":
            value["request"]["query"] += " Португалия"
        if change == "reasoning":
            value["settings"]["digest_ai"]["reasoning_effort"] = "low"
        if change == "prompt":
            value["settings"]["analysis_prompts"]["hash"] = "new-prompt-hash"
        if change == "model":
            value["settings"]["processing"]["model"] = "other-model"
        if change == "limits":
            value["settings"]["analysis"]["search"]["max_query_terms"] = 2
        if change == "disabled":
            value["settings"]["analysis"]["cache"]["enabled"] = False
    target = clone_search_job(store, origin, mutate=mutate)
    calls = []
    capability = {"model": target["request"]["settings"]["processing"]["model"], "context_window": 400000,
                  "max_output_tokens": 128000, "source": "fixture"}
    result = analysis.prepare_search_query(store, target["job_id"], generation=target["generation"], api_key="fixture",
        capabilities=capability, deadline=100, monotonic_func=lambda: 0, sleep_func=lambda _: None,
        urlopen_func=expansion_sender({"terms": ["residencia", "NIE"]}, calls))
    if change is None:
        assert result == first and calls == [] and store.get_job(target["job_id"])["llm_calls"] == 0
        store.conn.execute("DELETE FROM analysis_jobs WHERE job_id=?", (origin["job_id"],))
        assert prepare_query(store, target, lambda *a, **k: pytest.fail("Copied cache must be owned by target")) == result
    else:
        assert any(url.endswith("responses") for url, data in calls)


def test_s27_query_preparation_original_terms_are_bounded_without_truncating_question(search_job):
    store, job = search_job
    raw = " ".join(f"термин-{n}" for n in range(30)) + " без ВНЖ"
    job = query_request(store, job, query=raw)
    result = prepare_query(store, job, expansion_sender({"terms": []}, []))
    assert result["query"] == raw and result["normalized_query"].endswith("без ВНЖ")
    assert len(result["original_terms"]) == 24 and "original_terms_limited" in result["reasons"]


@pytest.mark.parametrize("key,value", [("max_llm_calls", 1), ("max_provider_requests", 1), ("max_total_tokens", 50)])
def test_s27_query_preparation_shared_budget_denial_keeps_original_and_cumulative_usage(search_job, key, value):
    store, job = search_job
    if key == "max_llm_calls":
        prior = store.plan_batch(job["job_id"], generation=job["generation"], step_key="fixture-prior-ai", kind="expand_query", payload={})
        store.reserve_provider_attempt(job["job_id"], prior["batch_id"], generation=job["generation"], attempt_id="fixture-prior",
            request_kind="inference", input_tokens=1, output_tokens=1)
        store.settle_provider_attempt(job["job_id"], prior["batch_id"], generation=job["generation"], attempt_id="fixture-prior",
            outcome="succeeded", usage={"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0})
    change_setting(store, job, "analysis", key, value)
    calls = []
    result = prepare_query(store, job, expansion_sender({"terms": ["NIE"]}, calls))
    saved = store.get_job(job["job_id"])
    assert result["expansion_status"] == "fallback" and result["original_terms"] and key in result["reasons"]
    assert saved["checkpoint"]["budget_stop"]["reason"] == key
    assert not any(url.endswith("responses") for url, data in calls)
    consumed = saved["used_tokens"] + saved["reserved_tokens"] if key == "max_total_tokens" else saved[key.replace("max_", "")]
    assert consumed <= value


def test_s27_query_preparation_oversized_original_is_rejected_before_provider(search_job):
    store, job = search_job
    query_request(store, job, query="я" * 2001)
    with pytest.raises(ValueError, match="invalid_search_query"):
        prepare_query(store, job, lambda *a, **k: pytest.fail("Over-limit query must not reach provider"))
    assert store.get_job(job["job_id"])["provider_requests"] == 0


def search_policy(store, job, **values):
    current = copy.deepcopy(store.get_job(job["job_id"])["request"]["settings"]["analysis"]["search"])
    current.update(values)
    change_setting(store, job, "analysis", "search", current)


def ready_search_index(store, job):
    from telegram_connector.tests.test_analysis_fts import queue_index, complete_index
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=datetime.now(timezone.utc))
    queued = queue_index(store.conn, batch=100)
    running = store.claim_job(queued["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))
    complete_index(store.conn, running)
    store.resume_job(job["job_id"], generation=job["generation"], phase="select", max_queued_jobs=8, now=datetime.now(timezone.utc))
    return store.claim_job(job["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))


def search_manifest(store, job, send):
    function = getattr(analysis, "build_search_manifest", None)
    assert callable(function), "Fast selection must freeze scoped candidate/context IDs through the existing manifest owner"
    return function(store, job["job_id"], generation=job["generation"], api_key="fixture",
        capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
        deadline=100, monotonic_func=lambda: 0, sleep_func=lambda _: None, urlopen_func=send)


@pytest.mark.parametrize("rebuild", [False, True])
def test_s28_fast_candidates_require_ready_index_before_expansion(search_job, rebuild):
    store, job = search_job
    if rebuild:
        job = ready_search_index(store, job)
        from telegram_connector.tests.test_analysis_fts import queue_index
        queue_index(store.conn, rebuild=True)
    with pytest.raises(ValueError, match="fts_not_ready|fts_rebuild_required"):
        search_manifest(store, job, lambda *a, **k: pytest.fail("Not-ready FTS must fail before paid expansion"))
    assert store.get_job(job["job_id"])["provider_requests"] == 0


def test_s28_fast_candidates_original_priority_and_scope_filters_precede_limit(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=0, max_candidates=1)
    seed(store, 22, [1], text="банк")
    seed(store, 22, [2], text="residencia residencia residencia")
    seed(store, 11, [9], text="банк", date="2026-10-09T10:00:00+00:00")
    seed(store, 11, [10], text="банк", date="2026-10-13T10:00:00+00:00")
    store.conn.execute("INSERT INTO channels VALUES (33,NULL,'outside','Outside','Channel','{}','now','now')")
    seed(store, 33, [1], text="банк банк банк")
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": ["residencia"]}, []))
    assert [(m["input"]["channel_id"], m["input"]["message_id"]) for m in messages(store, job)] == [(22, 1)]
    assert manifest["selection_mode"] == "candidates" and manifest["candidate_count"] == 1
    assert manifest["partial"] and "max_candidates" in manifest["reasons"]
    assert manifest["stored_in_window"] == 2 and manifest["known_skipped"] == 1


def test_s28_fast_candidates_alias_finds_lexical_miss_with_real_fts(search_job):
    store, job = search_job
    query_request(store, job, query="ВНЖ")
    search_policy(store, job, context_messages=0)
    seed(store, 22, [1], text="Tarjeta de residencia: documentos y сроки.")
    seed(store, 11, [1], text="Только культурные мероприятия.")
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": ["tarjeta de residencia"]}, []))
    assert manifest["candidate_count"] == 1 and manifest["selected"] == 1
    assert [(m["input"]["channel_id"], m["input"]["message_id"]) for m in messages(store, job)] == [(22, 1)]
    assert manifest["known_skipped"] == 1 and not manifest["partial"]


def test_s28_fast_candidates_model_fts_syntax_is_a_literal_phrase(search_job):
    store, job = search_job
    query_request(store, job, query="needle")
    search_policy(store, job, context_messages=0)
    seed(store, 22, [1], text="needle")
    seed(store, 22, [2], text="secret")
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": ['needle" OR "secret']}, []))
    assert manifest["selected"] == 1
    assert [m["input"]["message_id"] for m in messages(store, job)] == [1]
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_s28_fast_candidates_context_prefers_same_peer_reply_and_deduplicates_neighbors(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=2)
    for peer, number, day, text in [(22, 1, "10", "Родитель обсуждения"), (22, 2, "11", "банк"),
        (22, 3, "12", "Отдельный ответ"), (11, 1, "10", "Чужой родитель"), (11, 3, "12", "Другой канал")]:
        seed(store, peer, [number], text=text, date=f"2026-10-{day}T10:00:00+00:00")
    store.conn.execute("UPDATE messages SET raw_json=? WHERE channel_id=22 AND message_id=2", (json.dumps({"reply_to": {"reply_to_msg_id": 1}}),))
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": []}, []))
    refs = [(m["input"]["channel_id"], m["input"]["message_id"]) for m in messages(store, job)]
    assert refs == [(22, 1), (22, 2), (22, 3)] and len(set(refs)) == len(refs)
    assert manifest["candidate_count"] == 1 and manifest["context_messages"] == 2 and manifest["selected"] == 3
    assert not manifest["partial"]


def test_s28_fast_candidates_total_cap_keeps_candidates_before_context(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=20)
    change_setting(store, job, "analysis", "max_messages", 2)
    seed(store, 22, [1, 5], text="банк")
    seed(store, 22, [2, 3, 4], text="Контекст обсуждения")
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": []}, []))
    assert [m["input"]["message_id"] for m in messages(store, job)] == [1, 5]
    assert manifest["selected"] == 2 and manifest["context_messages"] == 0
    assert manifest["partial"] and "max_messages" in manifest["reasons"]


def test_s28_fast_candidates_zero_results_do_not_scan_unmatched_history(search_job):
    store, job = search_job
    query_request(store, job, query="несуществующееслово")
    seed(store, 22, [1, 2], text="Другая тема")
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": []}, []))
    assert manifest["state"] == "complete" and manifest["selection_mode"] == "candidates"
    assert manifest["candidate_count"] == manifest["selected"] == 0
    assert manifest["known_skipped"] == 2 and messages(store, job) == []
    assert not manifest["partial"]


@pytest.mark.parametrize("mode,selected", [("fast", 2), ("full", 3)])
def test_s28_fast_candidates_engine_dispatches_correct_selection_and_expansion(search_job, runtime, mode, selected):
    store, job = search_job
    query_request(store, job, query="ВНЖ", mode=mode)
    search_policy(store, job, context_messages=0)
    seed(store, 22, [1], text="ВНЖ: исходный термин")
    seed(store, 22, [2], text="Tarjeta de residencia: другой язык")
    seed(store, 22, [3], text="Другая тема")
    job = ready_search_index(store, job)
    calls, sent = [], []
    provider = engine_provider(store, job, [])
    def send(req, timeout):
        data = json.loads(req.data)
        if not req.full_url.endswith("input_tokens"):
            name = data["text"]["format"]["name"]
            calls.append(name)
            if name == "telegram_query_terms_v1":
                return Response(model_response({"terms": ["residencia"]}))
        return provider(req, timeout)
    status = analysis.execute_analysis_job(store, job, runtime=runtime, client=EngineHistoryClient(store, {22: [], 11: []}),
        api_key="fixture", token="fixture", access_provider=engine_access,
        capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
        urlopen_func=send, send_func=lambda token, chat, text, parse_mode, **kw:
            sent.append(text) or {"message_id": 1, "chat": {"id": 100}})
    saved = store.get_job(job["job_id"])
    assert status == "completed" and sent and saved["result"]["coverage"]["selected_messages"] == selected
    assert saved["result"]["coverage"]["selection_mode"] == ("candidates" if mode == "fast" else "all_messages")
    assert calls.count("telegram_query_terms_v1") == (1 if mode == "fast" else 0)


def test_s28_fast_candidates_not_ready_engine_fails_before_secrets_and_shows_actionable_status(search_job, tmp_path, monkeypatch):
    store, job = search_job
    monkeypatch.setattr(analysis.bridge, "resolve_secret_value", lambda *a: pytest.fail("Check index before key resolution"))
    def executor(store, current):
        return analysis.execute_analysis_job(store, current, access_provider=engine_access)
    code = analysis.run_analysis(store, tmp_path / "worker.sqlite3", job_id=job["job_id"], generation=job["generation"], executor=executor)
    saved = store.get_job(job["job_id"])
    assert code != 0 and saved["status"] == "failed" and saved["provider_requests"] == 0
    assert saved["checkpoint"]["last_stop"]["reason"] == "fts_not_ready"
    text = analysis.bridge.format_analysis_status(analysis.analysis_status_view(saved))
    assert "Индекс" in text and "--mode full" in text


@pytest.mark.parametrize("page", [1, 2])
def test_s28_fast_candidates_resume_keeps_frozen_pool_and_checkpoint_cursor(search_job, monkeypatch, page):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=4)
    seed(store, 22, [1, 5], text="банк")
    seed(store, 22, [2, 3, 4], text="Ответ обсуждения")
    job = ready_search_index(store, job)
    original = store.commit_manifest_page
    stopped = []
    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["manifest"]["page"] == page and not stopped:
            stopped.append(True)
            raise InterruptedError("fixture after committed selection page")
        return result
    monkeypatch.setattr(store, "commit_manifest_page", interrupt)
    with pytest.raises(InterruptedError):
        search_manifest(store, job, expansion_sender({"terms": []}, []))
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=datetime.now(timezone.utc))
    seed(store, 22, [6], text="банк: поздняя вставка")
    from telegram_connector.tests.test_analysis_manifest import resume
    continued = resume(store, job)
    state = search_manifest(store, continued, lambda *a, **k: pytest.fail("Frozen query preparation must not repeat"))
    refs = [m["input"]["message_id"] for m in messages(store, continued)]
    assert state["candidate_count"] == 2 and len(refs) == len(set(refs))
    assert 1 in refs and 5 in refs and 6 not in refs


def test_s28_fast_candidates_no_false_context_truncation_when_all_inputs_were_selected(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=1)
    seed(store, 22, range(1, 6), text="банк")
    job = ready_search_index(store, job)
    manifest = search_manifest(store, job, expansion_sender({"terms": []}, []))
    assert manifest["selected"] == 5 and manifest["known_skipped"] == 0
    assert not manifest["partial"] and "context_limited" not in manifest["reasons"]


@pytest.mark.parametrize("reason", ["private-query-secret", {}, [], None])
def test_s28_fast_candidates_status_error_is_whitelisted_and_handles_other_checkpoint_values(search_job, reason):
    store, job = search_job
    checkpoint = store.get_job(job["job_id"])["checkpoint"]
    checkpoint["last_stop"] = {"reason": reason}
    store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    view = analysis.analysis_status_view(store.get_job(job["job_id"]))
    assert "error" not in view and "private-query-secret" not in analysis.bridge.format_analysis_status(view)


def test_s28_fast_candidates_invalid_expansion_still_selects_original_matches(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=0)
    seed(store, 22, [1], text="банк: условия")
    seed(store, 22, [2], text="Другое обсуждение")
    job = ready_search_index(store, job)
    state = search_manifest(store, job, expansion_sender({"answer": "Неподдержанный ответ"}, []))
    assert [m["input"]["message_id"] for m in messages(store, job)] == [1]
    assert state["partial"] and "query_expansion_invalid_schema" in state["reasons"]


def test_s28_fast_candidates_ocr_only_hit_reaches_the_same_manifest_reader(search_job):
    store, job = search_job
    query_request(store, job, query="residencia")
    search_policy(store, job, context_messages=0)
    seed(store, 22, [1], text="")
    store.conn.execute("""INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,ocr_status,ocr_text,created_at)
        VALUES(22,1,0,'photo','done','residencia: документы','now')""")
    job = ready_search_index(store, job)
    state = search_manifest(store, job, expansion_sender({"terms": []}, []))
    assert state["candidate_count"] == 1
    source = messages(store, job)[0]
    assert source["message"]["media"][0]["ocr_text"] == "residencia: документы"


def test_s28_fast_candidates_source_edit_repairs_only_fixed_inputs_and_context_counts(search_job):
    from telegram_connector.tests.test_analysis_manifest import resume
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=2)
    seed(store, 22, [2], text="банк")
    seed(store, 22, [1, 3], text="Ответ")
    job = ready_search_index(store, job)
    search_manifest(store, job, expansion_sender({"terms": []}, []))
    store.conn.execute("DELETE FROM messages WHERE channel_id=22 AND message_id=3")
    with pytest.raises(ValueError, match="input_changed"):
        messages(store, job)
    continued = resume(store, job)
    state = search_manifest(store, continued, lambda *a, **k: pytest.fail("Repair must not rediscover candidates or expand"))
    assert [m["input"]["message_id"] for m in messages(store, continued)] == [1, 2]
    assert state["selected_candidates"] == 1 and state["context_messages"] == 1
    assert state["partial"] and state["known_skipped"] == 1


def test_s28_fast_candidates_sql_uses_fts_rowid_lookup_and_indexed_context_ranges(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=1)
    seed(store, 22, [2], text="банк")
    seed(store, 22, [1, 3], text="Ответ")
    job = ready_search_index(store, job)
    statements = []
    store.conn.set_trace_callback(statements.append)
    search_manifest(store, job, expansion_sender({"terms": []}, []))
    store.conn.set_trace_callback(None)
    fts = next(sql for sql in statements if "FROM messages_fts" in sql and "ORDER BY messages_fts.rank" in sql)
    neighbor = next(sql for sql in statements if "AND (date_utc,message_id)" in sql and "FROM messages" in sql)
    fts_plan = [row[3] for row in store.conn.execute("EXPLAIN QUERY PLAN " + fts)]
    neighbor_plan = [row[3] for row in store.conn.execute("EXPLAIN QUERY PLAN " + neighbor)]
    assert any("VIRTUAL TABLE INDEX" in line for line in fts_plan)
    assert any("INTEGER PRIMARY KEY" in line for line in fts_plan)
    assert any("idx_messages_channel_date" in line for line in neighbor_plan)


def test_s28_fast_candidates_metadata_and_pages_obey_real_shared_byte_cap(search_job):
    store, job = search_job
    query_request(store, job, query="банк")
    search_policy(store, job, context_messages=0)
    seed(store, 22, range(1, 1001), text="банк")
    job = ready_search_index(store, job)
    prepare_query(store, job, expansion_sender({"terms": []}, []))
    margin = 4096
    assert len(analysis.canonical_json([[22, number] for number in range(1, 1001)]).encode()) > margin
    cap = store.storage_usage(job["job_id"])["allocated_bytes"] + margin
    change_setting(store, job, "analysis", "max_intermediate_bytes", cap)
    state = search_manifest(store, job, lambda *a, **k: pytest.fail("Completed preparation must be reused"))
    assert state["state"] == "complete" and state["partial"]
    assert "candidate_metadata_limited" in state["reasons"]
    assert state["candidate_count"] < 1000 and store.storage_usage(job["job_id"])["allocated_bytes"] <= cap


@pytest.mark.parametrize("unavailable", [False, True])
def test_s28_fast_candidates_empty_available_scope_skips_useless_expansion(search_job, unavailable):
    store, job = search_job
    job = ready_search_index(store, job)
    if unavailable:
        checkpoint = store.get_job(job["job_id"])["checkpoint"]
        for source in checkpoint["history"]["sources"].values():
            source["state"] = "failed"
        store.save_checkpoint(job["job_id"], checkpoint, generation=job["generation"], max_bytes=10000)
    state = search_manifest(store, job, lambda *a, **k: pytest.fail("No available input can benefit from expansion"))
    assert state["state"] == "complete" and state["selected"] == 0
    assert state["partial"] is unavailable and store.get_job(job["job_id"])["provider_requests"] == 0


@pytest.fixture
def search_answer(search_job, runtime, tmp_path, monkeypatch):
    store, job = search_job
    corpus = {(22, 1): "España: Residencia por pasaporte; tasa 1250 EUR, plazo 30 días.",
        (22, 2): "Concierto el viernes.", (22, 3): "Experiencia personal: aceptaron NIE; cita obligatoria.",
        (11, 1): "Para ese trámite pidieron 1500 EUR; estudiantes exentos.",
        (11, 2): "Fiesta de vecinos.", (11, 3): "Rectificación: 1250 EUR, no 1500 EUR; plazo 30 días."}
    for (peer, number), text in corpus.items():
        seed(store, peer, [number], text=text)
    root = tmp_path / "repo" / "telegram_connector"
    root.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(root))
    def run(*, mode="full", detail="normal", query="  Условия\u00a0ВНЖ в Espan\u0303a?  ", empty=False, max_calls=None):
        profile = store.get_job(job["job_id"])["request"]["settings"]["analysis"]["output"]["profiles"][detail]
        current = query_request(store, job, query=query, mode=mode, detail=detail, limit=profile["default_limit"])
        change_setting(store, job, "digest_ai", "messages_per_ai_pass", 2)
        change_setting(store, job, "analysis", "overlap_messages", 0)
        change_setting(store, job, "analysis", "merge_fan_in", 2)
        if max_calls is not None:
            change_setting(store, job, "analysis", "max_llm_calls", max_calls)
        search_policy(store, job, context_messages=20)
        if mode == "fast":
            current = ready_search_index(store, current)
        calls, sent = [], []
        base = engine_provider(store, current, [])
        def send(req, timeout):
            data = json.loads(req.data)
            if req.full_url.endswith("input_tokens"):
                return Response({"input_tokens": 100})
            calls.append(data)
            kind = data["text"]["format"]["name"]
            if kind == "telegram_query_terms_v1":
                return Response(model_response({"terms": []}))
            if kind == "telegram_evidence_v1":
                fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
                records = []
                for fragment in fragments:
                    message = fragment["message"]
                    if empty or message["message_id"] == 2:
                        continue
                    value = document({"messages": [fragment]})["records"][0]
                    value.update(claim=message["text"], entities=["Residencia"], topic_key="documentos",
                        conditions=["cita obligatoria"] if "cita obligatoria" in message["text"] else [],
                        exceptions=["estudiantes exentos"] if "estudiantes exentos" in message["text"] else [],
                        assertion_kind="experience" if "Experiencia personal" in message["text"] else "reported")
                    records.append(value)
                return Response(model_response({"records": records, "saturated": False}))
            if kind == "telegram_relations_v1":
                pairs = json.loads(data["input"].split("Кандидаты: ", 1)[1])
                by_id = {r["record_id"]: r["payload"] for r in pairs["records"]}
                decisions = []
                for left, right in pairs["pairs"]:
                    a, b = by_id[left]["claim"], by_id[right]["claim"]
                    contrary = ("1250 EUR" in a and "1500 EUR" in b) or ("1500 EUR" in a and "1250 EUR" in b)
                    decisions.append({"left_id": left, "right_id": right, "relation": "contradicts" if contrary else "complements",
                        "reason": "Fixture-labelled compatibility of conditions"})
                return Response(model_response({"decisions": decisions, "saturated": False}))
            if kind == "telegram_synthesis_v1":
                evidence = json.loads(data["input"].split("Опорные записи: ", 1)[1])
                claims = [r["payload"]["claim"] for r in evidence["records"]]
                return Response(model_response({"summary": "В обсуждениях приведены условия и противоречащие суммы.",
                    "items": [{"title": "Условия оформления", "text": " ".join(claims), "conditions": [], "exceptions": [],
                        "contradictions": [], "evidence_ids": [r["record_id"] for r in evidence["records"]]}],
                    "limitations": [], "insufficient_information": False, "saturated": False}))
            return base(req, timeout)
        status = analysis.execute_analysis_job(store, current, runtime=runtime, client=EngineHistoryClient(store, {22: [], 11: []}),
            api_key="fixture", token="fixture", access_provider=engine_access,
            capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
            urlopen_func=send, send_func=lambda token, chat, text, parse_mode, **kw:
                sent.append(text) or {"message_id": len(sent), "chat": {"id": 100}})
        return store.get_job(current["job_id"]), status, calls, sent
    return store, job, run


def test_s29_full_search_answer_all_batches_channels_and_lexical_misses(search_answer):
    store, job, run = search_answer
    saved, status, calls, sent = run(query="ВНЖ")
    assert status == "completed" and saved["result"]["coverage"]["processed_messages"] == 6
    item = saved["result"]["items"][0]
    assert all(text in item["text"] for text in ("1250 EUR", "1500 EUR", "30 días", "aceptaron NIE", "Rectificación"))
    assert "cita obligatoria" in item["conditions"] and "estudiantes exentos" in item["exceptions"]
    assert item["contradictions"] and len(item["evidence_ids"]) == 4
    assert len([data for data in calls if data["text"]["format"]["name"] == "telegram_evidence_v1"]) >= 3
    assert not any(data["text"]["format"]["name"] == "telegram_query_terms_v1" for data in calls)
    sources = [store.leaf_evidence(saved["job_id"], key)["payload"] for key in item["evidence_ids"]]
    assert {ref["channel_id"] for record in sources for ref in record["source_refs"]} == {11, 22}
    assert all("внж" not in r["claim"].casefold() for r in sources)


def test_s29_full_search_answer_normalizes_each_ai_stage_but_keeps_original_question(search_answer):
    store, job, run = search_answer
    raw = "  Условия\u00a0ВНЖ в Espan\u0303a?  "
    saved, status, calls, sent = run(query=raw)
    assert saved["result"]["query"] == raw
    assert {data["text"]["format"]["name"] for data in calls} >= {
        "telegram_evidence_v1", "telegram_relations_v1", "telegram_merge_v1", "telegram_synthesis_v1"}
    assert all("Условия ВНЖ в España?" in data["input"] for data in calls)


@pytest.mark.parametrize("detail", ["short", "normal", "full"])
def test_s29_full_search_answer_presents_query_brief_details_conflicts_and_sources(search_answer, detail):
    store, job, run = search_answer
    saved, status, calls, sent = run(detail=detail)
    text = analysis.resolve_analysis_artifact(saved)[0].read_text() if detail == "full" else "\n".join(sent)
    assert all(label in text for label in ("Запрос", "Краткий ответ", "Подробности", "Противоречия", "Источники"))
    assert "1250 EUR" in text and "1500 EUR" in text and "https://t.me/" in text


def test_s29_full_search_answer_zero_fast_candidates_is_not_corpus_absence(search_answer):
    store, job, run = search_answer
    saved, status, calls, sent = run(mode="fast", query="ВНЖ")
    coverage = saved["result"]["coverage"]
    assert coverage["selection_mode"] == "candidates" and coverage["selected_messages"] == 0
    assert coverage["candidate_messages"] == coverage["context_messages"] == 0
    assert "В предварительной выборке" in "\n".join(sent)
    assert not any(data["text"]["format"]["name"] == "telegram_evidence_v1" for data in calls)


def test_s29_full_search_answer_candidate_and_context_counts_are_distinct(search_answer):
    store, job, run = search_answer
    saved, status, calls, sent = run(mode="fast", query="Residencia")
    coverage = saved["result"]["coverage"]
    assert coverage["candidate_messages"] == 1 and coverage["context_messages"] == 2
    assert coverage["selected_messages"] == coverage["processed_messages"] == 3
    assert "Кандидатов: 1; сообщений контекста: 2." in "\n".join(sent)


def test_s29_full_search_answer_full_without_evidence_reports_insufficiency(search_answer):
    store, job, run = search_answer
    saved, status, calls, sent = run(empty=True)
    assert status == "completed" and not saved["result"]["partial"]
    assert saved["result"]["coverage"]["processed_messages"] == 6
    assert saved["result"]["insufficient_information"] and saved["result"]["items"] == []
    assert "В выбранной истории недостаточно сведений" in "\n".join(sent)
    assert not any(data["text"]["format"]["name"] == "telegram_synthesis_v1" for data in calls)


def test_s29_full_search_answer_full_budget_stop_is_not_empty_complete(search_answer):
    store, job, run = search_answer
    saved, status, calls, sent = run(max_calls=1)
    assert status == "partial" and saved["result"]["partial"] and saved["llm_calls"] <= 1
    assert len(calls) == saved["llm_calls"]
    assert "max_llm_calls" in saved["result"]["reasons"]
    assert "Полный подтверждённый ответ не сформирован" in "\n".join(sent)


def test_s29_full_search_answer_unsupported_free_summary_is_not_an_answer(search_job):
    store, job = search_job
    value = analysis_pipeline._synthesis_result(job, {"records": [], "relations": [], "reasons": [], "partial": False,
        "coverage": {"selection_mode": "all_messages", "selected_messages": 0}},
        {"summary": "Все банки гарантированно принимают без документов.", "items": [], "limitations": [],
         "insufficient_information": False, "saturated": False})
    assert value["summary"] == "" and value["insufficient_information"]


def test_s29_full_search_answer_requires_brief_when_supported_details_exist(search_job):
    store, job = search_job
    evidence = {"records": [{"record_id": "supported", "payload": {"claim": "1250 EUR", "conditions": [], "exceptions": []}}],
        "relations": [], "reasons": [], "partial": False, "coverage": {"selection_mode": "all_messages"}}
    with pytest.raises(ValueError, match="search_answer_missing_summary"):
        analysis_pipeline._synthesis_result(job, evidence, {"summary": "", "items": [{"title": "Условия", "text": "1250 EUR",
            "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": ["supported"]}],
            "limitations": [], "insufficient_information": False, "saturated": False})


@pytest.fixture
def search_journey(listener, config, access, runtime, tmp_path, monkeypatch):
    """Real bridge/supervisor/worker boundaries; external history and AI are fixtures."""
    config["digest_ai"]["messages_per_ai_pass"] = 2
    config["analysis"] = {"overlap_messages": 0}
    root = tmp_path / "repo" / "telegram_connector"
    root.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(root))
    path = Path(config["paths"]["history_db"])
    conn = common_sqlite.connect_sqlite(path)
    try:
        queued = queue_index(conn)
        running = AnalysisStore(conn).claim_job(queued["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))
        complete_index(conn, running)
    finally:
        conn.close()
    day = datetime.now(timezone.utc) - timedelta(days=1)
    corpus = {22: posts([3, 2, 1]), 11: posts([2, 1])}
    for peer, values in corpus.items():
        for message in values:
            message.date = day.replace(hour=10, minute=0, second=0, microsecond=0)
            message.message = f"NIE: этап {message.id}, срок {message.id * 10} дней; сумма {1250 if peer == 22 else 1500} EUR."
    state = {"calls": [], "sent": [], "runs": [], "client": None}
    original = analysis.execute_analysis_job
    def engine(store, job):
        client = EngineHistoryClient(store, corpus)
        state["client"] = client
        base = engine_provider(store, job, [])
        def send(req, timeout):
            data = json.loads(req.data)
            if req.full_url.endswith("input_tokens"):
                return base(req, timeout)
            kind = data["text"]["format"]["name"]
            state["calls"].append((job["job_id"], data))
            if state.get("before_inference"):
                state["before_inference"](job, data)
            if kind == "telegram_query_terms_v1":
                return Response(model_response({"terms": ["NIE"]}))
            if kind == "telegram_evidence_v1" and state.pop("refuse_batch", False):
                return Response({"status": "completed", "output": [{"type": "message", "content": [
                    {"type": "refusal", "refusal": "fixture"}]}]})
            return base(req, timeout)
        def deliver(token, chat, text, parse_mode, **kw):
            state["sent"].append((job["job_id"], text, parse_mode))
            if state.get("unknown_send"):
                from telegram_shared.errors import TelegramApiError
                raise TelegramApiError("fixture lost receipt")
            return {"message_id": len(state["sent"]), "chat": {"id": 100}}
        return original(store, job, runtime=runtime, client=client, api_key="fixture", token="fixture",
            capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
            access_provider=lambda: access, urlopen_func=send, send_func=deliver)
    monkeypatch.setattr(analysis, "execute_analysis_job", engine)
    def launch(job):
        code = analysis.main(["--db", str(path), "--job-id", job["job_id"], "--generation", str(job["generation"]), "--worker-inner"])
        state["runs"].append(code)
        process = FixtureProcess()
        process.returncode = code
        return process
    scheduler = supervisor(config, access, launch=launch)
    def tick():
        conn = common_sqlite.connect_sqlite(path)
        try:
            scheduler._tick(AnalysisStore(conn))
            scheduler._tick(AnalysisStore(conn))
        finally:
            conn.close()
    def dispatch(text, number=42):
        listener["events"] = [update(number, text)]
        assert bridge.cmd_listen(listener["args"]) == 0
    def run(text='/search @two,@one since=yesterday until=yesterday "Документы"', number=42):
        dispatch(text, number)
        tick()
        return next(job for job in listener["jobs"]() if job["admission_key"].endswith(":" + str(number)))
    state.update(run=run, dispatch=dispatch, tick=tick, listener=listener)
    return state


@pytest.mark.parametrize("mode", ["fast", "full"])
@pytest.mark.parametrize("detail,tokens", [("short", 1000), ("normal", 3000), ("full", 12000)])
def test_s30_search_journey_mode_and_detail_are_independent(search_journey, mode, detail, tokens):
    state = search_journey
    job = state["run"](f'/search @two,@one since=yesterday until=yesterday "Документы" --mode {mode} --detail {detail}')
    assert job["status"] == "completed" and job["delivery"]["outcome"] == "confirmed"
    request = job["request"]["request"]
    assert request["query"] == "Документы" and request["mode"] == mode and request["detail"] == detail
    assert job["result"]["coverage"]["processed_messages"] == 5
    assert job["result"]["coverage"]["selection_mode"] == ("candidates" if mode == "fast" else "all_messages")
    calls = [data for identity, data in state["calls"] if identity == job["job_id"]]
    assert any(data["text"]["format"]["name"] == "telegram_query_terms_v1" for data in calls) == (mode == "fast")
    assert next(data for data in calls if data["text"]["format"]["name"] == "telegram_synthesis_v1")["max_output_tokens"] == tokens
    sent = [text for identity, text, parse_mode in state["sent"] if identity == job["job_id"]]
    if detail == "full":
        target, relative = analysis.resolve_analysis_artifact(job)
        assert sent == [relative]
        text = target.read_text()
    else:
        text = "\n".join(sent)
        assert all(parse_mode == "HTML" for identity, text, parse_mode in state["sent"])
    assert all(value in text for value in ("Документы", "Краткий ответ", "1250", "1500", "https://t.me/peer22/", "https://t.me/peer11/"))
    assert state["runs"] == [0]


def test_s30_search_journey_defaults_quotes_and_mandatory_channels(search_journey):
    state = search_journey
    state["dispatch"]('/search since=yesterday until=yesterday "Какие документы?"')
    assert not any(job["kind"] == "analysis" for job in state["listener"]["jobs"]())
    assert not state["runs"] and not state["calls"]
    job = state["run"]('/search @two since=yesterday until=yesterday "Какие документы без ВНЖ?"', number=43)
    request = job["request"]["request"]
    assert request["query"] == "Какие документы без ВНЖ?" and request["mode"] == "fast" and request["detail"] == "normal"
    assert job["status"] == "completed"


def test_s30_search_journey_query_scoped_cache_and_history_reuse(search_journey):
    state = search_journey
    first = state["run"]()
    second = state["run"](number=43)
    assert second["status"] == "completed" and second["downloaded_messages"] == 0 and not state["client"].calls
    repeated = [data["text"]["format"]["name"] for identity, data in state["calls"] if identity == second["job_id"]]
    assert "telegram_query_terms_v1" not in repeated and "telegram_evidence_v1" not in repeated
    assert second["llm_calls"] < first["llm_calls"]
    third = state["run"]('/search @two,@one since=yesterday until=yesterday "Сроки документов"', number=44)
    changed = [data for identity, data in state["calls"] if identity == third["job_id"]]
    assert third["status"] == "completed" and third["downloaded_messages"] == 0
    assert {data["text"]["format"]["name"] for data in changed} >= {"telegram_query_terms_v1", "telegram_evidence_v1"}
    assert all("Сроки документов" in data["input"] for data in changed)


def test_s30_search_journey_failed_batch_keeps_other_sources_and_partial(search_journey):
    state = search_journey
    state["refuse_batch"] = True
    job = state["run"]()
    assert job["status"] == "partial" and job["result"]["partial"]
    coverage = job["result"]["coverage"]
    assert 0 < coverage["processed_messages"] < coverage["selected_messages"] == 5
    text = "\n".join(text for identity, text, mode in state["sent"])
    assert "Частичный" in text and "https://t.me/" in text


def test_s30_search_journey_unknown_send_blocks_resume_and_replay(search_journey):
    state = search_journey
    state["unknown_send"] = True
    first = state["run"]()
    assert first["delivery"]["outcome"] == "unknown" and first["result"] is not None
    calls, sends, runs = len(state["calls"]), len(state["sent"]), len(state["runs"])
    state["dispatch"]('/resume ' + first["job_id"], number=43)
    state["tick"]()
    state["run"]()  # Replayed update is the same admission, not a new search.
    assert (len(state["calls"]), len(state["sent"]), len(state["runs"])) == (calls, sends, runs)
    assert state["listener"]["jobs"]()[-1]["delivery"]["outcome"] == "unknown"


def test_s30_search_journey_status_cancel_during_inference(search_journey):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    state = search_journey
    entered, release = Event(), Event()
    def hold(job, data):
        entered.set()
        assert release.wait(5), "Fixture inference must be released"
    state["before_inference"] = hold
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(state["run"])
        try:
            assert entered.wait(5) and future.running()
            running = next(job for job in state["listener"]["jobs"]() if job["command"] == "search")
            state["dispatch"]('/status ' + running["job_id"], number=43)
            state["dispatch"]('/cancel ' + running["job_id"], number=44)
            current = next(job for job in state["listener"]["jobs"]() if job["job_id"] == running["job_id"])
            assert current["status"] == "running" and current["cancel_requested"]
            assert any("select" in text for chat, text in state["listener"]["sent"])
        finally:
            release.set()
        stopped = future.result(timeout=5)
    assert stopped["status"] == "cancelled" and not state["sent"]


def test_s30_search_journey_saved_result_resume_avoids_inference(search_journey, monkeypatch):
    state = search_journey
    original = AnalysisStore.set_analysis_phase
    stopped = []
    def interrupt(self, *args, **kwargs):
        if kwargs["phase"] == "verify" and not stopped:
            stopped.append(True)
            raise InterruptedError("fixture pre-delivery stop")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(AnalysisStore, "set_analysis_phase", interrupt)
    first = state["run"]()
    assert first["status"] == "interrupted" and first["result"] is not None and not state["sent"]
    calls = len(state["calls"])
    state["dispatch"]('/resume ' + first["job_id"], number=43)
    state["tick"]()
    resumed = next(job for job in state["listener"]["jobs"]() if job["job_id"] == first["job_id"])
    assert resumed["status"] == "completed" and resumed["delivery"]["outcome"] == "confirmed"
    assert resumed["generation"] > first["generation"] and resumed["used_tokens"] == first["used_tokens"]
    assert len(state["calls"]) == calls and state["sent"]
