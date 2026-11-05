import copy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from common import sqlite as common_sqlite
from telegram_connector import analysis_evidence
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore
from telegram_connector.tests.test_analysis_quality import evaluator, config, raw_gold, fixture_api


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def recorded_gold(evaluator):
    gold = raw_gold()
    gold["tasks"] = [next(task for task in gold["tasks"] if task["command"] == "topics")]
    gold["tasks"][0].update(details=["normal"], modes=[None], limit=15, sources=3)
    conn = common_sqlite.connect_sqlite(":memory:")
    try:
        history.init_db(conn)
        peers = sorted({message["channel_id"] for message in gold["messages"]})
        channels = [{"channel_id": peer, "title": "Recorded fixture", "username": f"recorded{peer}",
                     "channel_type": "Chat"} for peer in peers]
        client = evaluator.RecordedCorpusClient({}, channels)
        for peer in peers:
            history.upsert_channel(conn, client.entity(peer))
        store, messages = AnalysisStore(conn), []
        for value in gold["messages"]:
            history.upsert_message(conn, client.entity(value["channel_id"]), SimpleNamespace(
                id=value["message_id"], message=value["text"], date=datetime.fromisoformat(value["date"]),
                media=None), None, None, None, None)
            snapshot, digest = analysis_evidence._analysis_message_snapshot(
                store, value["channel_id"], value["message_id"], 67108864)
            row = dict(conn.execute("SELECT * FROM messages WHERE channel_id=? AND message_id=?",
                                   (value["channel_id"], value["message_id"])).fetchone())
            messages.append({"input": {"channel_id": value["channel_id"], "message_id": value["message_id"],
                                       "content_digest": digest}, "snapshot": snapshot, "message": row})
        gold["recorded"] = {"baseline_job_id": "fixture-historical", "model": "gpt-5.6-luna",
            "messages": messages, "channels": channels,
            "limits": {"max_llm_calls": 32, "max_provider_requests": 128, "max_total_tokens": 1000000},
            "pricing": None}
        return evaluator.validate_gold(gold)
    finally:
        conn.close()


def test_recorded_scope_is_one_case_and_preserves_operator_packing(evaluator, config, tmp_path):
    config["processing"]["model"] = "gpt-5.6-luna"
    gold = recorded_gold(evaluator)
    scope = evaluator.build_scope(gold, config, config_dir=CONFIG_DIR, output_root=tmp_path)
    assert len(scope["cases"]) == 1 and scope["cases"][0]["command"] == "topics"
    trial = evaluator.trial_config(gold, config, config_dir=CONFIG_DIR, output_root=tmp_path)
    settings = evaluator.analysis.resolve_analysis_settings(trial, config_dir=CONFIG_DIR)
    assert settings["analysis"]["ai"]["messages_per_ai_pass"] == 500
    assert scope["baseline_cutoff_at"] == gold["period"]["accepted_at"]
    assert scope["total_limits"] == scope["limits"]


def test_recorded_case_uses_saved_cutoff_and_captured_run_cannot_spend_twice(evaluator, config, tmp_path):
    config["processing"]["model"] = "gpt-5.6-luna"
    gold = recorded_gold(evaluator)
    scope = evaluator.build_scope(gold, config, config_dir=CONFIG_DIR, output_root=tmp_path)
    case = scope["cases"][0]["id"]
    run = evaluator.execute_case(gold, config, scope, case, output_root=tmp_path,
        config_dir=CONFIG_DIR, api_key="fixture", urlopen_func=fixture_api("topics", []))
    assert run["telemetry"]["period"]["cutoff_at"] == gold["period"]["accepted_at"]
    assert run["telemetry"]["accepted_at"] != gold["period"]["accepted_at"]
    assert len(run["selected_source_refs"]) == len(gold["messages"])
    assert run["telemetry"]["phase_runs"]
    repeated = evaluator.execute_case(gold, config, scope, case, output_root=tmp_path,
        config_dir=CONFIG_DIR, api_key="fixture",
        urlopen_func=lambda *a, **k: pytest.fail("Captured case cannot run again"))
    assert repeated == run


def test_recorded_source_change_or_extra_case_is_rejected(evaluator):
    gold = recorded_gold(evaluator)
    changed = copy.deepcopy(gold)
    changed["recorded"]["messages"][0]["snapshot"]["text"] += " changed"
    with pytest.raises(ValueError, match="recorded_source_changed"):
        evaluator.validate_gold(changed)
    gold["tasks"][0]["details"].append("short")
    with pytest.raises(ValueError, match="one_topics_case"):
        evaluator.validate_gold(gold)
