import copy
import json

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_budget import plan, setting
from telegram_connector.tests.test_analysis_transport import Response, transport_job
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, change_setting


CAPABILITY = {"model": "fixture-model", "context_window": 200, "max_output_tokens": 100, "source": "fixture"}


def payload(text="message", **kwargs):
    return {"model": "fixture-model", "instructions": "system and shared prefix", "input": text,
            "text": {"format": {"type": "json_schema", "name": "fixture", "schema": {"type": "object"}}},
            "max_output_tokens": 20, "store": False, **kwargs}


def prepare(store, job, batch, data, send, *, capabilities=CAPABILITY):
    function = getattr(analysis, "prepare_analysis_payload", None)
    assert callable(function), "Every AI stage needs an exact, durable preflight of its final input"
    return function(store, job["job_id"], batch["batch_id"], generation=job["generation"], payload=data,
                    capabilities=capabilities, api_key="fixture-key", deadline=100,
                    monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)


def test_s15_bounded_payloads_count_includes_instructions_schema_and_input(transport_job):
    store, job = transport_job
    setting(store, job, "max_input_tokens", 100)
    batch = plan(store, job)
    data = payload()
    sent = []

    def count(req, timeout):
        import json
        sent.append(json.loads(req.data))
        assert req.full_url.endswith("/input_tokens")
        return Response({"input_tokens": 101, "object": "response.input_tokens"})

    with pytest.raises(ValueError, match="max_input_tokens"):
        prepare(store, job, batch, data, count)
    assert sent == [{key: data[key] for key in ("model", "instructions", "input", "text")}]
    assert store.get_job(job["job_id"])["llm_calls"] == 0


def test_s15_bounded_payloads_preflight_cache_survives_reload_and_tracks_input_identity(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    sent = []

    def count(*args, **kwargs):
        sent.append(1)
        return Response({"input_tokens": 30})

    data = payload()
    before = copy.deepcopy(data)
    first = prepare(store, job, batch, data, count)
    second = prepare(type(store)(store.conn), job, batch, data, count)
    assert first == second and first["input_tokens"] == 30
    assert data == before and first["payload"] == data
    prepare(store, job, batch, payload("different message"), count)
    assert len(sent) == 2
    assert store.get_job(job["job_id"])["provider_requests"] == 2


def test_s15_bounded_payloads_input_plus_reserved_output_must_fit_verified_model(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    with pytest.raises(ValueError, match="model_context_limit"):
        prepare(store, job, batch, payload(), lambda *a, **k: Response({"input_tokens": 181}))
    assert store.get_job(job["job_id"])["llm_calls"] == 0


def build_payload(messages, context):
    return payload(json.dumps({"messages": messages, "context": context}, ensure_ascii=False))


def fake_count(req, timeout):
    data = json.loads(req.data)
    body = json.loads(data["input"])
    count = 20 + sum(len(item["message"]["text"]) for item in body["messages"] + body["context"])
    return Response({"input_tokens": count})


def batches(store, job, **kwargs):
    function = getattr(analysis, "iter_analysis_batches", None)
    assert callable(function), "The manifest must stream into counted batches without loading the whole period"
    return function(store, job["job_id"], generation=job["generation"], build_payload=build_payload,
                    capabilities=CAPABILITY, api_key="fixture-key", deadline=100,
                    monotonic_func=lambda: 0, urlopen_func=fake_count, sleep_func=lambda _: None, **kwargs)


def test_s15_bounded_payloads_history_splits_and_each_final_payload_is_counted(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 10), text="x" * 40)
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 3)
    change_setting(store, job, "analysis", "max_input_tokens", 100)
    build(store, job)
    output = list(batches(store, job))
    assert len(output) > 1
    refs = [item["input"] for batch in output for item in batch["messages"]]
    assert [ref["message_id"] for ref in refs] == list(range(1, 10))
    assert all(batch["input_tokens"] <= 100 and len(batch["messages"]) + len(batch["context"]) <= 3 for batch in output)
    assert all(json.loads(batch["payload"]["input"])["messages"] == batch["messages"] for batch in output)


