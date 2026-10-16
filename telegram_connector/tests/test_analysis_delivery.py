import pytest
import copy
import xml.etree.ElementTree as ET
from telegram_shared.errors import TelegramApiError

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, messages
from telegram_connector.tests.test_analysis_evidence import document


@pytest.mark.parametrize("channel_id,username,kind,expected", [
    (22, "public_two", "Channel", "https://t.me/public_two/7"),
    (22, None, "Channel", "https://t.me/c/22/7"),
    (-1000000000022, None, "Channel", "https://t.me/c/22/7"),
    (22, None, "Chat", None),
    (22, "some_user", "User", None),
    (-22, None, "Channel", None),
    (22, 'bad\"/><a href="https://invalid.example"', "Channel", "https://t.me/c/22/7"),
])
def test_s21_sources_and_rendering_message_links_use_peer_type_and_real_id(channel_id, username, kind, expected):
    function = getattr(analysis, "build_analysis_message_link", None)
    assert callable(function), "Message links require validated peer type and normalized channel identity"
    assert function({"channel_id": channel_id, "username": username, "channel_type": kind}, 7) == expected


@pytest.mark.parametrize("message_id", [0, -1, True, "7"])
def test_s21_sources_and_rendering_invalid_message_ids_have_no_url(message_id):
    function = getattr(analysis, "build_analysis_message_link", None)
    assert callable(function)
    assert function({"channel_id": 22, "username": "public_two", "channel_type": "Channel"}, message_id) is None


def sourced_result(store, job, *, record_type="claim"):
    seed(store, 22, [1], text="Условие A: 1250 EUR.")
    seed(store, 11, [1], text="Условие B: 30 дней.")
    build(store, job)
    ids = []
    for fragment in messages(store, job):
        fragment["input"]["fragment_end"] = len(fragment["message"]["text"])
        batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key=f"fixture-extract/{fragment['input']['channel_id']}", kind="extract", payload={})
        data = document({"messages": [fragment]})
        data["records"][0].update(record_type=record_type, claim=fragment["message"]["text"], conditions=[], exceptions=[])
        values = analysis.validate_evidence_records(store, job["job_id"], batch["batch_id"], generation=job["generation"], document=data, fragments=[fragment])
        store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=values, inputs=[fragment["input"]])
        ids.append(values[0]["record_id"])
    return {"command": "topics", "detail": "normal", "query": None, "summary": "Условия из истории.",
        "items": [{"title": "Условия", "text": "Минимум 1250 EUR, срок 30 дней.", "conditions": [], "exceptions": [],
                   "contradictions": [], "evidence_ids": ids}], "limitations": [], "insufficient_information": False,
        "partial": False, "reasons": [], "coverage": {"selection_mode": "all_messages", "selected_messages": 2,
            "processed_messages": 2, "known_skipped": 0}}


def verified(store, job, result):
    function = getattr(analysis, "verify_analysis_sources", None)
    assert callable(function), "Final sources must resolve through current-job evidence and live message digests"
    return function(store, job["job_id"], result, generation=job["generation"])


@pytest.mark.parametrize("sent", [False, "confirmed", "unknown", "partial"])
def test_review_source_invalidation_clears_only_unsent_delivery(manifest_job, sent):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    store.prepare_delivery(job["job_id"], generation=job["generation"], chunks=["1250 EUR", "Срок 30 дней."],
        parse_mode="HTML", detail="normal", analysis_partial=False)
    if sent:
        intent = delivery_intent(store, job)
        if sent != "unknown":
            delivery_receipt(store, job, intent)
            if sent == "confirmed":
                intent = delivery_intent(store, job, part=1)
                store.settle_delivery_attempt(job["job_id"], generation=job["generation"], part_index=1,
                    attempt_id=intent["attempt_id"], outcome="confirmed", message_id=701, chat_id="100")
    before = store.get_job(job["job_id"])
    batch_id = store.leaf_evidence(job["job_id"], result["items"][0]["evidence_ids"][0])["batch_id"]
    if sent:
        with pytest.raises(ValueError, match="delivery_source_changed"):
            store.invalidate_batch_dependencies(job["job_id"], batch_id, generation=job["generation"])
        after = store.get_job(job["job_id"])
        assert after["delivery"] == before["delivery"] and after["result"] == before["result"]
        assert verified(store, job, result)
    else:
        store.invalidate_batch_dependencies(job["job_id"], batch_id, generation=job["generation"])
        assert store.get_job(job["job_id"])["delivery"] == {}
        assert store.get_job(job["job_id"])["result"] is None


