import io
import json
from email.message import Message
from urllib import error
from urllib import request
from urllib.response import addinfourl

import pytest

from telegram_shared import openai_api as api
from telegram_connector import telegram_analysis as analysis
from telegram_connector import telegram_history_client as history
from common import sqlite as common_sqlite
from telegram_connector.tests.test_analysis_budget import admit_budget_job, plan


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.data).encode()


def post(payload=None, **kwargs):
    function = getattr(api, "post_json", None)
    assert callable(function), "Responses and input_tokens need one bounded transport with physical-attempt hooks"
    return function(payload or {"model": "fixture-model"}, "fixture-key", timeout_seconds=120,
                    retry_attempts=3, retry_backoff_seconds=1, **kwargs)


def test_s14_transport_accounting_hook_brackets_every_physical_retry():
    events = []

    def send(req, timeout):
        events.append(("send", req.full_url))
        if len([e for e in events if e[0] == "send"]) == 1:
            raise error.HTTPError(req.full_url, 503, "overloaded", Message(), io.BytesIO(b'{}'))
        return Response({"object": "response.input_tokens", "input_tokens": 12})

    result = post(endpoint="input_tokens", urlopen_func=send, sleep_func=lambda _: None, random_func=lambda: 0,
                  before_attempt=lambda n: events.append(("before", n)) or f"attempt-{n}",
                  after_attempt=lambda identity, response, exc, latency: events.append(("after", identity, exc is None)))
    assert result.response["input_tokens"] == 12
    assert [e[0] for e in events] == ["before", "send", "after", "before", "send", "after"]
    assert events[1][1] == "https://api.openai.com/v1/responses/input_tokens"
    assert events[2][1:] == ("attempt-1", False)
    assert events[-1][1:] == ("attempt-2", True)


def test_s14_transport_accounting_denied_hook_and_endpoint_do_not_send():
    sends = []
    for endpoint in ("https://other.invalid", "responses/../files"):
        with pytest.raises(ValueError, match="endpoint"):
            post(endpoint=endpoint, urlopen_func=lambda *a, **k: sends.append(1))

    def deny(_):
        raise ValueError("budget exhausted")

    with pytest.raises(ValueError, match="budget exhausted"):
        post(endpoint="responses", before_attempt=deny, urlopen_func=lambda *a, **k: sends.append(1))
    assert sends == []


def test_s14_transport_accounting_deadline_bounds_timeout_and_retry_wait():
    timeouts, delays = [], []

    def send(req, timeout):
        timeouts.append(timeout)
        raise error.HTTPError(req.full_url, 503, "overloaded", Message(), io.BytesIO(b'{}'))

    with pytest.raises(api.OpenAIRequestError, match="deadline"):
        post(endpoint="responses", deadline=0.5, monotonic_func=lambda: 0,
             urlopen_func=send, sleep_func=delays.append, random_func=lambda: 0)
    assert timeouts == [0.5]
    assert delays == []


@pytest.fixture
def transport_job():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    try:
        yield admit_budget_job(conn)
    finally:
        conn.close()


def call_analysis(store, job, batch, *, endpoint="responses", send, **kwargs):
    function = getattr(analysis, "request_analysis_json", None)
    assert callable(function), "Analytics must bind each physical transport attempt to its durable job ledger"
    return function(store, job["job_id"], batch["batch_id"], generation=job["generation"],
                    endpoint=endpoint, payload={"model": "fixture-model", "input": "private query and history",
                    **({"max_output_tokens": 20} if endpoint == "responses" else {})},
                    api_key="fixture-secret", counted_input_tokens=10, deadline=100,
                    monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None, **kwargs)


