import json
import copy
import sqlite3
from types import SimpleNamespace

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_evidence
from telegram_connector import telegram_history_client as history
from telegram_connector.tests.test_analysis_manifest import manifest_job, DATE
from telegram_connector.tests.test_analysis_manifest import seed, build, messages, change_setting
from telegram_connector.tests.test_analysis_evidence import document
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response


def records_fixture(store, job, values):
    for index, value in enumerate(values, 1):
        seed(store, 22, [index], text=value.get("text", value["claim"]))
        if "reply_to" in value:
            store.conn.execute("UPDATE messages SET raw_json=? WHERE channel_id=22 AND message_id=?",
                (json.dumps({"reply_to": value["reply_to"]}), index))
    build(store, job)
    fragments = messages(store, job)
    result = []
    for index, (value, fragment) in enumerate(zip(values, fragments)):
        fragment["input"]["fragment_end"] = len(fragment["message"]["text"])
        batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key=f"fixture-extract/{index}", kind="extract", payload={})
        data = document({"messages": [fragment]})
        data["records"][0].update({key: copy.deepcopy(item) for key, item in value.items() if key not in {"text", "reply_to"}})
        records = analysis_evidence.validate_evidence_records(store, job["job_id"], batch["batch_id"], generation=job["generation"], document=data, fragments=[fragment])
        store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=records, inputs=[fragment["input"]])
        result.append(records[0])
    return result


def candidates(store, job):
    function = getattr(analysis, "iter_relation_candidates", None)
    assert callable(function), "Cross-batch links require bounded, indexed topic/entity/source candidates"
    return list(function(store, job["job_id"], generation=job["generation"]))


def test_s18_record_relations_semantic_candidates_cross_batches_without_reply(manifest_job):
    store, job = manifest_job
    records = records_fixture(store, job, [
        {"record_type": "question", "claim": "Как открыть счёт?", "entities": ["Банк Uno"], "topic_key": "счёт"},
        {"record_type": "answer", "claim": "Принесите паспорт и NIE.", "entities": [" банк uno "], "topic_key": "документы"},
        {"claim": "Прошла выставка.", "entities": ["галерея"], "topic_key": "культура"}])
    pairs = candidates(store, job)
    assert len(pairs) == 1
    assert {pairs[0]["left"]["record_id"], pairs[0]["right"]["record_id"]} == {r["record_id"] for r in records[:2]}
    assert not store.conn.in_transaction


