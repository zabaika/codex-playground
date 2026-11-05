from datetime import datetime, timedelta, timezone

import pytest

from telegram_connector.tests.test_analysis_budget import budget_job, plan
from telegram_connector.tests.test_analysis_transport import call_analysis, Response


def test_phase_timings_use_monotonic_clock_and_keep_generation(budget_job):
    from telegram_connector.analysis_telemetry import PhaseRecorder

    store, job = budget_job
    clock = [0.0]
    wall = datetime.now(timezone.utc)
    with PhaseRecorder(store, job, monotonic_func=lambda: clock[0],
                       utc_now=lambda: wall + timedelta(seconds=clock[0])) as recorder:
        clock[0] = 2
        recorder.enter("extract")
        clock[0] = 5
        recorder.enter("link")
        clock[0] = 9
    rows = store.phase_runs(job["job_id"])
    actual = [row for row in rows if row["phase"] in {"startup", "extract", "link"}]
    assert [row["elapsed_ms"] for row in actual] == [2000, 3000, 4000]
    assert all(row["duration_source"] == "monotonic" for row in actual)
    assert all(row["generation"] == job["generation"] for row in actual)
    assert len({row["process_run_id"] for row in actual}) == 1


def test_phase_exception_closes_span_and_failed_worker_retains_it(budget_job):
    from telegram_connector.analysis_telemetry import PhaseRecorder

    store, job = budget_job
    with pytest.raises(TimeoutError):
        with PhaseRecorder(store, job) as recorder:
            recorder.enter("extract")
            raise TimeoutError("fixture")
    rows = store.phase_runs(job["job_id"])
    assert rows[-1]["phase"] == "extract"
    assert rows[-1]["outcome"] == "timed_out"
    assert rows[-1]["finished_at"] is not None


def test_native_usage_details_and_endpoint_correlation_are_not_lost(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    response = {"id": "resp_fixture", "model": "fixture-model-snapshot",
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 4,
                "cache_write_tokens_details": {"ttl_30m_tokens": 3, "ttl_12h_tokens": 1}},
            "output_tokens_details": {"reasoning_tokens": 2}}}
    call_analysis(store, job, batch, send=lambda *a, **k: Response(response))
    row = store.conn.execute("SELECT * FROM ai_usage_log WHERE job_id=?", (job["job_id"],)).fetchone()
    assert row["cache_write_tokens"] == 4
    assert row["cache_write_30m_tokens"] == 3
    assert row["cache_write_12h_tokens"] == 1
    assert row["reasoning_tokens"] == 2
    assert row["response_status"] == "incomplete"
    assert row["incomplete_reason"] == "max_output_tokens"
    assert row["actual_model"] == "fixture-model-snapshot"
    assert row["response_id"] == "resp_fixture"
    assert row["generation"] == job["generation"]
    assert row["started_at"] is not None


def test_recovered_open_span_is_marked_as_estimated_not_measured(budget_job):
    store, job = budget_job
    started = datetime.now(timezone.utc)
    store.begin_phase(job["job_id"], generation=job["generation"], phase="extract",
                      process_run_id="terminated-child", now=started)
    store.finish_job(job["job_id"], generation=job["generation"], status="timed_out",
                     now=started + timedelta(seconds=3))
    row = next(row for row in store.phase_runs(job["job_id"]) if row["phase"] == "extract")
    assert row["outcome"] == "timed_out"
    assert row["elapsed_ms"] == 3000
    assert row["duration_source"] == "utc_estimate"


def test_summary_does_not_turn_missing_usage_into_zero_cost(budget_job):
    from telegram_connector.analysis_telemetry import summarize_job

    store, job = budget_job
    batch = plan(store, job)
    call_analysis(store, job, batch, send=lambda *a, **k: Response({"id": "missing-usage"}))
    report = summarize_job(store, job["job_id"])
    assert report["usage"]["missing_inference_receipts"] == 1
    assert report["cost"]["usd"] is None
    assert report["telemetry_complete"] is False
    assert "missing_inference_usage" in report["warnings"]