def test_s15_bounded_payloads_long_source_fragments_preserve_offsets_and_content(manifest_job):
    store, job = manifest_job
    text = "Отказ 1250 EUR; не позже 30 дней. " * 8
    seed(store, 22, [1], text=text)
    change_setting(store, job, "analysis", "max_input_tokens", 100)
    build(store, job)
    output = list(batches(store, job))
    fragments = [item for batch in output for item in batch["messages"]]
    assert len(fragments) > 1
    assert all(store.get_batch(item["batch_id"])["payload"]["plan"].get("split_depth", 0) > 0 for item in output)
    assert "".join(item["message"]["text"] for item in fragments) == text
    assert all(item["input"]["message_id"] == 1 and len(item["input"]["content_digest"]) == 64 for item in fragments)
    assert fragments[0]["input"]["fragment_start"] == 0
    assert fragments[-1]["input"]["fragment_end"] == len(text)
    for left, right in zip(fragments, fragments[1:]):
        assert left["input"]["fragment_end"] == right["input"]["fragment_start"]


def test_s15_bounded_payloads_split_depth_stops_with_explicit_partial(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1], text="x" * 100)
    change_setting(store, job, "analysis", "max_input_tokens", 50)
    change_setting(store, job, "analysis", "max_split_depth", 1)
    build(store, job)
    assert list(batches(store, job)) == []
    result = store.get_job(job["job_id"])["result"]
    assert result["status"] == "partial" and result["reason"] == "max_split_depth"


def test_s15_bounded_payloads_tiny_batch_disables_overlap_and_resume_uses_count_cache(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1, 2, 3], text="x")
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 1)
    build(store, job)
    first = list(batches(store, job))
    requests = store.get_job(job["job_id"])["provider_requests"]
    second = list(batches(store, job))
    assert first == second
    assert len(first) == 3 and all(not batch["context"] for batch in first)
    assert store.get_job(job["job_id"])["provider_requests"] == requests


@pytest.mark.parametrize("value", [None, True, -1, "12"])
def test_s15_bounded_payloads_count_unavailable_never_falls_back_to_char_estimate(transport_job, value):
    store, job = transport_job
    batch = plan(store, job)
    with pytest.raises(ValueError, match="input_token_count_unavailable"):
        prepare(store, job, batch, payload(), lambda *a, **k: Response({"input_tokens": value}))
    assert store.get_job(job["job_id"])["llm_calls"] == 0


def test_s15_bounded_payloads_ocr_fragments_share_source_and_ordered_text_view(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1], text="Начало 12 EUR.")
    for ordinal, ocr in ((2, "Второй OCR; " * 10), (1, "Первый OCR; " * 10)):
        store.conn.execute("""INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,ocr_status,created_at,ocr_text)
                              VALUES (22,1,?,'photo','done','now',?)""", (ordinal, ocr))
    change_setting(store, job, "analysis", "max_input_tokens", 100)
    build(store, job)
    fragments = [item for batch in batches(store, job) for item in batch["messages"]]
    text = "".join(item["message"]["text"] for item in fragments)
    assert text == "Начало 12 EUR.\n[OCR 1]\n" + "Первый OCR; " * 10 + "\n[OCR 2]\n" + "Второй OCR; " * 10
    assert all(item["input"]["message_id"] == 1 for item in fragments)
    assert fragments[-1]["input"]["fragment_end"] == len(text)


@pytest.mark.parametrize("kind", ["expand_query", "extract", "link", "merge", "synthesize"])
def test_s15_bounded_payloads_all_ai_stages_use_same_complete_preflight(transport_job, kind):
    store, job = transport_job
    batch = plan(store, job, key=f"{kind}/0", kind=kind)
    ready = prepare(store, job, batch, payload(), lambda *a, **k: Response({"input_tokens": 20}))
    assert ready["input_tokens"] == 20
    assert store.get_job(job["job_id"])["provider_requests"] == 1


def test_s15_bounded_payloads_provider_cap_stops_stream_with_explicit_partial(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1, 2], text="x")
    change_setting(store, job, "analysis", "max_provider_requests", 1)
    build(store, job)
    assert list(batches(store, job)) == []
    assert store.get_job(job["job_id"])["result"]["reason"] == "max_provider_requests"


def test_s15_bounded_payloads_overlap_is_context_not_a_second_selected_message(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 8), text="x")
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 3)
    build(store, job)
    output = list(batches(store, job))
    assert [m["input"]["message_id"] for b in output for m in b["messages"]] == list(range(1, 8))
    assert all(m["input"]["role"] == "context" for b in output for m in b["context"])
    assert [m["input"]["message_id"] for m in output[1]["context"]] == [m["input"]["message_id"] for m in output[0]["messages"][-2:]]
    assert all(len(b["messages"]) + len(b["context"]) <= 3 for b in output)


