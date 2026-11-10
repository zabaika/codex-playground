import pytest

from telegram_connector.analysis_telemetry import _cost, summarize_job
from telegram_connector.tests.test_analysis_budget import budget_job, plan
from telegram_connector.tests.test_analysis_transport import call_analysis, Response


RATES = {"input": .2, "cached_input": .02, "cache_write_30m": .25,
         "cache_write_12h": .5, "output": 1.2}


def receipt(**changes):
    return {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2,
            "cache_write_tokens": 4, "cache_write_30m_tokens": None,
            "cache_write_12h_tokens": None, **changes}


def test_missing_cache_lifetimes_have_positive_conditional_bounds():
    result = _cost([receipt()], RATES)
    assert result["usd"] is None
    assert result["usd_min"] == pytest.approx(7.84e-6)
    assert result["usd_max"] == pytest.approx(8.84e-6)
    assert result["known_usage_usd"] == 0
    assert result["unknown_components"] == ["cache_write_lifetime"]


def test_partial_lifetime_uses_known_component_and_bounds_remaining_writes():
    result = _cost([receipt(cache_write_30m_tokens=3)], RATES)
    assert result["usd"] is None
    assert result["usd_min"] == pytest.approx(7.84e-6)
    assert result["usd_max"] == pytest.approx(8.09e-6)
    exact = _cost([receipt(cache_write_tokens=0)], RATES)
    assert exact["usd"] == exact["usd_min"] == exact["usd_max"]


@pytest.mark.parametrize("changes", [{"cached_input_tokens": None}, {"cache_write_tokens": None},
    {"input_tokens": -1}, {"cached_input_tokens": 11}, {"cache_write_30m_tokens": 5},
    {"cache_write_30m_tokens": 1, "cache_write_12h_tokens": 1}])
def test_invalid_or_missing_counters_have_no_finite_whole_receipt_bounds(changes):
    result = _cost([receipt(**changes)], RATES)
    assert result["usd"] is result["usd_min"] is result["usd_max"] is None
    assert result["unknown_components"]


def test_actual_service_tier_is_native_and_billing_is_independent_of_phase_trace(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    response = {"model": "fixture-model", "service_tier": "default", "usage": {
        "input_tokens": 10, "output_tokens": 5, "input_tokens_details": {
            "cached_tokens": 2, "cache_write_tokens": 0}}}
    call_analysis(store, job, batch, send=lambda *a, **k: Response(response))
    row = store.conn.execute("SELECT * FROM ai_usage_log WHERE job_id=?", (job["job_id"],)).fetchone()
    assert row["actual_service_tier"] == "default"
    report = summarize_job(store, job["job_id"], pricing={**RATES, "model": "fixture-model",
                                                      "service_tier": "standard"})
    assert report["billing_complete"] is True
    assert report["telemetry_complete"] is False
    assert report["cost"]["usd"] > 0
    assert report["usage"]["actual_service_tiers"] == ["default"]


def test_unknown_or_incompatible_native_tier_cannot_claim_exact_billing(budget_job):
    store, job = budget_job
    batch = plan(store, job)
    response = {"model": "fixture-model", "service_tier": "priority", "usage": {
        "input_tokens": 10, "output_tokens": 5, "input_tokens_details": {
            "cached_tokens": 0, "cache_write_tokens": 0}}}
    call_analysis(store, job, batch, send=lambda *a, **k: Response(response))
    report = summarize_job(store, job["job_id"], pricing={**RATES, "service_tier": "standard"})
    assert report["billing_complete"] is False
    assert report["cost"]["usd_min"] is None
    assert "actual_service_tier_mismatch" in report["billing_warnings"]
