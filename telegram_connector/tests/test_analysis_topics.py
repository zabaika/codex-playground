"""Topics contract fixtures prove application mechanics, not live model quality."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, messages
from telegram_connector.tests.test_analysis_evidence import document, model_response
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_merge import merge
from telegram_connector.tests.test_analysis_relations import link
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
        data = document({"messages": [fragment]})
        data["records"][0].update(record_type="opinion" if topic == "мнение" else "event",
            claim=text, entities=["Банк Uno"], topic_key=topic,
            conditions=["только счета без поступлений"] if ref["channel_id"] == 22 and ref["message_id"] == 2 else [],
            exceptions=["студенты"] if "студенты" in text else [], event_at=None,
            assertion_kind="opinion" if topic == "мнение" else "reported")
        batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key=f"fixture/{index}", kind="extract", payload={})
        values = analysis.validate_evidence_records(store, job["job_id"], batch["batch_id"], generation=job["generation"],
            document=data, fragments=[fragment])
        store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=values, inputs=[ref])
        records.extend(values)
    return store, job, records


def event_provider(req, timeout):
    if req.full_url.endswith("input_tokens"):
        return Response({"input_tokens": 100})
    request = json.loads(req.data)
    kind = request["text"]["format"]["name"]
    if kind == "telegram_relations_v1":
        data = json.loads(request["input"].split("Кандидаты: ", 1)[1])
        records = {r["record_id"]: r["payload"] for r in data["records"]}
        decisions = []
        for left, right in data["pairs"]:
            a, b = records[left], records[right]
            relation = "unrelated"
            if a["topic_key"] == b["topic_key"] == "комиссия":
                relation = "duplicate" if a["claim"] == b["claim"] else "contradicts" if any("Опровержение" in r["claim"] for r in (a, b)) else "complements"
            decisions.append({"left_id": left, "right_id": right, "relation": relation, "reason": "Fixture-labelled event relationship"})
        output = {"decisions": decisions, "saturated": False}
    else:
        assert kind == "telegram_merge_v1"
        data = json.loads(request["input"].split("Записи: ", 1)[1])
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


def test_s25_topics_events_synthesis_receives_ranked_membership_not_flat_entity_pool(topic_records):
    store, job, original = prepare_events(topic_records)
    evidence = analysis._synthesis_evidence(store, job)
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
        evidence = json.loads(request["input"].split("Опорные записи: ", 1)[1])
        assert all(group.get("evidence_ids") for group in evidence["groups"]), "Missing ranked event membership at the API boundary"
        items = []
        for group in evidence["groups"][:limit]:
            text = {"безопасность": "Утечка данных: сменить пароль до 13 октября.",
                "комиссия": "Комиссия 10 EUR была объявлена с 1 ноября, затем уточнена и отменена до запуска.",
                "офис": "Открылся офис в Мадриде; приём по записи с 15 октября."}[group["title"]]
            items.append({"title": group["title"], "text": text, "conditions": [], "exceptions": [],
                "contradictions": [], "evidence_ids": group["evidence_ids"]})
        return Response(model_response({"summary": "События периода", "items": items, "limitations": [],
            "insufficient_information": False, "saturated": False}))
    result = synthesize(store, job, sender)
    assert len(result["items"]) == limit and result["items"][0]["title"] == "безопасность"
    assert not result["partial"]
    if limit == 3:
        fee = result["items"][1]
        assert "10 EUR" in fee["text"] and "отменена" in fee["text"]
        assert "только счета без поступлений" in fee["conditions"] and "студенты" in fee["exceptions"]
        assert any("Опровержение" in text for text in fee["contradictions"])
        assert len(fee["evidence_ids"]) == 4 and result["items"][2]["title"] == "офис"
        view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
        assert {source["channel_id"] for source in view["items"][1]["sources"]} == {11, 22}


@pytest.mark.parametrize("source_limit", [1, 3])
def test_s25_topics_events_sources_prioritize_late_correction_over_duplicate_announcement(topic_records, source_limit):
    from telegram_connector.tests.test_analysis_delivery import frozen_output_setting
    store, job, records = topic_records
    frozen_output_setting(store, job, lambda p: p["request"].update(sources=source_limit))
    fee_ids = [r["record_id"] for r in records if r["payload"]["topic_key"] == "комиссия"]
    result = {"items": [{"evidence_ids": fee_ids}]}
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    sources = view["items"][0]["sources"]
    assert len(sources) == source_limit
    assert (22, 3) in {(s["channel_id"], s["message_id"]) for s in sources}, "Late correction needs a visible source"
    assert len({s["quote"] for s in sources}) == source_limit, "Duplicate repost must not displace distinct evidence"


def test_s25_topics_events_membership_stays_in_scope_when_final_input_is_split(topic_records):
    store, job, records = prepare_events(topic_records)
    counted = []
    def send(req, timeout):
        data = json.loads(req.data)
        evidence = json.loads(data["input"].split("Опорные записи: ", 1)[1])
        selected = {r["record_id"] for r in evidence["records"]}
        assert all(group["evidence_ids"] and set(group["evidence_ids"]) <= selected for group in evidence["groups"])
        if req.full_url.endswith("input_tokens"):
            counted.append(len(selected))
            return Response({"input_tokens": 16001 if len(selected) > 2 else 100})
        first = evidence["groups"][0]
        claim = next(r["payload"]["claim"] for r in evidence["records"] if r["record_id"] in first["evidence_ids"])
        return Response(model_response({"summary": "", "items": [{"title": first["title"], "text": claim,
            "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": first["evidence_ids"]}],
            "limitations": [], "insufficient_information": False, "saturated": False}))
    result = synthesize(store, job, send)
    assert len(counted) > 1 and counted[-1] <= 2
    assert result["partial"] and "synthesis_evidence_limited" in result["reasons"]
    assert result["items"][0]["title"] == "безопасность"


@pytest.fixture
def topics_journey(listener, config, access, runtime, tmp_path, monkeypatch):
    config["digest_ai"]["messages_per_ai_pass"] = 2
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
                if data["text"]["format"]["name"] == "telegram_synthesis_v1":
                    evidence = json.loads(data["input"].split("Опорные записи: ", 1)[1])
                    by_id = {r["record_id"]: r["payload"] for r in evidence["records"]}
                    items = [{"title": "Запуск сервиса Uno", "text": " ".join(by_id[key]["claim"] for key in group["evidence_ids"]),
                        "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": group["evidence_ids"]}
                        for group in evidence["groups"]]
                    return Response(model_response({"summary": "Развитие события", "items": items,
                        "limitations": [], "insufficient_information": False, "saturated": False}))
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
    payload = next(data for identity, data in state["calls"] if data["text"]["format"]["name"] == "telegram_synthesis_v1")
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
    assert repeated and not any(data["text"]["format"]["name"] == "telegram_evidence_v1" for data in repeated)
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
    view = analysis.verify_analysis_sources(store, job["job_id"], result, generation=job["generation"])
    ids = {s["message_id"] for s in view["items"][0]["sources"]}
    assert len(ids) == 3
    assert 1 in ids, "The source with the dates and amount must not be displaced by three later replies"
    assert 4 in ids, "Keep the latest update alongside substantive evidence"