def test_s15_bounded_payloads_char_guard_fragments_without_truncation(manifest_job):
    store, job = manifest_job
    text = "1234567890"
    seed(store, 22, [1], text=text)
    change_setting(store, job, "digest_ai", "message_block_max_chars", 3)
    build(store, job)
    output = list(batches(store, job))
    assert "".join(m["message"]["text"] for b in output for m in b["messages"]) == text
    assert all(sum(len(m["message"]["text"]) for m in b["messages"] + b["context"]) <= 3 for b in output)


def test_s15_bounded_payloads_inference_uses_exact_counted_input_and_returns_attempt_identity(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    function = getattr(analysis, "request_counted_analysis_payload", None)
    assert callable(function), "Inference must use the checked payload and return its exact receipt identity"
    sent = []

    def send(req, timeout):
        data = json.loads(req.data)
        sent.append(data)
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 30})
        return Response({"usage": {"input_tokens": 30, "output_tokens": 5}, "output": []})

    result = function(store, job["job_id"], batch["batch_id"], generation=job["generation"], payload=payload(),
                      capabilities=CAPABILITY, api_key="fixture-key", deadline=100,
                      monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)
    assert len(sent) == 2
    assert all(sent[0][key] == sent[1][key] for key in ("model", "input", "instructions", "text"))
    attempt = store.get_batch(batch["batch_id"])["attempts"][-1]
    assert result["attempt_id"] == attempt["attempt_id"] and attempt["request_kind"] == "inference"
    assert result["input_tokens"] == 30 and store.get_job(job["job_id"])["used_tokens"] == 35


def test_s15_bounded_payloads_resume_skips_completed_outputs_and_preserves_context(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1, 2, 3], text="x")
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 1)
    build(store, job)
    output = list(batches(store, job))
    store.complete_batch(job["job_id"], output[0]["batch_id"], generation=job["generation"], records=[], inputs=[])
    requests = store.get_job(job["job_id"])["provider_requests"]
    resumed = list(batches(store, job))
    assert [m["input"]["message_id"] for b in resumed for m in b["messages"]] == [2, 3]
    assert store.get_job(job["job_id"])["provider_requests"] == requests


def test_s15_bounded_payloads_minimal_prompt_error_is_configuration_failure(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1], text="x")
    change_setting(store, job, "analysis", "max_input_tokens", 10)
    build(store, job)
    with pytest.raises(ValueError, match="minimal_payload_does_not_fit"):
        list(batches(store, job))
    assert store.get_job(job["job_id"])["llm_calls"] == 0


@pytest.mark.parametrize("capabilities", [None, {}, {**CAPABILITY, "model": "other"}, {**CAPABILITY, "max_output_tokens": 1}])
def test_s15_bounded_payloads_unverified_or_incompatible_model_fails_before_count(transport_job, capabilities):
    store, job = transport_job
    batch = plan(store, job)
    with pytest.raises(ValueError, match="model_(capability_unverified|output_limit)"):
        prepare(store, job, batch, payload(), lambda *a, **k: pytest.fail("No provider request allowed"), capabilities=capabilities)
    assert store.get_job(job["job_id"])["provider_requests"] == 0


def test_s15_bounded_payloads_first_yield_does_not_materialize_remaining_history(transport_job, monkeypatch):
    store, job = transport_job
    request = copy.deepcopy(store.get_job(job["job_id"])["request"])
    request["settings"]["digest_ai"]["messages_per_ai_pass"] = 2
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(request), job["job_id"]))
    consumed = []

    def source(*args, **kwargs):
        for number in range(1, 4001):
            consumed.append(number)
            yield {"message": {"channel_id": 22, "message_id": number, "text": "x", "media": []},
                   "input": {"channel_id": 22, "message_id": number, "date_utc": "2026-10-12T00:00:00+00:00",
                             "fragment_start": 0, "fragment_end": 0, "content_digest": "a" * 64}}

    monkeypatch.setattr(analysis, "iter_analysis_manifest_messages", source)
    stream = batches(store, job)
    first = next(stream)
    assert len(first["messages"]) == 2
    assert consumed == [1, 2, 3]  # One lookahead, no eager read of the period.
    stream.close()
