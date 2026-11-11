from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT
from telegram_connector.tests.analysis_fixtures import expand_wire_quotes, synthesis_fixture_groups
"""Topics contract fixtures prove application mechanics, not live model quality."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline
from telegram_connector import analysis_evidence
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, messages
from telegram_connector.tests.test_analysis_evidence import document, model_response
from telegram_connector.tests.test_analysis_owned_records import record as owned_record
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_merge import merge
from telegram_connector.tests.test_analysis_relations import link, relation_decision
from telegram_connector.tests.test_analysis_synthesis import synthesize, synthesis_job
from telegram_connector.tests.test_analysis_lifecycle import config, access, listener, update, supervisor, FixtureProcess
from telegram_connector.tests.test_analysis_history import runtime, posts
from telegram_connector.tests.test_analysis_engine import EngineHistoryClient, engine_provider
from telegram_connector import telegram_bridge as bridge
from telegram_connector.analysis_store import AnalysisStore
from common import sqlite as common_sqlite


CASES = [
    (22, 1, "2026-10-10", "комиссия", "Банк Uno объявил комиссию 10 EUR с 1 ноября; исключение: студенты."),
    (22, 2, "2026-10-11", "комиссия", "Уточнение комиссии 10 EUR: только счета без поступлений; студенты освобождены."),
    (22, 3, "2026-10-12", "комиссия", "Опровержение: Банк Uno отменил комиссию 10 EUR до запуска 1 ноября."),
    (22, 4, "2026-10-11", "офис", "Банк Uno открыл офис в Мадриде; приём по записи с 15 октября."),
    (11, 1, "2026-10-10", "комиссия", "Банк Uno объявил комиссию 10 EUR с 1 ноября; исключение: студенты."),
    (11, 2, "2026-10-12", "безопасность", "Банк Uno сообщил об утечке данных; нужно сменить пароль до 13 октября."),
    (22, 5, "2026-10-12", "мнение", "Мне нравится Банк Uno."),
]


@pytest.fixture
def topic_records(manifest_job, request):
    store, job = manifest_job
    source_cases = getattr(request, "param", CASES)
    cases = {(peer, number): (day, topic, text) for peer, number, day, topic, text in source_cases}
    for peer, number, day, topic, text in source_cases:
        seed(store, peer, [number], date=day + "T10:00:00+00:00", text=text)
    build(store, job)
    records = []
    for index, fragment in enumerate(messages(store, job)):
        ref = fragment["input"]
        ref["fragment_end"] = len(fragment["message"]["text"])
        day, topic, text = cases[(ref["channel_id"], ref["message_id"])]
        record = owned_record(text)
        record.update(record_type="opinion" if topic == "мнение" else "event",
            claim=text, entities=["Банк Uno"], topic_key=topic,
            assertion_kind="opinion" if topic == "мнение" else "reported")
        batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key=f"fixture/{index}", kind="extract", payload={})
        data = dict(saturated=False, input_dispositions={'i0000': dict(status='represented',
            records=[record], retained_record_ids=[], reason='')})
        values = analysis_evidence.classify_extraction_document(store, job["job_id"], batch["batch_id"], generation=job["generation"],
            document=data, fragments=[fragment], selected=[fragment])['records']
        store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=values, inputs=[ref])
        records.extend(values)
    return store, job, records


def event_provider(req, timeout):
    if req.full_url.endswith("input_tokens"):
        fmt = json.loads(req.data).get('text', {}).get('format', {}).get('name')
        return Response({"input_tokens": 1000000 if fmt == CURRENT_SYNTHESIS_FORMAT else 100})
    request = json.loads(req.data)
    kind = request["text"]["format"]["name"]
    if kind == "telegram_relations_v2":
        data = expand_wire_quotes(prompt_json(request["input"], 'records'))
        records = {r["record_id"]: r["payload"] for r in data["records"]}
        decisions = []
        for pair in data["pairs"]:
            left, right = pair["left_id"], pair["right_id"]
            a, b = records[left], records[right]
            relation = "unrelated"
            if a["topic_key"] == b["topic_key"] == "комиссия":
                relation = "duplicate" if a["claim"] == b["claim"] else "contradicts" if any("Опровержение" in r["claim"] for r in (a, b)) else "complements"
            decisions.append(relation_decision(pair, relation, "Fixture-labelled event relationship"))
        output = {"decisions": decisions, "saturated": False}
    else:
        assert kind == "telegram_merge_v1"
        data = prompt_json(request["input"], 'records')
        inputs = data["inputs"]
        groups = [{"title": topic, "summary": "Развитие одного события", "input_ids": [
            item["node_id"] for item in inputs if item["payload"]["topic_key"] == topic]}
            for topic in ("безопасность", "комиссия", "офис")]
        output = {"groups": groups, "dispositions": [{"input_id": item["node_id"],
            "disposition": "irrelevant" if item["payload"]["topic_key"] == "мнение" else "merged",
            "reason": "Fixture-labelled global event ranking"} for item in inputs], "saturated": False}
    return Response(model_response(output))


def prepare_events(topic_records):
    store, job, records = topic_records
    link(store, job, event_provider)
    merge(store, job, event_provider)
    return store, store.get_job(job["job_id"]), records


def ranked_fixture_groups(wire):
    """Author ranking from fixture labels; model payload no longer carries merge groups."""
    labels = {text: topic for _, _, _, topic, text in CASES}
    groups = []
    for topic in ('безопасность', 'комиссия', 'офис'):
        seen, ids = set(), []
        for record in wire['records']:
            claim = record['payload']['claim']
            if labels.get(claim) == topic and claim not in seen:
                ids.append(record['record_id'])
                seen.add(claim)
        if ids:
            groups.append(dict(title=topic, evidence_ids=ids))
    return groups


def test_s25_topics_events_synthesis_receives_ranked_membership_not_flat_entity_pool(topic_records):
    store, job, original = prepare_events(topic_records)
    evidence = analysis_pipeline._synthesis_evidence(store, job)
    assert [group["title"] for group in evidence["groups"]] == ["безопасность", "комиссия", "офис"]
    by_id = {r["record_id"]: r["payload"] for r in original}
    for group in evidence["groups"]:
        assert group.get("evidence_ids"), "Final synthesis needs exact membership of each ranked event"
        assert {by_id[key]["topic_key"] for key in group["evidence_ids"]} == {group["title"]}
    fee = evidence["groups"][1]["evidence_ids"]
    assert len(fee) == 4  # first, middle, late correction and another channel
    assert len({by_id[key]["reported_at"][:10] for key in fee}) == 3
    assert {ref["channel_id"] for key in fee for ref in by_id[key]["source_refs"]} == {11, 22}
    assert not evidence["partial"]


@pytest.mark.parametrize("limit", [1, 3])
def test_s25_topics_events_global_rank_corrections_concrete_facts_and_sources(topic_records, limit):
    store, job, records = prepare_events(topic_records)
    job = synthesis_job(store, job, "normal", limit=limit)
    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        request = json.loads(req.data)
        evidence = expand_wire_quotes(prompt_json(request["input"], 'evidence'))
        groups = ranked_fixture_groups(evidence)
        assert all(set(group['evidence_ids']) <= set(component)
            for group in groups for component in evidence['topic_components']
            if set(group['evidence_ids']) & set(component))
        items = []
        for group in groups[:limit]:
            text = {"безопасность": "Утечка данных: сменить пароль до 13 октября.",
                "комиссия": "Комиссия 10 EUR была объявлена с 1 ноября, затем уточнена и отменена до запуска. Только счета без поступлений; студенты освобождены.",
                "офис": "Открылся офис в Мадриде; приём по записи с 15 октября."}[group["title"]]
            items.append({"title": group["title"], "text": text,
                "contradictions": [], "evidence_ids": group["evidence_ids"]})
        return Response(model_response({"summary": "События периода", "items": items, "limitations": [],
            "insufficient_information": False, "saturated": False}))
    result = synthesize(store, job, sender)
    assert len(result["items"]) == limit and result["items"][0]["title"] == "безопасность"
    assert not result["partial"]
    if limit == 3:
        fee = result["items"][1]
        assert "10 EUR" in fee["text"] and "отменена" in fee["text"]
        assert "только счета без поступлений" in fee["text"].lower() and "студенты" in fee["text"]
        assert not {'conditions', 'exceptions'} & fee.keys()
        assert any("Опровержение" in text for text in fee["contradictions"])
        assert len(fee["evidence_ids"]) == 4 and result["items"][2]["title"] == "офис"
        view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
        # Contradiction expansion includes both announcements. Three links cannot
        # cite all four records; the current per-fact policy excludes that item.
        assert [item['title'] for item in view['items']] == ['безопасность', 'офис']
        assert view['excluded_unsupported_items'] == 1


@pytest.mark.parametrize("source_limit", [1, 3])
def test_s25_topics_events_sources_prioritize_late_correction_over_duplicate_announcement(topic_records, source_limit):
    from telegram_connector.tests.test_analysis_delivery import frozen_output_setting
    store, job, records = topic_records
    frozen_output_setting(store, job, lambda p: p["request"].update(sources=source_limit))
    fee_ids = [r["record_id"] for r in records if r["payload"]["topic_key"] == "комиссия"
               and r['payload']['source_refs'][0]['channel_id'] == 22]
    result = {"items": [{"evidence_ids": fee_ids}]}
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    if source_limit == 1:
        assert not view['items'], 'One source cannot confirm three distinct timeline facts'
        return
    sources = view["items"][0]["sources"]
    assert len(sources) == source_limit
    assert (22, 3) in {(s["channel_id"], s["message_id"]) for s in sources}, "Late correction needs a visible source"
    assert len({s["quote"] for s in sources}) == source_limit, "Duplicate repost must not displace distinct evidence"


def test_s25_topics_events_membership_stays_in_scope_when_final_input_is_split(topic_records):
    store, job, records = prepare_events(topic_records)
    counted = []
    def send(req, timeout):
        data = json.loads(req.data)
        evidence = expand_wire_quotes(prompt_json(data["input"], 'evidence'))
        selected = {r["record_id"] for r in evidence["records"]}
        groups = ranked_fixture_groups(evidence)
        assert all(group["evidence_ids"] and set(group["evidence_ids"]) <= selected for group in groups)
        if req.full_url.endswith("input_tokens"):
            counted.append(len(selected))
            return Response({"input_tokens": job["request"]["settings"]["analysis"]["max_input_tokens"] + 1 if len(selected) > 2 else 100})
        first = groups[0]
        claim = next(r["payload"]["claim"] for r in evidence["records"] if r["record_id"] in first["evidence_ids"])
        return Response(model_response({"summary": "", "items": [{"title": first["title"], "text": claim,
            "contradictions": [], "evidence_ids": first["evidence_ids"]}],
            "limitations": [], "insufficient_information": False, "saturated": False}))
    result = synthesize(store, job, send)
    assert len(counted) > 1 and counted[-1] <= 2
    assert result["partial"] and "synthesis_evidence_limited" in result["reasons"]
    assert result["items"][0]["title"] == "безопасность"


@pytest.fixture
def topics_journey(listener, config, access, runtime, tmp_path, monkeypatch):
    config["analysis"]["ai"]["messages_per_ai_pass"] = 2
    root = tmp_path / "repo" / "telegram_connector"
    root.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(root))
    values = posts([3, 2, 1])
    day = datetime.now(timezone.utc) - timedelta(days=1)
    for message in values:
        message.date = day.replace(hour=10, minute=0, second=0, microsecond=0)
        message.message = f"Запуск сервиса Uno: этап {message.id}, срок {message.id * 10} дней."
    state = {"client": None, "calls": [], "sent": [], "runs": []}
    original = analysis.execute_analysis_job
    def engine(store, job):
        class Client(EngineHistoryClient):
            async def get_entity(self, ref):
                if state.get("source_error") and ref == "@one":
                    from telethon import errors
                    raise errors.ChannelPrivateError(request=None)
                return await super().get_entity(ref)
        client = Client(store, {22: values, 11: []})
        state["client"] = client
        provider = engine_provider(store, job, [])
        def send(req, timeout):
            data = json.loads(req.data)
            if not req.full_url.endswith("input_tokens"):
                if state.get("before_inference"):
                    state["before_inference"](job, data)
                state["calls"].append((job["job_id"], data))
                if data["text"]["format"]["name"] == CURRENT_SYNTHESIS_FORMAT:
                    evidence = expand_wire_quotes(prompt_json(data["input"], 'evidence'))
                    by_id = {r["record_id"]: r["payload"] for r in evidence["records"]}
                    items = [{'title': 'Запуск сервиса Uno', 'text': ' '.join((by_id[key]['claim'] for key in group['evidence_ids'])), 'contradictions': [], 'evidence_ids': group['evidence_ids']}
                        for group in synthesis_fixture_groups(evidence)]
                    return Response(model_response({'summary': 'Развитие события', 'items': items, 'limitations': [], 'insufficient_information': False, 'saturated': False}))
            return provider(req, timeout)
        return original(store, job, runtime=runtime, client=client, api_key="fixture", token="fixture",
            capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
            access_provider=lambda: access, urlopen_func=send,
            send_func=lambda token, chat, text, mode, **kw:
                state["sent"].append((job["job_id"], text, mode)) or {"message_id": len(state["sent"]), "chat": {"id": 100}})
    monkeypatch.setattr(analysis, "execute_analysis_job", engine)
    path = Path(config["paths"]["history_db"])
    def launch(job):
        code = analysis.main(["--db", str(path), "--job-id", job["job_id"], "--generation", str(job["generation"]), "--worker-inner"])
        state["runs"].append(code)
        process = FixtureProcess()
        process.returncode = code
        return process
    scheduler = supervisor(config, access, launch=launch)
    def run(text="/topics @two since=yesterday until=yesterday", number=42):
        listener["events"] = [update(number, text)]
        assert bridge.cmd_listen(listener["args"]) == 0
        conn = common_sqlite.connect_sqlite(path)
        try:
            scheduler._tick(AnalysisStore(conn))
            scheduler._tick(AnalysisStore(conn))
        finally:
            conn.close()
        return next(job for job in listener["jobs"]() if job["admission_key"].endswith(":" + str(number)))
    state.update(run=run, listener=listener, scheduler=scheduler)
    return state


@pytest.mark.parametrize("detail,tokens", [("short", 1000), ("normal", 3000), ("full", 12000)])
def test_s26_topics_journey_bridge_supervisor_worker_and_three_outputs(topics_journey, detail, tokens):
    state = topics_journey
    option = "" if detail == "normal" else f" --detail {detail}"
    job = state["run"]("/topics @two since=yesterday until=yesterday" + option)
    assert job["status"] == "completed" and job["delivery"]["outcome"] == "confirmed"
    assert state["runs"] == [0] and job["job_id"] in state["listener"]["sent"][0][1]
    assert state["listener"]["offsets"] == [43]
    assert job["request"]["request"]["detail"] == detail
    payload = next(data for identity, data in state["calls"] if data["text"]["format"]["name"] == CURRENT_SYNTHESIS_FORMAT)
    assert payload["max_output_tokens"] == tokens
    sent = [text for identity, text, mode in state["sent"]]
    if detail == "full":
        target, relative = analysis.resolve_analysis_artifact(job)
        assert sent == [relative] and "этап 1" in target.read_text() and "этап 3" in target.read_text()
        assert "https://t.me/peer22/" in target.read_text()
    else:
        assert all(f"этап {n}" in "\n".join(sent) for n in (1, 2, 3))
        assert "https://t.me/peer22/" in "\n".join(sent)
        assert all(mode == "HTML" for identity, text, mode in state["sent"])


def test_s26_topics_journey_repeat_period_reuses_history_and_extraction(topics_journey):
    state = topics_journey
    first = state["run"]()
    second = state["run"](number=43)
    assert first["status"] == second["status"] == "completed"
    assert second["downloaded_messages"] == 0 and not state["client"].calls
    repeated = [data for identity, data in state["calls"] if identity == second["job_id"]]
    assert repeated and not any(data["text"]["format"]["name"] == CURRENT_EVIDENCE_FORMAT for data in repeated)
    assert second["llm_calls"] < first["llm_calls"]


def test_s26_topics_journey_partial_source_is_visible_with_successful_event(topics_journey):
    state = topics_journey
    state["source_error"] = True
    job = state["run"]("/topics @two,@one since=yesterday until=yesterday")
    assert job["status"] == "partial" and job["result"]["partial"]
    text = "\n".join(text for identity, text, mode in state["sent"])
    assert "Частичный" in text and "этап 3" in text and "https://t.me/peer22/" in text


def test_s26_topics_journey_status_and_cancel_remain_responsive_during_inference(topics_journey):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    state = topics_journey
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
            state["listener"]["events"] = [update(43, "/status " + running["job_id"]), update(44, "/cancel " + running["job_id"])]
            assert bridge.cmd_listen(state["listener"]["args"]) == 0
            current = state["listener"]["jobs"]()[0]
            assert current["status"] == "running" and current["cancel_requested"]
            assert any("extract" in text for chat, text in state["listener"]["sent"])
        finally:
            release.set()
        stopped = future.result(timeout=5)
    assert stopped["status"] == "cancelled" and not state["sent"]


def test_s26_topics_journey_explicit_resume_delivers_saved_result_without_inference(topics_journey, config, monkeypatch):
    state = topics_journey
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
    previous_calls = len(state["calls"])
    state["listener"]["events"] = [update(43, "/resume " + first["job_id"])]
    assert bridge.cmd_listen(state["listener"]["args"]) == 0
    conn = common_sqlite.connect_sqlite(config["paths"]["history_db"])
    try:
        state["scheduler"]._tick(AnalysisStore(conn))
        state["scheduler"]._tick(AnalysisStore(conn))
    finally:
        conn.close()
    resumed = state["listener"]["jobs"]()[0]
    assert resumed["status"] == "completed" and resumed["delivery"]["outcome"] == "confirmed"
    assert resumed["generation"] > first["generation"] and resumed["used_tokens"] == first["used_tokens"]
    assert len(state["calls"]) == previous_calls and len(state["sent"]) == 1



@pytest.mark.parametrize("topic_records", [[
    (22, 1, "2026-10-10", "взнос", "Autonomo зарегистрировано 27 июля. За август пришел и оплачен соцстрах 88.56. За июль с 27 по 31 нет начисления. Что делать?"),
    (22, 2, "2026-10-11", "взнос", "Если не выставили — ждать: https://portal.example/" + "payment-receipts/" * 10),
    (22, 3, "2026-10-11", "взнос", "Если выставят позже, появится информация в системе."),
    (22, 4, "2026-10-12", "взнос", "Не, сами сделают"),
]], indirect=True)
def test_s38_topics_sources_keep_substantive_original_and_latest_update(topic_records):
    store, job, records = topic_records
    result = {"items": [{"evidence_ids": [r["record_id"] for r in records]}]}
    assert not analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])['items']
    # Publish only the three cited facts; the fourth fact cannot borrow an unrelated link.
    result['items'][0]['evidence_ids'] = [r['record_id'] for r in records
        if r['payload']['source_refs'][0]['message_id'] in {1, 2, 4}]
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    ids = {s["message_id"] for s in view["items"][0]["sources"]}
    assert len(ids) == 3
    assert 1 in ids, "The source with the dates and amount must not be displaced by three later replies"
    assert 4 in ids, "Keep the latest update alongside substantive evidence"


@pytest.mark.parametrize("topic_records", [[
    (22, 1, "2026-10-10", "комиссия", "Банк Uno объявил комиссию 10 EUR."),
    (22, 2, "2026-10-12", "комиссия", "Нейродайджест за неделю\nБанк Uno объявил комиссию 10 EUR.\n#дайджест"),
]], indirect=True)
def test_topics_standalone_publication_cannot_launder_an_additional_digest_only_fact(topic_records):
    from telegram_connector.tests.test_analysis_delivery import frozen_output_setting
    store, job, records = topic_records
    frozen_output_setting(store, job, lambda p: p["request"].update(sources=1))
    result = {"items": [{"evidence_ids": [r["record_id"] for r in records]}]}
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    assert not view['items']
    result['items'][0]['evidence_ids'] = [r['record_id'] for r in records
        if r['payload']['source_refs'][0]['message_id'] == 1]
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    assert [s["message_id"] for s in view["items"][0]["sources"]] == [1]


@pytest.mark.parametrize("topic_records", [[
    (22, 1, "2026-10-10", "pro", "Нейродайджест за две недели\nOpenAI изменила лимиты Pro.\n#дайджест"),
]], indirect=True)
def test_digest_only_item_is_excluded_without_replacing_it_with_unrelated_post(topic_records):
    store, job, records = topic_records
    result = {"summary": "OpenAI изменила лимиты Pro.", "items": [{"evidence_ids": [records[0]["record_id"]]}]}
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    assert not view["items"] and not view.get("sources")
    assert view["summary"] == "" and view["insufficient_information"]
    assert view["excluded_digest_only_items"] == 1
    assert view['limitations'] == []
    from telegram_connector.analysis_rendering import _digest_selection_notice
    assert _digest_selection_notice(view) == 'Исключён 1 пункт: информация есть только в дайджесте, отдельного поста по теме нет.'
    assert len(result["items"]) == 1  # Persisted synthesis remains auditable.


@pytest.mark.parametrize("topic_records", [[
    (22, 1, "2026-10-10", "комиссия", "Банк Uno объявил комиссию 10 EUR."),
    (22, 2, "2026-10-12", "pro", "Нейродайджест за неделю\nOpenAI изменила лимиты Pro.\n#дайджест"),
]], indirect=True)
def test_excluding_digest_only_item_preserves_introduction_before_retained_topics(topic_records):
    store, job, records = topic_records
    summary = ("За период источники сообщили о релизах и изменениях у OpenAI, Google, Anthropic "
               "и других компаний: новые модели, продукты, тарифы, режимы доступа и исследовательские результаты. "
               "Сведения имеют статус сообщений источников; для большинства событий дата события "
               "не указана отдельно от даты публикации.")
    items = [{'title': 'Тема', 'text': 'Подробность', 'contradictions': [], 'evidence_ids': [r['record_id']]} for r in records]
    result = {"summary": summary, "items": items, "limitations": [], "partial": False,
              "coverage": {"selected_messages": 2, "processed_messages": 2}}
    view = analysis.prepare_analysis_presentation(store, job["job_id"], result, generation=job["generation"])
    assert view["result"]["summary"] == summary
    assert len(view["result"]["items"]) == 1
    assert view["result"]["excluded_digest_only_items"] == 1
    html = "\n".join(view["chunks"])
    assert html.index("Главные темы") < html.index(summary) < html.index("<b>Тема</b>")
    assert "https://t.me/peer22/2" not in html


def test_presentation_header_resolves_all_scoped_channel_titles(topic_records):
    store, job, records = topic_records
    store.conn.execute("UPDATE channels SET title='Налоги 🇪🇸' WHERE channel_id=22")
    store.conn.execute("UPDATE channels SET title='Visa Spain' WHERE channel_id=11")
    result = {"summary": "", "items": [], "coverage": {"selected_messages": 7},
              "partial": False, "insufficient_information": True, "limitations": []}
    view = analysis.prepare_analysis_presentation(store, job["job_id"], result, generation=job["generation"])
    assert set(view["result"]["channel_titles"]) == {"Налоги 🇪🇸", "Visa Spain"}
    assert "Сообщений в анализе: 7" in view["chunks"][0]


def test_source_policy_still_rejects_an_item_without_evidence(topic_records):
    store, job, records = topic_records
    with pytest.raises(ValueError, match="final_evidence_without_support"):
        analysis.verify_analysis_sources(store, job["job_id"], {"items": [{"evidence_ids": []}]},
                                         generation=job["generation"])


@pytest.mark.parametrize("text,digest", [
    ("Нейродайджест за неделю\nНовости", True),
    ("📰 **Дайджест недели**\nНовости", True),
    ("Weekly roundup\nNews", True),
    ("Новости\n#дайджест", True),
    ("Новая модель\n#DIGEST", True),
    ("Как собрать дайджест\nОтдельная новость о продукте", False),
    ("Новость\nЯ прочитал дайджест и проверил эту новость.", False),
])
def test_digest_source_detection_uses_explicit_heading_or_tag(text, digest):
    assert analysis_evidence._is_digest_source({"text": text}) is digest