def test_s21_sources_and_rendering_same_message_id_keeps_distinct_peers(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    before = copy.deepcopy(result)
    output = verified(store, job, result)
    refs = output["items"][0]["sources"]
    assert {(ref["channel_id"], ref["message_id"]) for ref in refs} == {(22, 1), (11, 1)}
    assert {ref["url"] for ref in refs} == {"https://t.me/peer22/1", "https://t.me/peer11/1"}
    assert result == before


def test_s21_sources_and_rendering_forged_url_cannot_replace_real_source(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["items"][0]["sources"] = [{"url": "https://t.me/unrelated/999"}]
    assert all(ref["url"] != "https://t.me/unrelated/999" for ref in verified(store, job, result)["items"][0]["sources"])


@pytest.mark.parametrize("mutation", ["unknown_id", "source_edit", "quote"])
def test_s21_sources_and_rendering_invalid_or_changed_support_is_rejected(manifest_job, mutation):
    store, job = manifest_job
    result = sourced_result(store, job)
    if mutation == "unknown_id": result["items"][0]["evidence_ids"] = ["foreign-record"]
    if mutation == "source_edit": store.conn.execute("UPDATE messages SET text='Другие условия' WHERE channel_id=22 AND message_id=1")
    if mutation == "quote":
        record = store.leaf_evidence(job["job_id"], result["items"][0]["evidence_ids"][0])
        record["payload"]["source_refs"][0]["quote"] = "Нерелевантный текст"
        store.conn.execute("UPDATE analysis_evidence SET payload_json=? WHERE record_id=?", (analysis.canonical_json(record["payload"]), record["record_id"]))
    with pytest.raises(ValueError):
        verified(store, job, result)


def test_s21_sources_and_rendering_sources_override_does_not_skip_validation(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    payload = store.get_job(job["job_id"])["request"]
    payload["request"]["sources"] = 1
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(payload), job["job_id"]))
    shown = verified(store, job, result)["items"][0]["sources"]
    assert len(shown) == 1
    omitted_peer = ({11, 22} - {shown[0]["channel_id"]}).pop()
    store.conn.execute("UPDATE messages SET text='Изменённый источник' WHERE channel_id=?", (omitted_peer,))
    with pytest.raises(ValueError, match="input_changed"):
        verified(store, job, result)


def presentation(store, job, result):
    function = getattr(analysis, "prepare_analysis_presentation", None)
    assert callable(function), "Structured analysis requires a bounded, escaped presentation before delivery"
    return function(store, job["job_id"], result, generation=job["generation"])


def test_s21_sources_and_rendering_escapes_model_markup_and_preserves_conditions(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["items"][0].update(title="<b>Банк & условия</b>", text='Сумма < 1250 EUR & срок > 30 дней.',
        conditions=['Только при наличии "контракта"'], exceptions=["Кроме нерезидентов"], contradictions=["Другой источник: 60 дней"])
    output = presentation(store, job, result)
    text = "\n".join(output["chunks"])
    assert output["parse_mode"] == "HTML"
    assert "&lt;b&gt;Банк &amp; условия&lt;/b&gt;" in text
    assert "Сумма &lt; 1250 EUR &amp; срок &gt; 30 дней." in text
    assert "Только при наличии &quot;контракта&quot;" in text and "Кроме нерезидентов" in text
    assert "Другой источник: 60 дней" in text
    assert '<a href="https://t.me/peer22/1">' in text


@pytest.mark.parametrize("field", ["summary", "text", "conditions"])
def test_s21_sources_and_rendering_unverified_body_urls_are_rejected(manifest_job, field):
    store, job = manifest_job
    result = sourced_result(store, job)
    value = "Смотрите https://t.me/unrelated/999"
    if field == "summary": result[field] = value
    elif field == "conditions": result["items"][0][field] = [value]
    else: result["items"][0][field] = value
    with pytest.raises(ValueError, match="unverified_url"):
        presentation(store, job, result)


def test_s21_sources_and_rendering_full_has_no_telegram_prose(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    payload = store.get_job(job["job_id"])["request"]
    payload["request"]["detail"] = "full"
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(payload), job["job_id"]))
    result["detail"] = "normal"  # Only the frozen request controls the destination.
    output = presentation(store, job, result)
    assert output["detail"] == "full" and output["chunks"] == []
    assert output["result"]["items"][0]["sources"]


def test_s21_sources_and_rendering_budget_fallback_is_honest_and_has_no_claims(manifest_job):
    store, job = manifest_job
    sourced_result(store, job)
    fallback = store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_llm_calls")
    text = "\n".join(presentation(store, job, fallback)["chunks"])
    assert "Частичный" in text and "max_llm_calls" in text
    assert "Обработано сообщений: 2" in text and "Проверенные утверждения не сформированы" in text
    assert "Источники обработанных сообщений" in text


def test_s21_sources_and_rendering_chat_link_unavailability_is_rendered(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.conn.execute("UPDATE channels SET channel_type='Chat',username=NULL WHERE channel_id=22")
    text = "\n".join(presentation(store, job, result)["chunks"])
    assert "ссылка недоступна" in text and "22/1" in text


def frozen_output_setting(store, job, mutate):
    payload = store.get_job(job["job_id"])["request"]
    mutate(payload)
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(payload), job["job_id"]))


