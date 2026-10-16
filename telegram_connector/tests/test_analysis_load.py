"""Opt-in resource acceptance on real SQLite, with a bounded synthetic provider.

Worker: python .../test_analysis_load.py --count 10000 --report /tmp/baseline.json
Acceptance: RUN_TELEGRAM_ANALYSIS_LOAD=1 pytest .../test_analysis_load.py
RSS policy is frozen after baseline, before production optimizations.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from common import json_io, sqlite as common_sqlite
from telegram_connector import telegram_analysis as analysis, telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore
from telegram_connector.tests.test_analysis_engine import EngineHistoryClient
from telegram_connector.tests.test_analysis_transport import Response


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
POLICY = ROOT / "scratch/telegram-connector-analytics/load/rss-policy.json"


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def mock_input_units(payload):
    # Synthetic budget units only. This is NOT a tokenizer or provider latency trial.
    counted = {key: value for key, value in payload.items() if key in {"model", "input", "instructions", "text", "reasoning"}}
    return (len(analysis.canonical_json(counted).encode("utf-8")) + 3) // 4


def bounded_provider(job, stats):
    def send(req, timeout):
        data = json.loads(req.data)
        units = mock_input_units(data)
        stats["max_input_units"] = max(stats["max_input_units"], units)
        if req.full_url.endswith("input_tokens"):
            stats["count_calls"] += 1
            return Response({"input_tokens": units})
        stats["inference_calls"] += 1
        stats["max_inference_input_units"] = max(stats["max_inference_input_units"], units)
        kind = data["text"]["format"]["name"]
        if kind == "telegram_evidence_v1":
            fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
            stats["max_batch_messages"] = max(stats["max_batch_messages"], len(fragments))
            records = []
            chosen = fragments[:1] if len(fragments) == 1 else [fragments[0], fragments[-1]]
            for fragment in chosen:
                ref = fragment["input"]
                quote = fragment["message"]["text"][:64]
                records.append({"record_type": "answer" if job["command"] == "qa" else "event" if job["command"] == "topics" else "claim",
                    "claim": quote, "details": [], "entities": ["Uno"], "topic_key": "документы",
                    "conditions": [], "exceptions": [], "event_at": None,
                    "source_refs": [{**{key: ref[key] for key in ("channel_id", "message_id", "content_digest", "fragment_start")},
                        "fragment_end": ref["fragment_start"] + len(quote), "quote": quote}],
                    "related_message_refs": [], "assertion_kind": "reported", "support_status": "reported"})
            output = {"records": records, "saturated": False}
        elif kind == "telegram_relations_v1":
            pairs = json.loads(data["input"].split("Кандидаты: ", 1)[1])["pairs"]
            output = {"decisions": [{"left_id": left, "right_id": right, "relation": "complements", "reason": "Synthetic repeated condition"}
                for left, right in pairs], "saturated": False}
        elif kind == "telegram_merge_v1":
            values = json.loads(data["input"].split("Записи: ", 1)[1])["inputs"]
            ids = [value["node_id"] for value in values]
            output = {"groups": [{"title": "Документы Uno", "summary": "Повторяющиеся условия подачи", "input_ids": ids}],
                "dispositions": [{"input_id": key, "disposition": "merged", "reason": "Сохранено"} for key in ids], "saturated": False}
        else:
            assert kind == "telegram_synthesis_v1"
            values = json.loads(data["input"].split("Опорные записи: ", 1)[1])["records"]
            output = {"summary": "Условия из синтетического корпуса", "items": [{"title": "Какие документы нужны?" if job["command"] == "qa" else "Документы Uno",
                "text": "Паспорт; 1250 EUR, срок 30 дней.", "conditions": [], "exceptions": [], "contradictions": [],
                "evidence_ids": [values[0]["record_id"]]}] if values else [], "limitations": [],
                "insufficient_information": not values, "saturated": False}
        text = analysis.canonical_json(output)
        output_units = (len(text.encode("utf-8")) + 3) // 4
        stats["max_output_units"] = max(stats["max_output_units"], output_units)
        assert output_units <= data["max_output_tokens"], "Synthetic output must respect the real configured output cap"
        return Response({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": units, "output_tokens": output_units}})
    return send


def source_rows(count):
    base = "Документы Uno: паспорт, 1250 EUR, срок 30 дней. "
    for number in range(1, count + 1):
        peer = 22 if number % 2 else 11
        text = base + ("Условия и исключения подачи. " * 300 if number % 100 == 0 else "")
        raw = analysis.canonical_json({"reply_to": {"reply_to_msg_id": number - 2}}) if number > 2 and number % 10 == 0 else "{}"
        yield (peer, number, f"2026-10-{10 + number % 3}T10:00:00+00:00", text, raw, "fixture", NOW.isoformat())


def seed_corpus(conn, count, batch):
    for peer in (22, 11):
        conn.execute("INSERT INTO channels VALUES (?,NULL,?,'Synthetic','Channel','{}','now','now')", (peer, f"peer{peer}"))
    rows, source_bytes = [], 0
    def flush():
        with common_sqlite.write_tx(conn):
            conn.executemany("""INSERT INTO messages(channel_id,message_id,date_utc,text,raw_json,content_hash,imported_at)
                VALUES (?,?,?,?,?,?,?)""", rows)
            for peer, number, *rest in rows:
                if number % 50 == 0:
                    conn.execute("""INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,ocr_status,ocr_text,created_at)
                        VALUES (?,?,0,'photo','done',?,'now')""", (peer, number, "OCR паспорт и условия Uno " * 60))
    for row in source_rows(count):
        source_bytes += len(row[3].encode("utf-8"))
        if row[1] % 50 == 0:
            source_bytes += len(("OCR паспорт и условия Uno " * 60).encode("utf-8"))
        rows.append(row)
        if len(rows) == batch:
            flush()
            rows.clear()
    if rows:
        flush()
    return source_bytes


def measure(count, report, *, cache_probe=False):
    started = time.monotonic()
    result = {"count": count, "python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
        "provider": "offline synthetic input/output units, no real tokenization or network", "stages": {}, "commands": [],
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    def stage(name, since):
        result["stages"][name] = {"seconds": time.monotonic() - since, "peak_rss_bytes": peak_rss()}
        print(json.dumps({"stage": name, **result["stages"][name]}), flush=True)
    with tempfile.TemporaryDirectory(prefix="telegram-analysis-load-") as folder:
        path = Path(folder) / "history.sqlite3"
        config = tomllib.loads((history.APP_DIR / "config/runtime.example.toml").read_text())
        config["paths"]["history_db"] = str(path)
        config["processing"]["model"] = "gpt-5.6-luna"
        config["digest_ai"].update(reasoning_effort="low", reasoning_summary="none", max_output_tokens=1500)
        conn = common_sqlite.connect_sqlite(path)
        try:
            history.init_db(conn)
            clock = time.monotonic()
            result["source_bytes"] = seed_corpus(conn, count, int(config["sync"]["batch_size"]))
            stage("seed", clock)
            store = AnalysisStore(conn)
            clock = time.monotonic()
            queued = analysis.enqueue_fts_backfill(store, config, rebuild=False, now=NOW)
            index = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
            while history.backfill_fts_page(store, index["job_id"], generation=index["generation"])["state"] != "ready":
                pass
            stage("fts_backfill", clock)
            result["fts_rows"] = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
            clock = time.monotonic()
            traced = []
            def trace(sql):
                if sql.startswith("INSERT OR REPLACE INTO messages_fts(rowid"):
                    traced[:] = [sql]
            conn.set_trace_callback(trace)
            written = 0
            for row in source_rows(min(count, int(config["sync"]["batch_size"]))):
                peer, identity, stamp, text, *_ = row
                history.upsert_message(conn, SimpleNamespace(id=peer), SimpleNamespace(id=identity,
                    date=datetime.fromisoformat(stamp), message=text, media=None), None, None, None, None)
                written += 1
            conn.set_trace_callback(None)
            stage("indexed_upserts", clock)
            result["indexed_write_rows"] = written
            result["indexed_write_plan"] = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + traced[0])]
            result["source_bytes"] = conn.execute("SELECT SUM(length(CAST(text AS BLOB))) FROM messages").fetchone()[0]
            result["source_bytes"] += conn.execute("SELECT COALESCE(SUM(length(CAST(ocr_text AS BLOB))),0) FROM media_assets").fetchone()[0]
            runtime = history.RuntimeConfig(db_path=path, media_root=Path(folder) / "media", user_session_name="fixture-user", bot_session_name="fixture-bot",
                api_id="1", api_hash="fixture", phone="fixture", bot_token="fixture", user_password="", tesseract_binary="tesseract", vision_prompt="",
                sync_batch_size=500, default_auth_mode="user", public_auth_mode="bot", private_auth_mode="user", default_channels=[],
                connection_timeout_seconds=60, connection_attempts=3, connection_retry_backoff_seconds=2, connection_cleanup_timeout_seconds=10)
            access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
            for number, command in enumerate(("topics", "search", "qa"), 1):
                query = ' "Документы Uno" --mode full' if command == "search" else ""
                event = {"update_id": number, "message": {"chat": {"id": 100}, "from": {"id": 7},
                    "text": f"/{command} @two,@one since=2026-10-10 until=2026-10-12" + query}}
                queued, _ = analysis.admit_analysis_job(store, config, event, access=access, bot_namespace="telegram:999", now=NOW,
                    config_dir=history.APP_DIR / "config")
                job = store.claim_job(queued["job_id"], max_running_jobs=1, now=NOW)
                store.save_checkpoint(job["job_id"], {"history": {"refs": {"@two": 22, "@one": 11}, "sources": {
                    str(peer): {"mode": "reuse", "state": "complete"} for peer in (22, 11)}}}, generation=job["generation"], max_bytes=67108864)
                stats = {"count_calls": 0, "inference_calls": 0, "max_input_units": 0, "max_inference_input_units": 0,
                    "max_output_units": 0, "max_batch_messages": 0}
                clock = time.monotonic()
                status = analysis.execute_analysis_job(store, store.get_job(job["job_id"]), runtime=runtime,
                    client=EngineHistoryClient(store, {22: [], 11: []}), api_key="fixture", token="fixture", access_provider=lambda: access,
                    urlopen_func=bounded_provider(job, stats), send_func=lambda token, chat, text, parse_mode, **kw: {"message_id": 1, "chat": {"id": 100}})
                store.finish_job(job["job_id"], generation=job["generation"], status=status, now=NOW)
                saved = store.get_job(job["job_id"])
                stage(command, clock)
                manifest = saved["checkpoint"]["manifest"]
                result["commands"].append({"command": command, "status": status, "selected": manifest["selected"],
                    "processed": store.processed_analysis_messages(job["job_id"]), "partial": saved["result"]["partial"],
                    "reasons": saved["result"].get("reasons", []), "storage": store.storage_usage(job["job_id"]),
                    "llm_calls": saved["llm_calls"], "provider_requests": saved["provider_requests"],
                    "used_tokens": saved["used_tokens"], "reserved_tokens": saved["reserved_tokens"], **stats})
            result["sqlite_bytes"] = path.stat().st_size
            result["wal_bytes"] = path.with_name(path.name + "-wal").stat().st_size if path.with_name(path.name + "-wal").exists() else 0
            result["cache_bytes"] = store.cache_storage_bytes()
            if cache_probe:
                before = {row[0]: row[1] for row in conn.execute("SELECT job_id,result_json FROM analysis_jobs WHERE kind='analysis'")}
                result["cache_probe"] = store.maintain_analysis_storage(now=NOW, max_cache_bytes=1)
                result["cache_probe"]["retained_results"] = all(store.get_job(key)["result"] == json.loads(value) for key, value in before.items())
            result["plans"] = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN SELECT message_id FROM messages WHERE channel_id=22 AND date_utc>=? ORDER BY date_utc,message_id LIMIT 500", ("2026-10-10",))]
        finally:
            conn.close()
    result.update(peak_rss_bytes=peak_rss(), elapsed_seconds=time.monotonic() - started)
    result["code_hashes"] = {name: hashlib.sha256((history.APP_DIR / name).read_bytes()).hexdigest()
        for name in ("telegram_analysis.py", "analysis_store.py", "telegram_history_client.py", "analysis_evidence.py")}
    json_io.write_json_atomic(report, result)
    print(json.dumps({"report": str(report), "peak_rss_bytes": result["peak_rss_bytes"], "elapsed_seconds": result["elapsed_seconds"]}), flush=True)
    return result


@pytest.mark.skipif(os.environ.get("RUN_TELEGRAM_ANALYSIS_LOAD") != "1", reason="Explicit resource acceptance, frozen host baseline required")
@pytest.mark.parametrize("count", [10000, 100000, 200000])
def test_s35_resource_bounds_three_commands_on_mixed_corpus(tmp_path, count):
    policy = json.loads(POLICY.read_text())
    report = tmp_path / f"load-{count}.json"
    completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--count", str(count), "--report", str(report)],
        timeout=3600, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert completed.returncode == 0, "Resource worker failed; see its streamed stage output"
    measured = json.loads(report.read_text())
    assert measured["fts_rows"] == count and measured["peak_rss_bytes"] <= policy["max_peak_rss_bytes"]
    assert {job["command"] for job in measured["commands"]} == {"topics", "search", "qa"}
    for job in measured["commands"]:
        assert 0 < job["processed"] <= job["selected"] <= count
        assert job["storage"]["allocated_bytes"] <= 67108864 and job["llm_calls"] <= 256 and job["provider_requests"] <= 1024
        assert job["used_tokens"] + job["reserved_tokens"] <= 2000000 and job["max_batch_messages"] <= 500
        assert job["max_inference_input_units"] <= 16000
        assert job["processed"] == count or job["partial"]
    assert any("INDEX" in plan for plan in measured["plans"])
    assert any("SEARCH m " in plan and "message_id" in plan for plan in measured["indexed_write_plan"])
    json_io.write_json_atomic(POLICY.parent / f"load-{count}.json", measured)


def test_s35_resource_bounds_real_cache_exhaustion_keeps_final_results(tmp_path):
    report = tmp_path / "cache-probe.json"
    completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--count", "100", "--report", str(report), "--cache-probe"],
        capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    measured = json.loads(report.read_text())
    assert measured["cache_bytes"] > 1 and measured["cache_probe"]["cache_bytes"] <= 1
    assert measured["cache_probe"]["evicted_batches"] > 0 and measured["cache_probe"]["retained_results"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cache-probe", action="store_true")
    args = parser.parse_args()
    measure(args.count, args.report, cache_probe=args.cache_probe)
