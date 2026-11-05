"""Operator-only offline replay of the approved 29-message snapshot, never a paid run."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import tomllib

import pytest

from common import sqlite as common_sqlite
from common import process as common_process
from telegram_connector import analysis_evidence, analysis_pipeline, analysis_telemetry
from telegram_connector import telegram_analysis as analysis, telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore, canonical_json
from telegram_connector.scripts import evaluate_analysis as evaluator
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_evidence import model_response


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "scratch/telegram-connector-analytics/ai-limits-reference"
HISTORICAL_JOB = "a62d665f7e7441fbb60de1e4e4593032"


@pytest.fixture
def recorded_case(tmp_path, monkeypatch):
    if not (REFERENCE / "trial/recorded-topics-all-normal.stages.json").is_file():
        pytest.skip("Operator's frozen 29-message fixture is unavailable")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("Offline replay forbids network"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Offline replay forbids network"))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a, **k: pytest.fail("Offline replay forbids network"))
    gold = evaluator.validate_gold(json.loads((REFERENCE / "recorded-topics.json").read_text()))
    stages = json.loads((REFERENCE / "trial/recorded-topics-all-normal.stages.json").read_text())
    historical = sqlite3.connect(f"file:{REFERENCE / 'trial/history.sqlite3'}?mode=ro", uri=True)
    historical.row_factory = sqlite3.Row
    try:
        before = tuple(historical.execute("SELECT status,provider_requests,llm_calls,used_tokens,delivery_json FROM analysis_jobs WHERE job_id=?",
                                          (HISTORICAL_JOB,)).fetchone())
        leaves = [{**dict(row), "payload": json.loads(row["payload_json"])} for row in historical.execute(
            "SELECT e.* FROM analysis_evidence e JOIN analysis_batches b USING(batch_id) WHERE b.job_id=? AND b.kind='extract' AND b.state='succeeded' ORDER BY e.record_id",
            (HISTORICAL_JOB,))]
        batches = [dict(row) for row in historical.execute("SELECT batch_id,kind,payload_json FROM analysis_batches WHERE job_id=?", (HISTORICAL_JOB,))]
        groups = {kind: [] for kind in ("link", "merge")}
        for row in batches:
            if row["kind"] not in groups:
                continue
            plan = json.loads(row["payload_json"])["plan"]
            outputs = [analysis_evidence.parse_document(analysis_evidence.response_text(value["response"])[1])
                       for value in stages if value["batch_id"] == row["batch_id"]]
            groups[row["kind"]].append((plan, outputs))
        synthesis = next(analysis_evidence.parse_document(analysis_evidence.response_text(value["response"])[1])
                         for value in stages if any(row["batch_id"] == value["batch_id"] and row["kind"] == "synthesize" for row in batches))
        conn = common_sqlite.connect_sqlite(tmp_path / "fixture.sqlite3")
        history.init_db(conn)
        config_dir = ROOT / "telegram_connector/config"
        config = tomllib.loads((config_dir / "runtime.local.toml").read_text())
        config.setdefault("paths", {})["history_db"] = str(tmp_path / "fixture.sqlite3")
        config["processing"]["model"] = gold["recorded"]["model"]
        values = evaluator.trial_config(gold, config, config_dir=config_dir, output_root=tmp_path)
        channels = gold["recorded"]["channels"]
        refs = {"@" + channel["username"]: channel["channel_id"] for channel in channels}
        client = evaluator.RecordedCorpusClient(refs, channels)
        for channel in channels:
            history.upsert_channel(conn, client.entity(channel["channel_id"]))
        evaluator.seed_recorded_sources(conn, gold["recorded"])
        store = AnalysisStore(conn)
        event = {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 7},
            "text": f"/topics {','.join(refs)} since={gold['period']['since']} until={gold['period']['until']} --detail normal --limit 15 --sources 3"}}
        access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
        now = datetime.now(timezone.utc)
        queued, _ = analysis.admit_analysis_job(store, values, event, access=access, bot_namespace="offline-r8-m9",
            now=now, config_dir=config_dir, cutoff_at=datetime.fromisoformat(gold["period"]["accepted_at"]))
        job = store.claim_job(queued["job_id"], max_running_jobs=1, now=now)
        store.save_checkpoint(job["job_id"], {"history": {"refs": refs, "sources": {
            str(peer): {"mode": "reuse", "state": "complete"} for peer in refs.values()}}},
            generation=job["generation"], max_bytes=67108864)
        analysis.build_all_messages_manifest(store, job["job_id"], generation=job["generation"])
        fragments = list(analysis.iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"]))
        assert {(x["input"]["channel_id"], x["input"]["message_id"]): x["input"]["content_digest"] for x in fragments} == {
            (x["input"]["channel_id"], x["input"]["message_id"]): x["input"]["content_digest"] for x in gold["recorded"]["messages"]}
        assert len(fragments) == 29 and len(leaves) == 23
        # Import already-validated extraction with its frozen IDs. This preserves
        # the original candidate universe; extraction is not generated again.
        batch = store.plan_batch(job["job_id"], generation=job["generation"], step_key="recorded-extraction-import", kind="extract", payload={"origin": HISTORICAL_JOB})
        store.complete_batch(job["job_id"], batch["batch_id"], generation=job["generation"], records=[
            {key: row[key] for key in ("record_id", "record_type", "topic_key", "payload")} for row in leaves],
            inputs=[row["input"] for row in fragments])
        calls, consumed = [], {}
        def provider(req, timeout):
            payload = json.loads(req.data)
            if req.full_url.endswith("input_tokens"):
                return Response({"input_tokens": 100})  # Fixture count, not a real token estimate.
            kind = payload["text"]["format"]["name"]
            calls.append(kind)
            if kind == "telegram_relations_v2":
                data = json.loads(payload["input"].split("Кандидаты: ", 1)[1])
                mapping = {pair["pair_id"]: [pair["left_id"], pair["right_id"]] for pair in data["pairs"]}
                wanted = {tuple(sorted(pair)) for pair in mapping.values()}
                matches = [(plan, outputs) for plan, outputs in groups["link"] if wanted <= {tuple(sorted(pair)) for pair in plan["pairs"]}]
                assert len(matches) == 1
                plan, outputs = matches[0]
                identity = hashlib.sha256(canonical_json(plan).encode()).hexdigest()
                index = consumed.get(identity, 0)
                consumed[identity] = index + 1
                raw = outputs[min(index, len(outputs) - 1)]
                root_scope = {tuple(sorted(pair)) for pair in plan["pairs"]}
                reverse = {tuple(sorted(pair)): key for key, pair in mapping.items()}
                decisions = []
                for value in raw["decisions"]:
                    pair = tuple(sorted((value["left_id"], value["right_id"])))
                    if pair in reverse:
                        decisions.append({"pair_id": reverse[pair], "relation": value["relation"], "reason": value["reason"]})
                    elif pair not in root_scope:
                        decisions.append({"pair_id": "foreign", "relation": value["relation"], "reason": value["reason"]})
                output = {"decisions": decisions, "saturated": raw["saturated"]}
            elif kind == "telegram_merge_v1":
                data = json.loads(payload["input"].split("Записи: ", 1)[1])
                wanted = {node["node_id"] for node in data["inputs"]}
                matches = [outputs[0] for plan, outputs in groups["merge"] if wanted == {node["node_id"] for node in plan["inputs"]}]
                assert len(matches) == 1 and data["operation"] == "merge"
                output = copy.deepcopy(matches[0])
            else:
                assert kind == "telegram_synthesis_v1"
                data = json.loads(payload["input"].split("Опорные записи: ", 1)[1])
                assert data["group_order"] == "unranked" and data["coverage"]["ranking_scope"] == "complete"
                assert len(data["groups"]) == len(data["records"]) == 23
                assert "всему переданному набору" in payload["input"]
                output = copy.deepcopy(synthesis)
            response = model_response(output)
            response.update(model=values["processing"]["model"], id="offline-fixture")
            response["usage"]["input_tokens_details"] = {"cached_tokens": 0, "cache_write_tokens": 0}
            return Response(response)
        yield store, store.get_job(job["job_id"]), provider, calls
        assert tuple(historical.execute("SELECT status,provider_requests,llm_calls,used_tokens,delivery_json FROM analysis_jobs WHERE job_id=?",
                                        (HISTORICAL_JOB,)).fetchone()) == before
    finally:
        historical.close()
        if "conn" in locals():
            conn.close()


@pytest.mark.parametrize("mode", ["relations", "merge", "combined"])
def test_frozen_29_message_policy_replay(recorded_case, mode):
    store, job, provider, calls = recorded_case
    transport = {"api_key": "offline-fixture", "capabilities": {"model": job["request"]["settings"]["processing"]["model"],
        "context_window": 1050000, "max_input_tokens": 922000, "max_output_tokens": 128000, "source": "offline-fixture"},
        "deadline": common_process.continuous_time() + 300, "urlopen_func": provider}
    with analysis_telemetry.PhaseRecorder(store, job) as recorder:
        if mode in {"relations", "combined"}:
            recorder.enter("link")
            assert not analysis_pipeline.link_analysis_records(store, job["job_id"], generation=job["generation"], **transport)["partial"]
            assert len(list(store.iter_link_decisions(job["job_id"]))) == 120
        else:
            current = store.get_job(job["job_id"])
            store.save_checkpoint(job["job_id"], {**current["checkpoint"], "relations": {"partial": True}},
                                  generation=job["generation"], max_bytes=67108864)
        if mode in {"merge", "combined"}:
            recorder.enter("merge")
            merged = analysis_pipeline.merge_analysis_records(store, job["job_id"], generation=job["generation"], **transport)
            assert merged["state"] == "ready" and merged["group_order"] == "unranked"
            assert "merge_no_progress" not in merged["reasons"]
            recorder.enter("synthesize")
            result = analysis_pipeline.synthesize_analysis_result(store, job["job_id"], generation=job["generation"], **transport)
            assert result["partial"] == (mode == "merge")
            assert result["reasons"] == (["relations_incomplete"] if mode == "merge" else [])
            recorder.enter("verify")
            rendered = analysis.prepare_analysis_presentation(store, job["job_id"], result, generation=job["generation"])
            assert len(rendered["result"]["items"]) == 14 and rendered["result"]["excluded_digest_only_items"] == 1
    store.finish_job(job["job_id"], generation=job["generation"], status="partial" if mode == "merge" else "completed", now=datetime.now(timezone.utc))
    report = analysis_telemetry.summarize_job(store, job["job_id"])
    assert report["telemetry_complete"] and not report["warnings"]
    if mode != "merge":
        assert report["relations"]["planned_pairs"] == report["relations"]["accepted_pairs"] == 120
        assert report["relations"]["unresolved_pairs"] == 0
    if mode != "relations":
        assert calls.count("telegram_merge_v1") == 3 and calls.count("telegram_synthesis_v1") == 1
        assert report["merge"]["reduction_outcome"] == "no_reduction"
    output = ROOT / "scratch/telegram-connector-analytics/r12-local-replay" / job['job_id']
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{mode}.json").write_text(json.dumps({"fixture_mode": mode, "historical_job": HISTORICAL_JOB,
        "messages": 29, "evidence_records": 23, "real_api_calls": 0, "telegram_sends": 0,
        "input_token_counts": "Synthetic 100; actual new token count/cost unverified",
        "model_documents": "Historical content replay, legacy pair addresses projected to requested v2 scope; not fresh model-quality evidence",
        "inference_calls": calls, "telemetry": report}, ensure_ascii=False, indent=2) + "\n")