def test_s14_transport_accounting_retry_uses_two_durable_reservations_and_safe_logs(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    calls = []

    def send(req, timeout):
        current = store.get_job(job["job_id"])
        calls.append(current)
        if len(calls) == 1:
            raise TimeoutError("private query and history fixture-secret")
        return Response({"id": "resp_fixture", "usage": {"input_tokens": 10, "output_tokens": 5,
                         "input_tokens_details": {"cached_tokens": 4}}})

    call_analysis(store, job, batch, send=send)
    assert [c["provider_requests"] for c in calls] == [1, 2]
    saved = store.get_job(job["job_id"])
    assert (saved["llm_calls"], saved["used_tokens"], saved["reserved_tokens"]) == (2, 15, 30)
    rows = store.conn.execute("SELECT job_id,batch_id,attempt_id,prompt_text,error FROM ai_usage_log").fetchall()
    assert len(rows) == 2
    assert all(tuple(r[:2]) == (job["job_id"], batch["batch_id"]) and r[2] and r[3] is None for r in rows)
    assert "private query" not in str([tuple(r) for r in rows])
    assert "fixture-secret" not in str([tuple(r) for r in rows])
    assert store.conn.execute("SELECT total_tokens FROM ai_usage_log ORDER BY id DESC LIMIT 1").fetchone()[0] == 15


def test_s14_transport_accounting_count_and_missing_usage_do_not_fabricate_zero_cost(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    call_analysis(store, job, batch, endpoint="input_tokens", send=lambda *a, **k: Response({"input_tokens": 10}))
    saved = store.get_job(job["job_id"])
    assert (saved["provider_requests"], saved["llm_calls"], saved["reserved_tokens"]) == (1, 0, 0)
    call_analysis(store, job, batch, send=lambda *a, **k: Response({"id": "without-usage"}))
    saved = store.get_job(job["job_id"])
    assert (saved["provider_requests"], saved["llm_calls"], saved["reserved_tokens"]) == (2, 1, 30)


def test_s14_transport_accounting_unknown_attempt_retains_latency_without_prompt(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    call_analysis(store, job, batch, send=lambda *a, **k: Response({"id": "no-usage"}))
    row = store.conn.execute("SELECT input_tokens,latency_ms,prompt_text FROM ai_usage_log").fetchone()
    assert row[0] is None and row[1] is not None and row[1] >= 0 and row[2] is None


def test_s14_transport_accounting_deadline_is_rechecked_after_reservation():
    clock, sends, receipts = [0], [], []

    def reserve(_):
        clock[0] = 2  # A slow durable reservation exhausted the remaining TTL.
        return "reserved"

    with pytest.raises(api.OpenAIRequestError, match="deadline"):
        post(endpoint="responses", deadline=1, monotonic_func=lambda: clock[0], before_attempt=reserve,
             after_attempt=lambda *args: receipts.append(args),
             urlopen_func=lambda *a, **k: sends.append(1) or Response({}))
    assert sends == []
    assert len(receipts) == 1


def test_s14_transport_accounting_nonretryable_failure_and_denied_retry_keep_exact_attempts(transport_job):
    store, job = transport_job
    batch = plan(store, job)

    def forbidden(req, timeout):
        raise error.HTTPError(req.full_url, 401, "unauthorized", Message(), io.BytesIO(b'{}'))

    with pytest.raises(api.OpenAIRequestError):
        call_analysis(store, job, batch, send=forbidden)
    assert store.get_job(job["job_id"])["provider_requests"] == 1
    from telegram_connector.tests.test_analysis_budget import setting
    setting(store, job, "max_llm_calls", 2)
    sends = []

    def timeout(*a, **k):
        sends.append(1)
        raise TimeoutError("fixture")

    with pytest.raises(ValueError, match="max_llm_calls"):
        call_analysis(store, job, batch, send=timeout)
    assert sends == [1]
    assert store.get_job(job["job_id"])["provider_requests"] == 2


def test_s14_transport_accounting_default_sender_does_not_follow_redirects(monkeypatch):
    destinations = []

    def memory_https_open(self, req):
        destinations.append(req.full_url)
        headers = Message()
        headers["Location"] = "https://other.invalid/collect"
        response = addinfourl(io.BytesIO(b'{}'), headers, req.full_url, code=302 if len(destinations) == 1 else 200)
        response.msg = "fixture"
        return response

    monkeypatch.setattr(request.HTTPSHandler, "https_open", memory_https_open)
    with pytest.raises(api.OpenAIRequestError) as caught:
        post(endpoint="responses", before_attempt=lambda _: "first")
    assert caught.value.status_code == 302
    assert destinations == ["https://api.openai.com/v1/responses"]