def test_s21_sources_and_rendering_long_escaped_blocks_split_without_broken_html(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["items"][0]["text"] = "<&😀 " * 150
    frozen_output_setting(store, job, lambda p: p["settings"]["bridge"].update(text_chunk_size=500))
    output = presentation(store, job, result)
    assert 1 < len(output["chunks"]) <= 8 and output["result"]["partial"] is False
    for chunk in output["chunks"]:
        assert len(chunk.encode("utf-16-le")) // 2 <= 500
        ET.fromstring("<root>" + chunk + "</root>")
    assert sum(chunk.count("😀") for chunk in output["chunks"]) == 150


@pytest.mark.parametrize("bound", ["chars", "messages"])
def test_s21_sources_and_rendering_output_caps_drop_whole_items_and_mark_partial(manifest_job, bound):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["items"] = [{**copy.deepcopy(result["items"][0]), "title": f"Пункт {i}", "text": "Сведения. " * 50,
        "conditions": [f"Условие {i}: необходимо выполнить полностью"]} for i in range(6)]
    if bound == "chars":
        frozen_output_setting(store, job, lambda p: p["settings"]["analysis"]["output"]["profiles"]["normal"].update(max_chars=1800))
    else:
        frozen_output_setting(store, job, lambda p: (p["settings"]["bridge"].update(text_chunk_size=500),
            p["settings"]["analysis"]["output"].update(max_messages=2)))
    output = presentation(store, job, result)
    text = "\n".join(output["chunks"])
    assert output["result"]["partial"] and len(output["result"]["items"]) < 6 and "Частичный" in text
    assert output["result"]["summary"] == ""
    assert (sum(len(c) for c in output["chunks"]) <= 1800) if bound == "chars" else (len(output["chunks"]) <= 2)
    for item in output["result"]["items"]:
        assert item["conditions"][0] in text


def test_s21_sources_and_rendering_candidate_search_is_not_reported_as_full(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["coverage"].update(selection_mode="candidates", known_skipped=None)
    text = "\n".join(presentation(store, job, result)["chunks"])
    assert "отобранных кандидатов" in text and "неизвестно" in text


@pytest.mark.parametrize("mode,excluded", [("all_messages", 0), ("candidates", None)])
def test_s38_coverage_distinguishes_unprocessed_selection_from_excluded_history(mode, excluded):
    # Observed live case: 205 selected, 186 processed, zero excluded by selection.
    text = "\n".join(analysis._analysis_coverage_lines({"selection_mode": mode,
        "selected_messages": 205, "processed_messages": 186, "known_skipped": excluded}))
    assert "не обработано из выбранных: 19" in text
    assert f'Не включено в выборку: {excluded if excluded is not None else "неизвестно"}' in text
    assert "пропущено: 0" not in text


def test_s38_unprocessed_count_is_visible_in_telegram_and_full_markdown(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result.update(partial=True, reasons=["extraction_incomplete"])
    result["coverage"]["processed_messages"] = 1
    telegram = "\n".join(presentation(store, job, result)["chunks"])
    markdown = analysis._full_analysis_markdown(job, verified(store, job, result))
    assert all("не обработано из выбранных: 1" in text for text in (telegram, markdown))


def test_s21_sources_and_rendering_root_sources_cannot_authorize_model_url(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["sources"] = [{"url": "https://t.me/forged/999", "quote": None}]
    result["summary"] = "Сведения: https://t.me/forged/999"
    with pytest.raises(ValueError, match="unverified_url"):
        presentation(store, job, result)


def test_s21_sources_and_rendering_capped_fallback_preserves_original_reason(manifest_job):
    store, job = manifest_job
    sourced_result(store, job)
    fallback = store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_llm_calls")
    frozen_output_setting(store, job, lambda p: p["settings"]["analysis"]["output"]["profiles"]["normal"].update(max_chars=330))
    output = presentation(store, job, fallback)
    assert output["result"]["partial"] is True
    assert {"max_llm_calls", "max_chars"} <= set(output["result"]["reasons"])
    assert "max_llm_calls" in "\n".join(output["chunks"])


def test_s21_sources_and_rendering_uses_source_url_literally_present_in_quote(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1], text="Подробные условия https://example.org/rules")
    build(store, job)
    fragment = messages(store, job)[0]
    fragment["input"]["fragment_end"] = len(fragment["message"]["text"])
    batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key="fixture-url", kind="extract", payload={})
    data = document({"messages": [fragment]})
    data["records"][0]["source_refs"][0].update(fragment_end=len(fragment["message"]["text"]), quote=fragment["message"]["text"])
    records = analysis.validate_evidence_records(store, job["job_id"], batch["batch_id"], generation=job["generation"], document=data, fragments=[fragment])
    store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=records, inputs=[fragment["input"]])
    result = {"summary": "Условия https://example.org/rules", "items": [{"title": "Условия", "text": "Уточнение.", "conditions": [],
        "exceptions": [], "contradictions": [], "evidence_ids": [records[0]["record_id"]]}], "limitations": [], "partial": False,
        "reasons": [], "coverage": {"selection_mode": "all_messages", "selected_messages": 1, "processed_messages": 1, "known_skipped": 0}}
    assert "https://example.org/rules" in "\n".join(presentation(store, job, result)["chunks"])


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
@pytest.mark.parametrize("detail", ["short", "normal", "full"])
def test_s21_sources_and_rendering_shared_adapter_for_each_command_and_detail(manifest_job, command, detail):
    store, job = manifest_job
    result = sourced_result(store, job, record_type="answer" if command == "qa" else "claim")
    store.conn.execute("UPDATE analysis_jobs SET command=? WHERE job_id=?", (command, job["job_id"]))
    frozen_output_setting(store, job, lambda p: p["request"].update(command=command, detail=detail, limit=5,
        query='Правила <банка> & сроки' if command == "search" else None))
    output = presentation(store, job, result)
    assert output["detail"] == detail and output["result"]["command"] == command
    assert bool(output["chunks"]) is (detail != "full")
    if command == "search" and detail != "full":
        assert "Правила &lt;банка&gt; &amp; сроки" in "\n".join(output["chunks"])


def test_s21_sources_and_rendering_whitespace_does_not_invent_empty_response(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["items"][0]["text"] = " " * 5000
    text = "\n".join(presentation(store, job, result)["chunks"])
    assert "empty response" not in text


def prepared_delivery(store, job, *, chunks=None):
    result = sourced_result(store, job)
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    output = presentation(store, job, result)
    function = getattr(store, "prepare_delivery", None)
    assert callable(function), "Delivery requires a durable immutable plan in the original job"
    return function(job["job_id"], generation=job["generation"], chunks=chunks or output["chunks"],
        parse_mode="HTML", detail="normal", analysis_partial=False)


def delivery_intent(store, job, part=0):
    function = getattr(store, "begin_delivery_attempt", None)
    assert callable(function), "Every physical send must first commit its intent"
    return function(job["job_id"], generation=job["generation"], part_index=part)


def delivery_receipt(store, job, attempt, *, outcome="confirmed", message_id=700):
    function = getattr(store, "settle_delivery_attempt", None)
    assert callable(function), "A receipt must settle the exact persisted send intent"
    return function(job["job_id"], generation=job["generation"], part_index=0, attempt_id=attempt["attempt_id"],
        outcome=outcome, message_id=message_id if outcome == "confirmed" else None, chat_id="100" if outcome == "confirmed" else None)


def test_s22_delivery_receipts_plan_intent_and_receipt_are_durable_and_scoped(manifest_job):
    store, job = manifest_job
    plan = prepared_delivery(store, job)
    assert plan["destination"] == "100" and plan["outcome"] == "prepared"
    intent = delivery_intent(store, job)
    saved = store.get_job(job["job_id"])
    assert saved["delivery"]["parts"][0]["attempts"][-1]["attempt_id"] == intent["attempt_id"]
    assert saved["delivery"]["outcome"] == "unknown" and not store.conn.in_transaction
    delivery_receipt(store, job, intent)
    confirmed = store.get_job(job["job_id"])["delivery"]
    assert confirmed["outcome"] == "confirmed" and confirmed["parts"][0]["attempts"][0]["message_id"] == 700
    assert store.get_job(job["job_id"])["result"] == saved["result"]
    assert not store.conn.in_transaction


def test_s22_delivery_receipts_unsettled_intent_prevents_a_second_send(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    delivery_intent(store, job)
    with pytest.raises(ValueError, match="unknown_delivery"):
        delivery_intent(store, job)
    assert len(store.get_job(job["job_id"])["delivery"]["parts"][0]["attempts"]) == 1


def test_s22_delivery_receipts_changed_plan_cannot_replace_send_history(manifest_job):
    store, job = manifest_job
    plan = prepared_delivery(store, job)
    assert store.prepare_delivery(job["job_id"], generation=job["generation"], chunks=[p["text"] for p in plan["parts"]],
        parse_mode="HTML", detail="normal", analysis_partial=False) == plan
    delivery_intent(store, job)
    with pytest.raises(ValueError, match="delivery_plan_conflict"):
        store.prepare_delivery(job["job_id"], generation=job["generation"], chunks=["Другой ответ"],
            parse_mode="HTML", detail="normal", analysis_partial=False)


def test_s22_delivery_receipts_receipt_commit_failure_preserves_unknown_intent(manifest_job, monkeypatch):
    store, job = manifest_job
    prepared_delivery(store, job)
    intent = delivery_intent(store, job)
    def fail(*args):
        raise RuntimeError("receipt storage fault")
    monkeypatch.setattr(store, "_sync_intermediate_no_tx", fail)
    with pytest.raises(RuntimeError, match="receipt storage fault"):
        delivery_receipt(store, job, intent)
    saved = store.get_job(job["job_id"])["delivery"]
    assert saved["outcome"] == "unknown" and saved["parts"][0]["attempts"][0]["outcome"] == "intent"


def deliver(store, job, sender, **kwargs):
    function = getattr(analysis, "deliver_analysis_result", None)
    assert callable(function), "Prepared analysis must use tracked single-attempt delivery"
    return function(store, job["job_id"], generation=job["generation"], token="fixture-token", send_func=sender,
        access_provider=kwargs.pop("access_provider", lambda: {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}), **kwargs)


def test_s22_delivery_receipts_sender_observes_committed_intent_and_original_destination(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls = []
    def send(token, chat_id, text, parse_mode, *, timeout_seconds):
        saved = store.get_job(job["job_id"])["delivery"]
        assert saved["outcome"] == "unknown" and not store.conn.in_transaction
        assert chat_id == "100" and parse_mode == "HTML"
        calls.append(text)
        return {"message_id": 701, "chat": {"id": 100}}
    assert deliver(store, job, send)["outcome"] == "confirmed"
    assert deliver(store, job, send)["outcome"] == "confirmed"
    assert len(calls) == 1 and store.get_job(job["job_id"])["llm_calls"] == 0


@pytest.mark.parametrize("failure", [TimeoutError("lost response"), TelegramApiError("unverified gateway error")])
def test_s22_delivery_receipts_unknown_send_is_not_retried(manifest_job, failure):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise failure
    assert deliver(store, job, send)["outcome"] == "unknown"
    assert deliver(store, job, send)["outcome"] == "unknown"
    assert calls == [1]


def test_s22_delivery_receipts_only_explicit_flood_refusal_is_retried(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls, sleeps = [], []
    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TelegramApiError("flood refusal", request_outcome="refused", error_code=429, retry_after=7)
        return {"message_id": 702, "chat": {"id": 100}}
    assert deliver(store, job, send, sleep_func=sleeps.append)["outcome"] == "confirmed"
    assert len(calls) == 2 and len(sleeps) == 1 and 6 < sleeps[0] <= 7
    assert len(store.get_job(job["job_id"])["delivery"]["parts"][0]["attempts"]) == 2


def test_s22_delivery_receipts_acl_is_rechecked_between_parts(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job, chunks=["Первая часть", "Вторая часть"])
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        access["users"] = frozenset()
        return {"message_id": 703, "chat": {"id": 100}}
    output = deliver(store, job, send, access_provider=lambda: access)
    assert output["outcome"] == "partial" and output["error"] == "forbidden" and calls == [1]
    assert output["parts"][1]["attempts"] == []


def test_s22_delivery_receipts_default_sender_uses_one_shared_transport_attempt(manifest_job, monkeypatch):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls = []
    def raw(token, method, payload, *, timeout_seconds):
        calls.append((method, payload, timeout_seconds))
        assert store.get_job(job["job_id"])["delivery"]["outcome"] == "unknown"
        return {"message_id": 704, "chat": {"id": 100}}
    monkeypatch.setattr(analysis.bridge, "shared_api_call", raw)
    assert deliver(store, job, None)["outcome"] == "confirmed"
    assert len(calls) == 1 and calls[0][0] == "sendMessage"
    assert calls[0][1]["disable_web_page_preview"] is True and calls[0][1]["chat_id"] == "100"


def test_s22_delivery_receipts_crash_after_send_leaves_unknown_and_no_resend(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls = []
    def crash(*args, **kwargs):
        calls.append(1)
        raise KeyboardInterrupt("crash after remote send")
    with pytest.raises(KeyboardInterrupt):
        deliver(store, job, crash)
    assert analysis.analysis_status_view(store.get_job(job["job_id"]))["delivery"] == "unknown"
    assert deliver(store, job, crash)["outcome"] == "unknown" and calls == [1]


@pytest.mark.parametrize("response", [None, {"message_id": True, "chat": {"id": 100}}, {"message_id": 10, "chat": {"id": 200}}])
def test_s22_delivery_receipts_malformed_or_foreign_receipt_is_unknown(manifest_job, response):
    store, job = manifest_job
    prepared_delivery(store, job)
    output = deliver(store, job, lambda *a, **k: response)
    assert output["outcome"] == "unknown" and len(output["parts"][0]["attempts"]) == 1


def test_s22_delivery_receipts_refusal_stops_but_explicit_resume_reuses_saved_result(manifest_job, monkeypatch):
    from telegram_connector.tests.test_analysis_manifest import NOW
    from telegram_connector.tests.test_analysis_lifecycle import CONFIG_DIR, update
    import tomllib
    store, job = manifest_job
    prepared_delivery(store, job, chunks=["Первая часть", "Вторая часть"])
    calls = []
    def initial(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 2:
            raise TelegramApiError("forbidden", request_outcome="refused", error_code=403)
        return {"message_id": 800, "chat": {"id": 100}}
    assert deliver(store, job, initial)["outcome"] == "partial"
    store.finish_job(job["job_id"], generation=job["generation"], status="completed", now=NOW)
    saved = store.get_job(job["job_id"])
    assert analysis.analysis_status_view(saved)["status"] == "completed"
    assert analysis.analysis_status_view(saved)["delivery"] == "partial"
    config = tomllib.loads((CONFIG_DIR / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    config["sync"]["batch_size"] = 2
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    analysis.control_analysis_job(store, config, update(text=f"/resume {job['job_id']}"), command="resume",
        job_id=job["job_id"], access=access, now=NOW, config_dir=CONFIG_DIR)
    resumed = store.claim_job(job["job_id"], max_running_jobs=1, now=NOW)
    def no_inference(*args, **kwargs):
        pytest.fail("Delivery resume must not rerun extraction")
    monkeypatch.setattr(analysis, "extract_analysis_batch", no_inference)
    def complete(*args, **kwargs):
        calls.append(args[2])
        return {"message_id": 801, "chat": {"id": 100}}
    assert deliver(store, resumed, complete, sleep_func=lambda _: None)["outcome"] == "confirmed"
    assert calls == ["Первая часть", "Вторая часть", "Вторая часть"]
    assert store.get_job(job["job_id"])["result"] == saved["result"]


def test_s22_delivery_receipts_flood_attempt_cap_is_cumulative(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls = []
    def refuse(*args, **kwargs):
        calls.append(1)
        raise TelegramApiError("flood", request_outcome="refused", error_code=429, retry_after=0)
    assert deliver(store, job, refuse, sleep_func=lambda _: None)["outcome"] == "failed"
    assert calls == [1, 1, 1]
    assert deliver(store, job, refuse)["outcome"] == "failed" and len(calls) == 3


def test_s22_delivery_receipts_deadline_does_not_allow_a_flood_retry(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    calls = []
    def refuse(*args, **kwargs):
        calls.append(1)
        raise TelegramApiError("flood", request_outcome="refused", error_code=429, retry_after=7)
    output = deliver(store, job, refuse, monotonic_func=lambda: 0, deadline=1,
        sleep_func=lambda _: pytest.fail("Cannot wait past the run deadline"))
    assert output["outcome"] == "failed" and output["error"] == "deadline_exceeded" and calls == [1]


def test_s22_delivery_receipts_revoked_acl_prevents_any_intent(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job)
    output = deliver(store, job, lambda *a, **k: pytest.fail("ACL must precede send"),
        access_provider=lambda: {"chats": frozenset(), "users": frozenset({"7"}), "usernames": frozenset()})
    assert output["outcome"] == "failed" and output["error"] == "forbidden"
    assert output["parts"][0]["attempts"] == []


def test_s22_delivery_receipts_cancellation_keeps_received_ack_and_stops_next_part(manifest_job):
    from telegram_connector.tests.test_analysis_manifest import NOW
    store, job = manifest_job
    prepared_delivery(store, job, chunks=["Первая часть", "Вторая часть"])
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        store.request_cancel(job["job_id"], now=NOW)
        return {"message_id": 802, "chat": {"id": 100}}
    with pytest.raises(InterruptedError):
        deliver(store, job, send)
    journal = store.get_job(job["job_id"])["delivery"]
    assert journal["parts"][0]["outcome"] == "confirmed" and journal["parts"][1]["attempts"] == [] and calls == [1]


def test_s22_delivery_receipts_part_cap_is_enforced_before_sending(manifest_job):
    store, job = manifest_job
    with pytest.raises(ValueError, match="delivery plan"):
        prepared_delivery(store, job, chunks=["Часть"] * 9)
    assert store.get_job(job["job_id"])["delivery"] == {}


@pytest.mark.parametrize("mutation", [None, "delete", "content"])
def test_s22_delivery_receipts_full_path_uses_verified_artifact_fixture(manifest_job, tmp_path, mutation):
    import hashlib
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    frozen_output_setting(store, job, lambda p: p["request"].update(detail="full"))
    path = f"scratch/telegram-connector-analytics/results/{job['job_id']}.md"
    artifact = tmp_path / "prepared.md"
    artifact.write_text("# Проверенный отчёт\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    store.conn.execute("UPDATE analysis_jobs SET full_path=?,full_hash=? WHERE job_id=?", (path, digest, job["job_id"]))
    verifications = []
    def verify(saved):
        verifications.append(1)
        if len(verifications) == 2 and mutation == "delete":
            artifact.unlink()
        if len(verifications) == 2 and mutation == "content":
            artifact.write_text("Другой отчёт", encoding="utf-8")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != saved["full_hash"]:
            raise ValueError("Artifact hash changed")
        return saved["full_path"]
    calls = []
    def send(token, chat, text, mode, **kwargs):
        calls.append((text, mode))
        return {"message_id": 803, "chat": {"id": 100}}
    output = deliver(store, job, send, artifact_verifier=verify)
    if mutation:
        assert output["outcome"] == "failed" and output["error"] == "artifact_unavailable" and calls == []
        assert output["parts"][0]["attempts"] == []
    else:
        assert output["outcome"] == "confirmed" and output["artifact_hash"] == digest and calls == [(path, None)]


def test_s22_delivery_receipts_receipt_failure_through_sender_does_not_retry(manifest_job, monkeypatch):
    store, job = manifest_job
    prepared_delivery(store, job)
    original = store._sync_intermediate_no_tx
    def fail_confirmed(job_id):
        if store.get_job(job_id)["delivery"]["outcome"] == "confirmed":
            raise RuntimeError("receipt commit fault")
        return original(job_id)
    monkeypatch.setattr(store, "_sync_intermediate_no_tx", fail_confirmed)
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        return {"message_id": 900, "chat": {"id": 100}}
    with pytest.raises(RuntimeError, match="receipt commit fault"):
        deliver(store, job, send)
    assert deliver(store, job, send)["outcome"] == "unknown" and calls == [1]


def test_s22_delivery_receipts_source_edit_between_parts_keeps_prior_receipt(manifest_job):
    store, job = manifest_job
    prepared_delivery(store, job, chunks=["Первая часть", "Вторая часть"])
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        store.conn.execute("UPDATE messages SET text='Новые условия' WHERE channel_id=22")
        return {"message_id": 901, "chat": {"id": 100}}
    with pytest.raises(ValueError, match="input_changed"):
        deliver(store, job, send)
    saved = store.get_job(job["job_id"])
    assert saved["status"] == "interrupted" and saved["delivery"]["outcome"] == "partial"
    assert saved["delivery"]["parts"][1]["attempts"] == [] and calls == [1]


def test_s22_delivery_receipts_real_unsettled_intent_blocks_control_resume(manifest_job):
    from datetime import datetime, timezone
    from telegram_connector.tests.test_analysis_lifecycle import CONFIG_DIR, update
    import tomllib
    store, job = manifest_job
    prepared_delivery(store, job)
    delivery_intent(store, job)
    now = datetime.now(timezone.utc)
    store.finish_job(job["job_id"], generation=job["generation"], status="interrupted", now=now)
    config = tomllib.loads((CONFIG_DIR / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
    with pytest.raises(analysis.AnalysisRejection, match="unknown_delivery"):
        analysis.control_analysis_job(store, config, update(text=f"/resume {job['job_id']}"), command="resume",
            job_id=job["job_id"], access=access, now=now, config_dir=CONFIG_DIR)


def test_s22_delivery_receipts_stale_generation_cannot_send_or_commit_receipt(manifest_job):
    from telegram_connector.analysis_store import StaleGenerationError
    store, job = manifest_job
    prepared_delivery(store, job)
    intent = delivery_intent(store, job)
    store.conn.execute("UPDATE analysis_jobs SET generation=generation+1 WHERE job_id=?", (job["job_id"],))
    with pytest.raises(StaleGenerationError):
        delivery_receipt(store, job, intent)
    with pytest.raises(StaleGenerationError):
        deliver(store, job, lambda *a, **k: pytest.fail("Stale generation cannot send"))


def test_s22_delivery_receipts_failed_intent_commit_prevents_network_call(manifest_job, monkeypatch):
    store, job = manifest_job
    prepared_delivery(store, job)
    def fail(*args):
        raise RuntimeError("intent commit fault")
    monkeypatch.setattr(store, "_sync_intermediate_no_tx", fail)
    with pytest.raises(RuntimeError, match="intent commit fault"):
        deliver(store, job, lambda *a, **k: pytest.fail("Network must follow committed intent"))
    assert store.get_job(job["job_id"])["delivery"]["parts"][0]["attempts"] == []


@pytest.mark.parametrize("cap_before", ["prepare", "intent"])
def test_s22_delivery_receipts_metadata_headroom_survives_global_byte_cap(manifest_job, cap_before):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    output = presentation(store, job, result)
    if cap_before == "intent":
        store.prepare_delivery(job["job_id"], generation=job["generation"], chunks=output["chunks"],
            parse_mode="HTML", detail="normal", analysis_partial=False)
    cap = store.storage_usage(job["job_id"])["allocated_bytes"] + 16
    frozen_output_setting(store, job, lambda p: p["settings"]["analysis"].update(max_intermediate_bytes=cap))
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        return {"message_id": 999999999999999999, "chat": {"id": 100}}
    assert deliver(store, job, send)["outcome"] == "confirmed"
    assert calls == [1] and store.storage_usage(job["job_id"])["allocated_bytes"] <= cap


def test_s22_delivery_receipts_large_text_at_byte_cap_delivers_fallback_without_losing_result(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["items"][0]["text"] = "Подробность " * 800
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    cap = store.storage_usage(job["job_id"])["allocated_bytes"] + 16
    frozen_output_setting(store, job, lambda p: p["settings"]["analysis"].update(max_intermediate_bytes=cap))
    calls = []
    def send(token, chat, text, mode, **kwargs):
        calls.append(text)
        return {"message_id": 950 + len(calls), "chat": {"id": 100}}
    journal = deliver(store, job, send)
    assert journal["outcome"] == "confirmed" and journal["analysis_partial"] is True
    assert "max_intermediate_bytes" in "\n".join(calls)
    assert "Проверенные утверждения не сформированы" in "\n".join(calls)
    assert store.get_job(job["job_id"])["result"] == result
    assert store.storage_usage(job["job_id"])["allocated_bytes"] <= cap


def test_s21_sources_and_rendering_non_channel_link_is_explicitly_unavailable(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.conn.execute("UPDATE channels SET channel_type='Chat',username=NULL WHERE channel_id=22")
    ref = next(s for s in verified(store, job, result)["items"][0]["sources"] if s["channel_id"] == 22)
    assert ref["url"] is None and ref["message_id"] == 1


def test_s21_sources_and_rendering_source_must_belong_to_resolved_job_scope(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    current = store.get_job(job["job_id"])
    del current["checkpoint"]["history"]["refs"]["@two"]
    store.save_checkpoint(job["job_id"], current["checkpoint"], generation=job["generation"], max_bytes=10000)
    with pytest.raises(ValueError, match="final_source_scope"):
        verified(store, job, result)


@pytest.mark.parametrize("kind", ["budget_stop", "empty_partial"])
def test_s21_sources_and_rendering_partial_without_claims_has_processed_sources(manifest_job, kind):
    store, job = manifest_job
    result = sourced_result(store, job)
    if kind == "budget_stop":
        result = store.record_budget_stop(job["job_id"], generation=job["generation"], reason="max_llm_calls")
        result["source_refs"] = [{"channel_id": 999, "message_id": 999, "url": "https://t.me/forged/999"}]
    else:
        result.update(items=[], partial=True, reasons=["incomplete"])
    output = verified(store, job, result)
    assert output["source_role"] == "processed_inputs"
    assert {(s["channel_id"], s["message_id"]) for s in output["sources"]} == {(11, 1), (22, 1)}
    assert all(s["quote"] is None and "forged" not in s["url"] for s in output["sources"])
    store.conn.execute("DELETE FROM messages WHERE channel_id=22")
    with pytest.raises(ValueError, match="input_changed"):
        verified(store, job, result)


@pytest.mark.parametrize("command", ["topics", "qa"])
@pytest.mark.parametrize("format", ["html", "markdown"])
def test_s38_insufficient_notice_preserves_supported_items_and_limits_completeness(manifest_job, command, format):
    store, job = manifest_job
    result = sourced_result(store, job)
    result["insufficient_information"] = True
    view = verified(store, job, result)
    job = copy.deepcopy(job)
    job["command"] = command
    def render(value):
        return "\n".join(analysis._analysis_html_chunks(job, value)) if format == "html" else analysis._full_analysis_markdown(job, value)
    text = render(view)
    assert "1250 EUR" in text
    assert "Недостаточно сведений для ответа." not in text
    assert ("Сведений недостаточно для полной сводки." if command == "topics" else "Сведений недостаточно для полного ответа.") in text
    empty = copy.deepcopy(view)
    empty["items"] = []
    assert "Недостаточно сведений для ответа." in render(empty)
