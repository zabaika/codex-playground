"""QA fixtures verify application contracts; semantic quality needs model evals."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, change_setting
from telegram_connector.tests.test_analysis_batching import batches
from telegram_connector.tests.test_analysis_evidence import document, extract, model_response
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_relations import records_fixture, link, link_sender
from telegram_connector.tests.test_analysis_merge import merge, merge_sender
from telegram_connector.tests.test_analysis_synthesis import synthesize
from telegram_connector import telegram_history_client as history
from telegram_connector.tests.test_analysis_history import runtime, posts
from telegram_connector.tests.test_analysis_lifecycle import config, access, listener, update, supervisor, FixtureProcess
from telegram_connector.tests.test_analysis_engine import EngineHistoryClient, engine_provider
from telegram_connector.analysis_store import AnalysisStore
from telegram_connector import telegram_bridge as bridge
from common import sqlite as common_sqlite


@pytest.fixture
def qa_job(manifest_job):
    store, job = manifest_job
    current = copy.deepcopy(job["request"])
    current["request"]["command"] = "qa"
    store.conn.execute("UPDATE analysis_jobs SET command='qa',request_json=? WHERE job_id=?",
        (analysis.canonical_json(current), job["job_id"]))
    return store, store.get_job(job["job_id"])


@pytest.mark.parametrize("question_type", ["question", "problem"])
def test_s36_qa_semantic_candidates_do_not_require_matching_entity_or_topic(qa_job, question_type):
    store, job = qa_job
    records = records_fixture(store, job, [
        {"record_type": question_type, "claim": "Какие документы нужны для счёта в Испании без ВНЖ?",
         "entities": ["счёт в Испании", "ВНЖ"], "topic_key": "documents_for_account"},
        {"record_type": "answer", "claim": "Abrí la cuenta con pasaporte, sin permiso de residencia.",
         "entities": ["España", "pasaporte"], "topic_key": "apertura_de_cuenta"},
        {"record_type": "claim", "claim": "Будет выставка фотографий.", "entities": ["фотографии"], "topic_key": "culture"}])
    pairs = list(analysis.iter_relation_candidates(store, job["job_id"], generation=job["generation"]))
    assert len(pairs) == 1
    assert {pairs[0]["left"]["record_id"], pairs[0]["right"]["record_id"]} == {r["record_id"] for r in records[:2]}
    # Candidate generation proposes a pair; only the existing LLM link stage
    # may decide whether it actually answers the question.
    assert not store.get_job(job["job_id"])["checkpoint"]["relations"]["limited"]


def test_s36_qa_semantic_candidate_expansion_respects_existing_pair_caps(qa_job):
    store, job = qa_job
    records_fixture(store, job, [{"record_type": kind, "claim": f"Сведение {i}",
        "entities": [f"сущность{i}"], "topic_key": f"тема{i}"}
        for i, kind in enumerate(["question", "problem", "answer", "answer"])])
    change_setting(store, job, "analysis", "max_relation_pairs", 1)
    pairs = list(analysis.iter_relation_candidates(store, job["job_id"], generation=job["generation"]))
    assert len(pairs) == 1
    assert store.get_job(job["job_id"])["checkpoint"]["relations"]["limited"] is True


QA_CASES = {
    1: ("Какие документы нужны в банк Uno?", [("question", "reported")]),
    2: ("В Uno принёс паспорт, заплатил 1250 EUR; ответ за 30 дней, только по записи.", [("answer", "experience")]),
    3: ("Не принимали документы без NIE. Записался в отделение и подал паспорт; приняли за 2 дня.",
        [("problem", "experience"), ("answer", "experience")]),
    4: ("По-моему, банк Uno ужасный.", [("opinion", "opinion")]),
    5: ("Банк Uno открыл новый офис.", [("event", "reported")]),
    6: ("Какая комиссия за перевод в Uno?", [("question", "reported")]),
    7: ("Отказали в выдаче справки; решение пока не нашёл.", [("problem", "experience")]),
}


@pytest.fixture
def qa_records(qa_job):
    store, job = qa_job
    for number, (text, labels) in QA_CASES.items():
        seed(store, 22, [number], text=text)
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 2)
    change_setting(store, job, "analysis", "overlap_messages", 0)
    build(store, job)
    calls = []
    def send(req, timeout):
        data = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(data)
        fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
        values = []
        for fragment in fragments:
            number = fragment["message"]["message_id"]
            for kind, assertion in QA_CASES[number][1]:
                value = document({"messages": [fragment]})["records"][0]
                value.update(record_type=kind, claim=fragment["message"]["text"], entities=["Uno"],
                    topic_key="документы" if number < 4 else str(number), assertion_kind=assertion,
                    conditions=["только по записи"] if number == 2 else [], exceptions=[])
                values.append(value)
        return Response(model_response({"records": values, "saturated": False}))
    for batch in batches(store, job):
        extract(store, job, batch, send)
    saved = list(store.iter_leaf_evidence(job["job_id"], page_size=2))
    return store, store.get_job(job["job_id"]), saved, calls


def evidence(records):
    return {"records": records, "relations": [], "reasons": [], "partial": False,
        "coverage": {"selection_mode": "all_messages"}}


def final_document(ids, *, text="Принесите паспорт; 1250 EUR, срок 30 дней."):
    return {"summary": "Практические ответы участников", "items": [{"title": "Как подать документы?", "text": text,
        "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": ids}],
        "limitations": [], "insufficient_information": False, "saturated": False}


def test_s31_qa_extraction_preserves_unmatched_questions_and_standalone_solutions(qa_records):
    store, job, records, calls = qa_records
    assert len(calls) == 4 and len(records) == 8
    assert {record["record_type"] for record in records} == {"question", "answer", "problem", "event", "opinion"}
    by_message = {}
    for record in records:
        source = record["payload"]["source_refs"][0]
        by_message.setdefault(source["message_id"], []).append(record)
        assert record["payload"]["related_message_refs"] == []
    assert by_message[1][0]["record_type"] == by_message[6][0]["record_type"] == "question"
    assert {record["record_type"] for record in by_message[3]} == {"problem", "answer"}
    answer = by_message[2][0]["payload"]
    assert all(value in answer["claim"] for value in ("1250 EUR", "30 дней", "паспорт"))
    assert answer["conditions"] == ["только по записи"] and answer["assertion_kind"] == "experience"
    assert all(job["request"]["settings"]["analysis_prompts"]["commands"]["qa"].strip() in call["input"] for call in calls)
    assert store.processed_analysis_messages(job["job_id"]) == 7


@pytest.mark.parametrize("record_type", ["question", "problem", "event", "opinion", "experience", "claim"])
def test_s31_qa_extraction_final_item_requires_answer_evidence(qa_job, record_type):
    store, job = qa_job
    records = records_fixture(store, job, [{"record_type": record_type, "claim": "Вопрос без содержательного ответа"}])
    with pytest.raises(ValueError, match="qa_answer_missing_support"):
        analysis._synthesis_result(job, evidence(records), final_document([records[0]["record_id"]]))


@pytest.mark.parametrize("message", [2, 3])
def test_s31_qa_extraction_personal_solution_supports_generalized_question(qa_records, message):
    store, job, records, calls = qa_records
    answer = next(record for record in records if record["record_type"] == "answer"
        and record["payload"]["source_refs"][0]["message_id"] == message)
    value = analysis._synthesis_result(job, evidence(records), final_document([answer["record_id"]], text=answer["payload"]["claim"]))
    assert value["items"][0]["evidence_ids"] == [answer["record_id"]] and not value["partial"]
    assert value["items"][0]["text"] == answer["payload"]["claim"]
    assert answer["payload"]["assertion_kind"] == "experience"


def test_s31_qa_extraction_empty_items_do_not_become_fabricated_summary(qa_job):
    store, job = qa_job
    data = {**final_document([]), "items": [], "summary": "Неподдержанный ответ по памяти модели"}
    result = analysis._synthesis_result(job, evidence([]), data)
    assert result["items"] == [] and result["summary"] == "" and result["insufficient_information"]


def test_s31_qa_extraction_bounded_repair_cannot_turn_question_into_answer(qa_job):
    store, job = qa_job
    records = records_fixture(store, job, [{"record_type": "question", "claim": "Как открыть счёт без ВНЖ?"}])
    merge(store, job, merge_sender())
    calls = []
    def send(req, timeout):
        data = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        calls.append(data)
        # Both the initial synthesis and its one repair return an invented answer
        # citing only the real question. The source ID alone cannot validate it.
        return Response(model_response(final_document([records[0]["record_id"]])))
    result = synthesize(store, job, send)
    assert len(calls) == 2 and result["items"] == [] and result["partial"]
    assert "invalid_schema" in result["reasons"] and result["insufficient_information"]


@pytest.mark.parametrize("answer_first", [False, True])
def test_s32_cross_batch_qa_links_without_reply_in_either_order(qa_job, answer_first):
    store, job = qa_job
    question = {"record_type": "question", "claim": "Что нужно для подачи в Uno?", "entities": ["Uno"], "topic_key": "вопрос",
        "conditions": [], "exceptions": []}
    answer = {"record_type": "answer", "claim": "Я принёс паспорт, заплатил 1250 EUR; ответ через 30 дней.",
        "entities": ["Uno"], "topic_key": "документы", "assertion_kind": "experience", "conditions": ["только по записи"], "exceptions": ["студенты освобождены"]}
    filler = [{"claim": f"Постороннее событие {n}", "entities": ["Театр"], "topic_key": "культура"} for n in range(5)]
    records = records_fixture(store, job, [answer if answer_first else question, *filler, question if answer_first else answer])
    with pytest.raises(ValueError, match="fts_not_ready"):
        history.require_fts_ready(store.conn)
    link(store, job, link_sender("answers"))
    decisions = list(store.iter_link_decisions(job["job_id"]))
    ids = [records[0]["record_id"], records[-1]["record_id"]]
    assert any(set((d["left_id"], d["right_id"])) == set(ids) and d["relation"] == "answers" for d in decisions)
    assert store.leaf_evidence(job["job_id"], ids[0])["batch_id"] != store.leaf_evidence(job["job_id"], ids[1])["batch_id"]
    merge(store, job, merge_sender())
    calls = []
    def send(req, timeout):
        data = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        supplied = json.loads(data["input"].split("Опорные записи: ", 1)[1])
        calls.append(supplied)
        return Response(model_response(final_document(ids, text=answer["claim"])))
    result = synthesize(store, job, send)
    assert not result["partial"] and len(result["items"]) == 1
    assert result["items"][0]["conditions"] == ["только по записи"]
    assert "студенты освобождены" in result["items"][0]["exceptions"]
    assert set(calls[0]["records"][0]) == {"record_id", "record_type", "payload"}
    verified = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    assert {s["message_id"] for s in verified["items"][0]["sources"]} == {1, 7}
    assert all(record["payload"]["related_message_refs"] == [] for record in (records[0], records[-1]))


@pytest.mark.parametrize("relation", [None, "unrelated", "complements", "duplicate", "contradicts"])
def test_s32_cross_batch_qa_unverified_question_answer_pair_is_rejected(qa_job, relation):
    store, job = qa_job
    records = records_fixture(store, job, [
        {"record_type": "question", "claim": "Какая комиссия Uno за перевод?", "entities": ["Uno"]},
        {"record_type": "answer", "claim": "Для подачи документов нужен паспорт.", "entities": ["Uno"]}])
    ids = [r["record_id"] for r in records]
    supplied = evidence(records)
    if relation:
        supplied["relations"] = [{"left_id": ids[0], "right_id": ids[1], "relation": relation}]
    with pytest.raises(ValueError, match="qa_question_without_answer_link"):
        analysis._synthesis_result(job, supplied, final_document(ids))


def test_s32_cross_batch_qa_answer_to_another_question_does_not_validate_pair(qa_job):
    store, job = qa_job
    records = records_fixture(store, job, [
        {"record_type": "question", "claim": "Комиссия Uno?"},
        {"record_type": "question", "claim": "Какие документы нужны?"},
        {"record_type": "answer", "claim": "Принесите паспорт."}])
    ids = [r["record_id"] for r in records]
    supplied = evidence(records)
    supplied["relations"] = [{"left_id": ids[1], "right_id": ids[2], "relation": "answers"}]
    with pytest.raises(ValueError, match="qa_question_without_answer_link"):
        analysis._synthesis_result(job, supplied, final_document([ids[0], ids[2]]))


@pytest.mark.parametrize("with_answer,error", [(False, "qa_answer_missing_support"), (True, "qa_question_without_answer_link")])
def test_s32_cross_batch_qa_saved_result_rechecks_support_before_delivery(qa_job, with_answer, error):
    store, job = qa_job
    values = [{"record_type": "question", "claim": "Какая комиссия за перевод?"}]
    if with_answer:
        values.append({"record_type": "answer", "claim": "Для документов нужен паспорт."})
    records = records_fixture(store, job, values)
    result = final_document([record["record_id"] for record in records])
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    with pytest.raises(ValueError, match=error):
        analysis.verify_analysis_sources(store, job["job_id"], store.get_job(job["job_id"])["result"], generation=job["generation"])


def test_s32_cross_batch_qa_contrary_results_keep_conditions_and_both_sources(qa_job):
    store, job = qa_job
    records = records_fixture(store, job, [
        {"record_type": "question", "claim": "Сколько стоит подача?", "conditions": [], "exceptions": []},
        {"record_type": "answer", "claim": "Я заплатил 1250 EUR в Uno.", "assertion_kind": "experience",
         "conditions": ["подача в отделении"], "exceptions": []},
        {"record_type": "answer", "claim": "За тот же пакет запросили 1500 EUR.", "assertion_kind": "experience",
         "conditions": ["без зарплатных поступлений"], "exceptions": ["студенты освобождены"]}])
    ids = [r["record_id"] for r in records]
    supplied = evidence(records)
    def send(req, timeout):
        data = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        pairs = json.loads(data["input"].split("Кандидаты: ", 1)[1])["pairs"]
        return Response(model_response({"decisions": [{"left_id": left, "right_id": right,
            "relation": "answers" if ids[0] in (left, right) else "contradicts", "reason": "Fixture-labelled practical case"}
            for left, right in pairs], "saturated": False}))
    link(store, job, send)
    supplied["relations"] = list(store.iter_link_decisions(job["job_id"]))
    result = analysis._synthesis_result(job, supplied, final_document(ids[:2], text="Суммы в личных кейсах различаются."))
    item = result["items"][0]
    assert set(item["evidence_ids"]) == set(ids)
    assert set(item["conditions"]) == {"подача в отделении", "без зарплатных поступлений"}
    assert item["exceptions"] == ["студенты освобождены"]
    assert all(value in " ".join(item["contradictions"]) for value in ("1250 EUR", "1500 EUR"))
    verified = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    assert {source["message_id"] for source in verified["items"][0]["sources"]} == {1, 2, 3}


def test_s32_cross_batch_qa_answer_after_cutoff_never_enters_analysis(qa_job):
    store, job = qa_job
    seed(store, 22, [1], text="Какие документы нужны?")
    outside = "Секретный ответ вне cutoff: достаточно паспорта."
    seed(store, 22, [2], text=outside, date="2026-10-13T00:00:00+00:00")
    build(store, job)
    calls = []
    def send(req, timeout):
        data = json.loads(req.data)
        calls.append(data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
        result = document({"messages": fragments})
        result["records"][0].update(record_type="question", claim="Какие документы нужны?", conditions=[], exceptions=[])
        return Response(model_response(result))
    for batch in batches(store, job):
        extract(store, job, batch, send)
    link(store, job, lambda *a, **k: pytest.fail("One question has no pair to verify"))
    merge(store, job, merge_sender())
    saved = list(store.iter_leaf_evidence(job["job_id"], page_size=2))
    assert len(saved) == 1 and saved[0]["record_type"] == "question"
    assert all(outside not in analysis.canonical_json(call) for call in calls)
    supplied = analysis._synthesis_evidence(store, store.get_job(job["job_id"]))
    assert supplied["coverage"]["selected_messages"] == supplied["coverage"]["processed_messages"] == 1
    assert all(ref["message_id"] == 1 for record in supplied["records"] for ref in record["payload"]["source_refs"])


@pytest.fixture
def qa_journey(listener, config, access, runtime, tmp_path, monkeypatch):
    config["digest_ai"]["messages_per_ai_pass"] = 2
    config["analysis"] = {"overlap_messages": 0}
    root = tmp_path / "repo" / "telegram_connector"
    root.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(root))
    day = datetime.now(timezone.utc) - timedelta(days=1)
    texts = {(22, 1): "Как подать документы в Uno?", (22, 2): "В Uno открылся офис.",
        (22, 3): "Запись не работала. Позвонил оператору; записали за 2 дня.",
        (11, 1): "В Uno принёс паспорт; 1250 EUR, ответ за 30 дней, только по записи."}
    corpus = {22: posts([3, 2, 1]), 11: posts([1])}
    for peer, values in corpus.items():
        for message in values:
            message.date = day.replace(hour=10, minute=0, second=0, microsecond=0)
            message.message = texts[(peer, message.id)]
    state = {"calls": [], "sent": [], "runs": [], "client": None}
    original = analysis.execute_analysis_job
    def engine(store, job):
        class Client(EngineHistoryClient):
            async def get_entity(self, ref):
                if state.get("source_error") and ref == "@one":
                    from telethon import errors
                    raise errors.ChannelPrivateError(request=None)
                return await super().get_entity(ref)
        sources = copy.deepcopy(corpus)
        if state.get("only_questions"):
            for values in sources.values():
                for message in values:
                    message.message = "Как решить вопрос с документами Uno? Ответа пока нет."
        client = Client(store, sources)
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
            if kind == "telegram_evidence_v1":
                fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
                records = []
                for fragment in fragments:
                    message = fragment["message"]
                    pair = (message["channel_id"], message["message_id"])
                    if pair == (22, 2):
                        continue
                    kind = "question" if pair == (22, 1) or state.get("only_questions") else "answer"
                    value = document({"messages": [fragment]})["records"][0]
                    value.update(record_type=kind, claim=message["text"], entities=["Uno"] if pair != (22, 3) else ["Запись"],
                        topic_key="документы" if pair != (22, 3) else "запись", conditions=["только по записи"] if pair == (11, 1) else [],
                        exceptions=[], assertion_kind="experience" if kind == "answer" else "reported")
                    records.append(value)
                return Response(model_response({"records": records, "saturated": False}))
            if kind == "telegram_relations_v1":
                supplied = json.loads(data["input"].split("Кандидаты: ", 1)[1])
                by_id = {record["record_id"]: record["payload"] for record in supplied["records"]}
                decisions = []
                for left, right in supplied["pairs"]:
                    types = {by_id[left]["record_type"], by_id[right]["record_type"]}
                    decisions.append({"left_id": left, "right_id": right, "relation": "answers" if types == {"question", "answer"} else "unrelated",
                        "reason": "Fixture-labelled answer to the document question"})
                return Response(model_response({"decisions": decisions, "saturated": False}))
            if kind == "telegram_synthesis_v1":
                supplied = json.loads(data["input"].split("Опорные записи: ", 1)[1])
                by_id = {record["record_id"]: record["payload"] for record in supplied["records"]}
                items = []
                for key, record in by_id.items():
                    if record["record_type"] != "answer":
                        continue
                    ids = [key]
                    for relation in supplied["relations"]:
                        pair = [relation["left_id"], relation["right_id"]]
                        if relation["relation"] == "answers" and key in pair:
                            ids.extend(identity for identity in pair if identity not in ids)
                    title = "Как подать документы?" if record["topic_key"] == "документы" else "Как восстановить запись?"
                    items.append({"title": title, "text": "Личный опыт: " + record["claim"], "conditions": [], "exceptions": [],
                        "contradictions": [], "evidence_ids": ids})
                return Response(model_response({"summary": "Практические решения из обсуждения" if items else "",
                    "items": items, "limitations": [], "insufficient_information": not items, "saturated": False}))
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
    path = Path(config["paths"]["history_db"])
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
    def run(text="/qa @two,@one since=yesterday until=yesterday", number=42):
        dispatch(text, number)
        tick()
        return next(job for job in listener["jobs"]() if job["admission_key"].endswith(":" + str(number)))
    state.update(run=run, dispatch=dispatch, tick=tick, listener=listener)
    return state


@pytest.mark.parametrize("detail,tokens", [("short", 1000), ("normal", 3000), ("full", 12000)])
def test_s33_qa_journey_three_outputs_with_semantic_cross_channel_answers(qa_journey, detail, tokens):
    state = qa_journey
    job = state["run"]("/qa @two,@one since=yesterday until=yesterday" + ("" if detail == "normal" else f" --detail {detail}"))
    assert job["status"] == "completed" and job["delivery"]["outcome"] == "confirmed"
    assert job["request"]["request"]["detail"] == detail and job["result"]["coverage"]["processed_messages"] == 4
    assert len(job["result"]["items"]) == 2
    sent = [text for identity, text, parse_mode in state["sent"]]
    if detail == "full":
        target, relative = analysis.resolve_analysis_artifact(job)
        assert sent == [relative]
        text = target.read_text()
    else:
        text = "\n".join(sent)
        assert all(parse_mode == "HTML" for identity, text, parse_mode in state["sent"])
    missing = [value for value in ("Вопрос", "Ответ", "паспорт", "1250 EUR", "30 дней", "2 дня", "только по записи", "Личный опыт") if value not in text]
    assert not missing, missing
    assert "https://t.me/peer22/1" in text and "https://t.me/peer11/1" in text
    synthesis = next(data for identity, data in state["calls"] if data["text"]["format"]["name"] == "telegram_synthesis_v1")
    assert synthesis["max_output_tokens"] == tokens and "telegram_query_terms_v1" not in [
        data["text"]["format"]["name"] for identity, data in state["calls"]]


def test_s33_qa_journey_questions_only_yield_no_invented_answers(qa_journey):
    state = qa_journey
    state["only_questions"] = True
    job = state["run"]()
    assert job["status"] == "completed" and job["result"]["items"] == [] and job["result"]["insufficient_information"]
    assert "Недостаточно сведений" in "\n".join(text for identity, text, mode in state["sent"])


def test_s33_qa_journey_reuses_compatible_cache_and_keeps_search_isolated(qa_journey):
    state = qa_journey
    first = state["run"]()
    second = state["run"]("/qa @two,@one since=yesterday until=yesterday --detail short", number=43)
    assert second["status"] == "completed" and second["downloaded_messages"] == 0 and not state["client"].calls
    repeated = [data["text"]["format"]["name"] for identity, data in state["calls"] if identity == second["job_id"]]
    assert "telegram_evidence_v1" not in repeated and second["llm_calls"] < first["llm_calls"]
    third = state["run"]('/search @two,@one since=yesterday until=yesterday "Документы" --mode full', number=44)
    assert third["status"] == "completed"
    assert any(identity == third["job_id"] and data["text"]["format"]["name"] == "telegram_evidence_v1" for identity, data in state["calls"])


def test_s33_qa_journey_partial_source_retains_standalone_solution(qa_journey):
    state = qa_journey
    state["source_error"] = True
    job = state["run"]()
    assert job["status"] == "partial" and job["result"]["partial"]
    text = "\n".join(text for identity, text, mode in state["sent"])
    assert "Частичный" in text and "Позвонил оператору" in text and "https://t.me/peer22/3" in text
    assert "1250 EUR" not in text


def test_s33_qa_journey_status_cancel_during_inference(qa_journey):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    state = qa_journey
    entered, release = Event(), Event()
    def hold(job, data):
        entered.set()
        assert release.wait(5), "Fixture inference must be released"
    state["before_inference"] = hold
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(state["run"])
        try:
            assert entered.wait(5) and future.running()
            running = state["listener"]["jobs"]()[0]
            state["dispatch"]('/status ' + running["job_id"], number=43)
            state["dispatch"]('/cancel ' + running["job_id"], number=44)
            current = state["listener"]["jobs"]()[0]
            assert current["status"] == "running" and current["cancel_requested"]
            assert any("extract" in text for chat, text in state["listener"]["sent"])
        finally:
            release.set()
        stopped = future.result(timeout=5)
    assert stopped["status"] == "cancelled" and not state["sent"]


def test_s33_qa_journey_saved_result_resume_avoids_inference(qa_journey, monkeypatch):
    state = qa_journey
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
    resumed = state["listener"]["jobs"]()[0]
    assert resumed["status"] == "completed" and resumed["delivery"]["outcome"] == "confirmed"
    assert resumed["generation"] > first["generation"] and resumed["used_tokens"] == first["used_tokens"]
    assert len(state["calls"]) == calls and state["sent"]


def test_s33_qa_journey_unknown_send_is_not_replayed(qa_journey):
    state = qa_journey
    state["unknown_send"] = True
    job = state["run"]()
    assert job["delivery"]["outcome"] == "unknown"
    before = (len(state["calls"]), len(state["sent"]), len(state["runs"]))
    state["dispatch"]('/resume ' + job["job_id"], number=43)
    state["tick"]()
    state["run"]()
    assert (len(state["calls"]), len(state["sent"]), len(state["runs"])) == before