def test_s18_record_relations_candidate_caps_are_visible_and_pairs_unique(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Событие {n}", "entities": ["Банк"], "topic_key": "Банк"} for n in range(8)])
    change_setting(store, job, "analysis", "relation_candidates_per_record", 2)
    change_setting(store, job, "analysis", "max_relation_pairs", 3)
    pairs = candidates(store, job)
    identities = [(v["left"]["record_id"], v["right"]["record_id"]) for v in pairs]
    assert len(identities) == 3 and len(set(identities)) == 3
    assert store.get_job(job["job_id"])["checkpoint"]["relations"]["limited"] is True


def link(store, job, sender):
    function = getattr(analysis, "link_analysis_records", None)
    assert callable(function), "Candidates must be checked semantically through the counted job ledger"
    return function(store, job["job_id"], generation=job["generation"], api_key="fixture-key",
        capabilities={"model": "fixture-model", "context_window": 16000, "max_output_tokens": 1200, "source": "fixture"},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=sender, sleep_func=lambda _: None)


def link_sender(relation, *, forge=False):
    def send(req, timeout):
        request = json.loads(req.data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        data = json.loads(request["input"].split("Кандидаты: ", 1)[1])
        decisions = [{"left_id": "forged" if forge else pair[0], "right_id": pair[1], "relation": relation,
                      "reason": "Опирается на содержание обеих записей."} for pair in data["pairs"]]
        return Response(model_response({"decisions": decisions, "saturated": False}))
    return send


@pytest.mark.parametrize("relation", ["complements", "contradicts", "answers", "unrelated"])
def test_s18_record_relations_semantic_disposition_keeps_both_sources(manifest_job, relation):
    store, job = manifest_job
    records = records_fixture(store, job, [
        {"record_type": "question", "claim": "Какие документы нужны?", "entities": ["Банк"], "topic_key": "счёт"},
        {"record_type": "answer", "claim": "Нужен паспорт.", "entities": ["Банк"], "topic_key": "счёт"}])
    result = link(store, job, link_sender(relation))
    assert result["partial"] is False
    saved = store.conn.execute("SELECT payload_json FROM analysis_batches WHERE job_id=? AND kind='link' AND state='succeeded'", (job["job_id"],)).fetchall()
    decisions = [d for row in saved for d in json.loads(row[0])["output"]["decisions"]]
    assert len(decisions) == 1 and decisions[0]["relation"] == relation
    assert {decisions[0]["left_id"], decisions[0]["right_id"]} == {r["record_id"] for r in records}
    assert store.get_job(job["job_id"])["evidence_records"] == 2
    before = store.get_job(job["job_id"])["provider_requests"]
    assert link(store, job, lambda *a, **k: pytest.fail("Completed link must resume without a provider request"))["partial"] is False
    assert store.get_job(job["job_id"])["provider_requests"] == before


def test_s18_record_relations_forged_pair_is_not_saved(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Первое событие", "topic_key": "Банк"}, {"claim": "Второе событие", "topic_key": "Банк"}])
    change_setting(store, job, "analysis", "max_repair_attempts", 0)
    assert link(store, job, link_sender("duplicate", forge=True))["partial"] is True
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='link' AND state='succeeded'").fetchone()[0] == 0


def test_s18_record_relations_overlap_duplicate_uses_no_inference_and_preserves_leafs(manifest_job):
    store, job = manifest_job
    records = records_fixture(store, job, [{"claim": "Срок подачи 30 дней.", "topic_key": "срок"}])
    batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key="overlap-extract", kind="extract", payload={})
    duplicate = {**records[0], "record_id": "overlap-copy"}
    store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=[duplicate], inputs=[])
    assert link(store, job, lambda *a, **k: pytest.fail("Technical duplicates need no LLM"))["partial"] is False
    saved = store.conn.execute("SELECT payload_json FROM analysis_batches WHERE kind='link' AND state='succeeded'").fetchone()
    assert json.loads(saved[0])["output"]["decisions"][0]["relation"] == "duplicate"
    assert store.get_job(job["job_id"])["evidence_records"] == 2


def test_s18_record_relations_source_edit_interrupts_before_link_inference(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    store.conn.execute("UPDATE messages SET text='Изменено' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match="input_changed"):
        link(store, job, lambda *a, **k: pytest.fail("Changed sources must be checked before inference"))
    assert store.get_job(job["job_id"])["status"] == "interrupted"


@pytest.mark.parametrize("peer,expected", [(None, 1), (22, 1), (11, 0)])
def test_s18_record_relations_reply_is_additional_scoped_candidate_signal(manifest_job, peer, expected):
    store, job = manifest_job
    reply = {"reply_to_msg_id": 1}
    if peer is not None:
        reply["reply_to_peer_id"] = {"channel_id": peer}
    records_fixture(store, job, [
        {"claim": "Как оформить?", "entities": [], "topic_key": None},
        {"claim": "Пришлите заявление.", "entities": [], "topic_key": None, "reply_to": reply}])
    assert len(candidates(store, job)) == expected


def test_s18_record_relations_cross_peer_header_does_not_invent_same_channel_reply(manifest_job):
    store, job = manifest_job
    message = SimpleNamespace(id=9, message="Ответ из другого peer.", date=history.datetime.fromisoformat(DATE),
        reply_to=SimpleNamespace(reply_to_msg_id=5, reply_to_peer_id=SimpleNamespace(channel_id=11)))
    history.upsert_message(store.conn, SimpleNamespace(id=22), message, None, None, None, None)
    raw = json.loads(store.conn.execute("SELECT raw_json FROM messages WHERE channel_id=22 AND message_id=9").fetchone()[0])
    assert raw["reply_to"].get("reply_to_peer_id") == {"channel_id": 11}
    payload, _ = analysis_evidence._analysis_message_snapshot(store, 22, 9, 67108864)
    assert payload["reply_to_message_id"] is None


def test_s18_record_relations_large_input_splits_and_counts_every_child(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Событие {n}", "topic_key": "Банк"} for n in range(3)])
    sent = []
    delegate = link_sender("unrelated")
    def sender(req, timeout):
        request = json.loads(req.data)
        data = json.loads(request["input"].split("Кандидаты: ", 1)[1])
        sent.append((req.full_url.endswith("input_tokens"), len(data["pairs"])))
        if req.full_url.endswith("input_tokens") and len(data["pairs"]) > 1:
            return Response({"input_tokens": 20000})
        return delegate(req, timeout)
    assert link(store, job, sender)["partial"] is False
    assert len([entry for entry in sent if not entry[0]]) == 3
    assert all(size == 1 for count, size in sent if not count)
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='link' AND state='skipped'").fetchone()[0] > 0


@pytest.mark.parametrize("outcome", ["refused", "incomplete", "saturated"])
def test_s18_record_relations_incomplete_output_is_explicit_partial(manifest_job, outcome):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        if outcome == "refused":
            return Response({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]})
        if outcome == "incomplete":
            return Response({"status": "incomplete", "output": []})
        return Response(model_response({"decisions": [], "saturated": True}))
    assert link(store, job, sender)["partial"] is True
    saved = store.conn.execute("SELECT state,payload_json FROM analysis_batches WHERE kind='link'").fetchone()
    assert saved[0] == "failed" and json.loads(saved[1])["outcome"] == outcome


def test_s36_single_pair_declared_saturation_gets_one_bounded_repair(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Планируется новый тариф.", "topic_key": "тариф"},
        {"claim": "Этот план отменён.", "topic_key": "тариф"}])
    requests = []
    def sender(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        requests.append(json.loads(req.data))
        if len(requests) == 1:
            return Response(model_response({"decisions": [], "saturated": True}))
        pairs = json.loads(requests[0]["input"].split("Кандидаты: ", 1)[1])["pairs"]
        return Response(model_response({"decisions": [{"left_id": left, "right_id": right,
            "relation": "contradicts", "reason": "План отменён."} for left, right in pairs], "saturated": False}))
    assert link(store, job, sender)["partial"] is False
    assert len(requests) == 2 and store.get_job(job["job_id"])["llm_calls"] == 2
    assert "relation_saturated" in requests[1]["input"]


def test_s18_record_relations_repair_is_counted_and_bounded(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    inference = 0
    def sender(req, timeout):
        nonlocal inference
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        inference += 1
        return Response(model_response({"wrong": "shape"}))
    assert link(store, job, sender)["partial"] is True
    assert inference == 2 and store.get_job(job["job_id"])["llm_calls"] == 2
    assert store.get_job(job["job_id"])["provider_requests"] == 4


def test_s18_record_relations_budget_stop_and_pair_cap_are_partial(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": f"Событие {n}", "topic_key": "Банк"} for n in range(4)])
    change_setting(store, job, "analysis", "max_relation_pairs", 1)
    assert link(store, job, link_sender("unrelated"))["partial"] is True
    # A new run respects the same global ledger, rather than resetting counters.
    change_setting(store, job, "analysis", "max_llm_calls", 1)
    store.conn.execute("DELETE FROM analysis_batches WHERE kind='link'")
    assert link(store, job, lambda *a, **k: pytest.fail("Budget must be reserved before network"))["partial"] is True
    assert store.get_job(job["job_id"])["checkpoint"]["budget_stop"]["reason"] == "max_llm_calls"


def test_s18_record_relations_source_edit_after_inference_keeps_cost_and_rejects_output(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    delegate = link_sender("contradicts")
    def sender(req, timeout):
        response = delegate(req, timeout)
        if not req.full_url.endswith("input_tokens"):
            store.conn.execute("UPDATE messages SET text='Изменено' WHERE channel_id=22 AND message_id=1")
        return response
    with pytest.raises(ValueError, match="input_changed"):
        link(store, job, sender)
    saved = store.get_job(job["job_id"])
    assert saved["status"] == "interrupted" and saved["llm_calls"] == 1 and saved["used_tokens"] > 0
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='link' AND state='succeeded'").fetchone()[0] == 0


def test_s18_record_relations_output_commit_failure_keeps_provider_receipt(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{"claim": "Срок 30 дней.", "topic_key": "срок"}, {"claim": "Срок 60 дней.", "topic_key": "срок"}])
    store.conn.execute("""CREATE TEMP TRIGGER refuse_link_output BEFORE UPDATE ON analysis_batches
        WHEN NEW.kind='link' AND NEW.state='succeeded' BEGIN SELECT RAISE(ABORT,'fixture-output-write'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="fixture-output-write"):
        link(store, job, link_sender("contradicts"))
    saved = store.get_job(job["job_id"])
    assert saved["llm_calls"] == 1 and saved["provider_requests"] == 2 and saved["used_tokens"] > 0
    assert saved["evidence_records"] == 2 and saved["reserved_tokens"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_batches WHERE kind='link' AND state='succeeded'").fetchone()[0] == 0


@pytest.mark.parametrize("reply_id", [None, 5])
def test_s18_record_relations_reply_header_survives_producer_database_manifest(manifest_job, reply_id):
    store, job = manifest_job
    message = SimpleNamespace(id=9, message="Подайте заявление в течение 30 дней.",
        date=history.datetime.fromisoformat(DATE),
        reply_to=None if reply_id is None else SimpleNamespace(reply_to_msg_id=reply_id))
    history.upsert_message(store.conn, SimpleNamespace(id=22), message, None, None, None, None)
    saved = json.loads(store.conn.execute("SELECT raw_json FROM messages WHERE channel_id=22 AND message_id=9").fetchone()[0])
    if reply_id is not None:
        assert saved.get("reply_to", {}).get("reply_to_msg_id") == reply_id
    payload, digest = analysis_evidence._analysis_message_snapshot(store, 22, 9, 67108864)
    assert payload["reply_to_message_id"] == reply_id and len(digest) == 64


@pytest.mark.parametrize('outcome', ['incomplete','saturated'])
def test_s38_relation_output_splits_preserve_pairs_usage_and_replay(manifest_job, outcome):
    store, job = manifest_job
    records_fixture(store, job, [{'claim':f'Срок {days} дней.', 'topic_key':'срок'} for days in (30,60,90)])
    sent = []
    def sender(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens':100})
        payload = json.loads(req.data)
        data = json.loads(payload['input'].split('Кандидаты: ',1)[1])
        sent.append(len(data['pairs']))
        if len(data['pairs']) > 1:
            if outcome == 'incomplete':
                return Response({'status':'incomplete','output':[], 'usage':{'input_tokens':100,'output_tokens':payload['max_output_tokens']}})
            return Response(model_response({'decisions':[], 'saturated':True}))
        return Response(model_response({'decisions':[{'left_id':a,'right_id':b,'relation':'complements','reason':'Уточнение.'} for a,b in data['pairs']], 'saturated':False}))
    result = link(store, job, sender)
    assert result['partial'] is False, 'Recover an oversized output by bounded pair splitting'
    assert len(list(store.iter_link_decisions(job['job_id']))) == 3
    assert sent == [3,1,2,1,1]
    calls = store.get_job(job['job_id'])['llm_calls']
    assert calls == 5, 'Parent inference attempts remain in cumulative ledger'
    assert link(store, job, sender)['partial'] is False
    assert store.get_job(job['job_id'])['llm_calls'] == calls
    assert len(sent) == calls, 'Replay must not request failed parents or successful children again'


@pytest.mark.parametrize('outcome', ['refused','incomplete_other','depth_limit'])
def test_s38_relation_output_split_stops_at_refusal_unknown_or_depth_limit(manifest_job, outcome):
    store, job = manifest_job
    records_fixture(store, job, [{'claim':f'Срок {days} дней.', 'topic_key':'срок'} for days in (30,60,90)])
    if outcome == 'depth_limit':
        change_setting(store, job, 'analysis', 'max_split_depth', 0)
    sent = []
    def sender(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens':100})
        payload = json.loads(req.data);sent.append(payload)
        if outcome == 'refused':
            return Response({'status':'completed','output':[{'type':'message','content':[{'type':'refusal'}]}]})
        return Response({'status':'incomplete','output':[], 'usage':{'input_tokens':100,'output_tokens':payload['max_output_tokens'] if outcome == 'depth_limit' else 20}})
    assert link(store, job, sender)['partial'] is True
    assert len(sent) == store.get_job(job['job_id'])['llm_calls'] == 1


def test_s38_relation_split_child_budget_stop_is_not_parent_schema_repair(manifest_job, monkeypatch):
    store, job = manifest_job
    records_fixture(store, job, [{'claim': f'Срок {days} дней.', 'topic_key': 'срок'} for days in (30, 60, 90)])
    reserve = store.reserve_provider_attempt

    def bounded_reserve(*args, **kwargs):
        if kwargs['request_kind'] == 'inference' and store.get_job(job['job_id'])['llm_calls']:
            raise analysis.BudgetExceededError('max_total_tokens')
        return reserve(*args, **kwargs)

    monkeypatch.setattr(store, 'reserve_provider_attempt', bounded_reserve)

    def sender(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        return Response({'status': 'incomplete', 'output': [],
                         'usage': {'input_tokens': 100, 'output_tokens': payload['max_output_tokens']}})

    assert link(store, job, sender)['partial'] is True
    current = store.get_job(job['job_id'])
    assert current['checkpoint']['budget_stop']['reason'] == 'max_total_tokens'
    assert current['llm_calls'] == 1
    batches = store.conn.execute("SELECT payload_json FROM analysis_batches WHERE job_id=? AND kind='link'", (job['job_id'],))
    assert all(not json.loads(row[0]).get('repair_attempts') for row in batches)
