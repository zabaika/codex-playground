"""Independent analytics policy and immutable stage budgets."""

import copy
from pathlib import Path
import tomllib

import pytest

from telegram_connector import analysis_settings as settings_owner
from telegram_connector import telegram_analysis as analysis


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def config():
    return tomllib.loads((CONFIG_DIR / "runtime.example.toml").read_text())


def test_analytics_ai_does_not_read_digest_settings():
    values = config()
    values.pop("digest_ai")
    settings = analysis.resolve_analysis_settings(values, config_dir=CONFIG_DIR)
    ai = settings_owner.analysis_ai(settings)
    assert ai["extract_max_output_tokens"] == 32768
    assert ai["link_max_output_tokens"] == 8192
    assert ai["request_timeout_seconds"] == 600
    assert ai["token_count_timeout_seconds"] == 30
    assert "digest_ai" not in settings
    values["digest_ai"] = {"max_output_tokens": "invalid", "reasoning_effort": "invalid"}
    assert analysis.resolve_analysis_settings(values, config_dir=CONFIG_DIR) == settings


def test_analytics_ai_overrides_are_frozen_and_stage_specific():
    values = config()
    values["analysis"].setdefault("ai", {}).update(
        extract_max_output_tokens=24000, link_max_output_tokens=6000,
        request_timeout_seconds=480, token_count_timeout_seconds=20,
    )
    original = copy.deepcopy(values)
    settings = analysis.resolve_analysis_settings(values, config_dir=CONFIG_DIR)
    expected = {"expand_query": 1024, "extract": 24000,
                "link": 6000, "merge": 8192, "synthesize": 3000}
    for stage, cap in expected.items():
        assert settings_owner.stage_output_limit(settings, stage, detail="normal") == cap
    assert settings["analysis"]["link_pairs_per_request"] == 0
    assert values == original


@pytest.mark.parametrize("key,value", [
    ("retry_attempts", 0), ("retry_attempts", 6),
    ("reasoning_effort", "invalid"), ("reasoning_summary", "invalid"),
    ("extract_max_output_tokens", 0), ("token_count_timeout_seconds", -1),
    ("request_timeout_seconds", True),
])
def test_analytics_ai_rejects_invalid_policy(key, value):
    values = config()
    values["analysis"].setdefault("ai", {})[key] = value
    with pytest.raises(ValueError, match="analysis.ai"):
        analysis.resolve_analysis_settings(values, config_dir=CONFIG_DIR)


def test_missing_analytics_ai_cannot_fall_back_to_digest():
    frozen = analysis.resolve_analysis_settings(config(), config_dir=CONFIG_DIR)
    del frozen['analysis']['ai']
    frozen['digest_ai'] = dict(max_output_tokens=1500, openai_timeout_seconds=120,
        openai_retry_attempts=3, openai_retry_backoff_seconds=1, reasoning_effort='low',
        reasoning_summary='none', store_prompt_text=False, messages_per_ai_pass=500,
        message_block_max_chars=100000)
    before = copy.deepcopy(frozen)
    with pytest.raises(KeyError, match="^'ai'$"):
        settings_owner.analysis_ai(frozen)
    assert frozen == before


@pytest.mark.parametrize("value", ["", "---\n---", "---\r---"])
def test_separator_is_one_nonempty_line(value):
    values = config()
    values["analysis"]["output"]["item_separator_text"] = value
    with pytest.raises(ValueError, match="item_separator_text"):
        analysis.resolve_analysis_settings(values, config_dir=CONFIG_DIR)


@pytest.mark.parametrize("stage", ["extract", "expand_query", "link", "merge", "synthesize"])
def test_ledger_uses_the_same_stage_cap_as_the_payload(stage):
    from common import sqlite as common_sqlite
    from telegram_connector import telegram_history_client as history
    from telegram_connector.tests.test_analysis_budget import admit_budget_job, plan

    connection = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(connection)
        store, job = admit_budget_job(connection)
        batch = plan(store, job, kind=stage)
        cap = settings_owner.stage_output_limit(job["request"]["settings"], stage, detail="normal")
        with pytest.raises(ValueError, match="stage_output_limit"):
            store.reserve_provider_attempt(job["job_id"], batch["batch_id"],
                generation=job["generation"], attempt_id="too-large", request_kind="inference",
                input_tokens=10, output_tokens=cap + 1)
        attempt = store.reserve_provider_attempt(job["job_id"], batch["batch_id"],
            generation=job["generation"], attempt_id="fits", request_kind="inference",
            input_tokens=10, output_tokens=cap)
        assert attempt["output_limit"] == cap
    finally:
        connection.close()


def test_provider_uses_separate_endpoint_timeouts_within_remaining_ttl():
    from common import sqlite as common_sqlite
    from telegram_connector import telegram_history_client as history
    from telegram_connector.tests.test_analysis_budget import admit_budget_job, plan
    from telegram_connector.tests.test_analysis_transport import call_analysis, Response

    connection = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(connection)
        store, job = admit_budget_job(connection)
        batch = plan(store, job)
        timeouts = []

        def send(request, timeout):
            timeouts.append(timeout)
            return Response({"input_tokens": 10, "usage": {"input_tokens": 10, "output_tokens": 5}})

        call_analysis(store, job, batch, endpoint="input_tokens", send=send)
        call_analysis(store, job, batch, send=send)
        assert timeouts == [30, 100]  # Inference clipped to the remaining TTL.
    finally:
        connection.close()