def test_retries_keep_backoff_separate_and_link_attempts_to_phase(budget_job):
    from telegram_connector import telegram_analysis as analysis
    from telegram_connector.analysis_telemetry import PhaseRecorder, summarize_job

    store, job = budget_job
    batch = plan(store, job)
    clock, calls = [0.0], []

    def send(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("fixture timeout")
        return Response({"usage": {"input_tokens": 10, "output_tokens": 5}})

    def sleep(delay):
        clock[0] += delay

    with PhaseRecorder(store, job, monotonic_func=lambda: clock[0]) as recorder:
        recorder.enter("synthesize")
        analysis.request_analysis_json(store, job["job_id"], batch["batch_id"],
            generation=job["generation"], endpoint="responses",
            payload={"model": "fixture-model", "input": "fixture", "max_output_tokens": 20},
            api_key="fixture-key", counted_input_tokens=10, deadline=100,
            monotonic_func=lambda: clock[0], sleep_func=sleep, urlopen_func=send)
    rows = [dict(row) for row in store.conn.execute("SELECT * FROM ai_usage_log ORDER BY id")]
    assert len(rows) == 2 and rows[0]["backoff_ms"] > 0
    assert rows[0]["backoff_ms"] == rows[0]["backoff_requested_ms"]
    assert rows[1]["backoff_ms"] == 0
    assert len({row["phase_sequence"] for row in rows}) == 1
    assert rows[0]["phase_sequence"] is not None
    report = summarize_job(store, job["job_id"])
    phase = next(row for row in report["phase_runs"] if row["phase"] == "synthesize")
    assert phase["backoff_ms"] == rows[0]["backoff_ms"]


def test_cost_counts_each_cache_write_lifetime_once():
    from telegram_connector.analysis_telemetry import _cost

    rows = [{"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2,
             "cache_write_tokens": 4, "cache_write_30m_tokens": 3, "cache_write_12h_tokens": 1}]
    rates = {"input": .2, "cached_input": .02, "cache_write_30m": .25,
             "cache_write_12h": .5, "output": 1.2}
    result = _cost(rows, rates)
    assert result["usd"] == pytest.approx(8.09 / 1000000)
    rows[0]["cache_write_12h_tokens"] = None
    assert _cost(rows, rates)["usd"] is None


def test_phase_metrics_follow_storage_accounting_and_job_retention(budget_job):
    from telegram_connector.analysis_telemetry import PhaseRecorder

    store, job = budget_job
    before = store.storage_usage(job["job_id"])
    with PhaseRecorder(store, job) as recorder:
        recorder.enter("verify")
    after = store.storage_usage(job["job_id"])
    assert after['logical_bytes'] > before['logical_bytes']
    assert after['telemetry_reserve'] < before['telemetry_reserve']
    assert store.get_job(job['job_id'])['intermediate_bytes'] == after['allocated_bytes']
    assert store.phase_runs(job["job_id"])
    store.conn.execute("DELETE FROM analysis_jobs WHERE job_id=?", (job["job_id"],))
    assert store.phase_runs(job["job_id"]) == []


@pytest.mark.parametrize("usage", [None, [], {"input_tokens": 10, "output_tokens": 5,
    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": "malformed"},
    "output_tokens_details": {"reasoning_tokens": "malformed"}}])
def test_missing_or_malformed_optional_usage_stays_unknown_without_losing_attempt(budget_job, usage):
    store, job = budget_job
    batch = plan(store, job)
    call_analysis(store, job, batch, send=lambda *a, **k: Response({"usage": usage}))
    row = store.conn.execute("SELECT * FROM ai_usage_log").fetchone()
    assert row["cache_write_tokens"] is None
    assert row["reasoning_tokens"] is None
    assert store.get_job(job["job_id"])["provider_requests"] == 1


def test_unlogged_reserved_attempt_keeps_cost_unknown(budget_job):
    from telegram_connector.analysis_telemetry import summarize_job
    from telegram_connector.tests.test_analysis_budget import reserve

    store, job = budget_job
    reserve(store, job, plan(store, job))
    rates = {"input": .2, "cached_input": .02, "cache_write_30m": .25,
             "cache_write_12h": .5, "output": 1.2}
    result = summarize_job(store, job["job_id"], pricing=rates)
    assert result["cost"]["usd"] is None
    assert result["usage"]["missing_inference_receipts"] == 1
    assert "inference_attempt_log_mismatch" in result["warnings"]


@pytest.mark.parametrize("value", [-1, float("nan"), True])
def test_invalid_price_cannot_produce_a_cost_estimate(value):
    from telegram_connector.analysis_telemetry import _cost

    rates = {"input": value, "cached_input": .02, "cache_write_30m": .25,
             "cache_write_12h": .5, "output": 1.2}
    with pytest.raises(ValueError, match="prices"):
        _cost([], rates)
