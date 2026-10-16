import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed
from telegram_connector.tests.test_analysis_transport import Response, transport_job
from telegram_connector.tests.test_analysis_batching import prepare, payload, CAPABILITY
from telegram_connector.tests.test_analysis_budget import plan, setting
from telegram_connector.tests.test_analysis_history import runtime, PageClient, posts
from telegram_connector.tests.test_analysis_evidence import document, model_response
from telegram_connector.tests.test_analysis_artifacts import artifact_job
from telegram_connector.tests.test_analysis_manifest import change_setting
from telegram_connector import telegram_history_client as history


@pytest.mark.parametrize("model,context,input_limit,source", [
    ("gpt-5.4-mini", 400000, 272000, "gpt-5.4-mini"),
    ("gpt-5.4-mini-2026-03-17", 400000, 272000, "gpt-5.4-mini"),
    ("gpt-5.6-luna", 1050000, 922000, "gpt-5.6-luna"),
])
def test_s24_engine_wiring_capability_owner_uses_verified_exact_model_metadata(model, context, input_limit, source):
    function = getattr(analysis, "resolve_analysis_model_capabilities", None)
    assert callable(function), "Production preflight needs a source-backed capability owner"
    actual = function(model)
    assert actual["model"] == model and actual["context_window"] == context
    assert actual["max_input_tokens"] == input_limit and actual["max_output_tokens"] == 128000
    assert actual["source"] == f"https://developers.openai.com/api/docs/models/{source}"
    actual["context_window"] = 1
    assert function(model)["context_window"] == context


def test_s24_engine_wiring_unknown_capability_is_not_guessed():
    function = getattr(analysis, "resolve_analysis_model_capabilities", None)
    assert callable(function)
    with pytest.raises(ValueError, match="model_capability_unverified"):
        function("gpt-5.4-mini-custom-alias")


def test_s24_engine_wiring_model_input_cap_is_distinct_from_total_context(transport_job):
    store, job = transport_job
    batch = plan(store, job)
    setting(store, job, "max_input_tokens", 150)
    capability = {**CAPABILITY, "max_input_tokens": 30}
    with pytest.raises(ValueError, match="max_input_tokens"):
        prepare(store, job, batch, payload(), lambda *a, **k: Response({"input_tokens": 31}), capabilities=capability)
    assert store.get_job(job["job_id"])["llm_calls"] == 0


@pytest.mark.parametrize("stage", ["extract", "link", "merge", "synthesize"])
@pytest.mark.parametrize("summary", ["none", "auto"])
def test_s24_model_settings_reach_count_and_inference(manifest_job, stage, summary):
    from telegram_connector.tests.test_analysis_relations import records_fixture, link, link_sender
    from telegram_connector.tests.test_analysis_merge import merge, merge_sender
    from telegram_connector.tests.test_analysis_evidence import extract
    from telegram_connector.tests.test_analysis_batching import batches
    from telegram_connector.tests.test_analysis_manifest import build
    from telegram_connector.tests.test_analysis_synthesis import synthesize, synthesis_sender

    store, job = manifest_job
    change_setting(store, job, "digest_ai", "reasoning_effort", "low")
    change_setting(store, job, "digest_ai", "reasoning_summary", summary)
    job = store.get_job(job["job_id"])
    sent = []

    def capture(sender):
        def send(req, timeout):
            sent.append((req.full_url, json.loads(req.data)))
            return sender(req, timeout)
        return send

    if stage == "extract":
        seed(store, 22, [1], text="Минимум 1250 EUR.")
        build(store, job)
        batch = next(batches(store, job))
        extract(store, job, batch, capture(lambda req, timeout: Response(
            {"input_tokens": 100} if req.full_url.endswith("input_tokens") else model_response(document(batch)))))
    else:
        records_fixture(store, job, [{"claim": "Минимум 1250 EUR."}, {"claim": "Срок 30 дней."}])
        if stage == "link":
            link(store, job, capture(link_sender("complements")))
        elif stage == "merge":
            merge(store, job, capture(merge_sender()))
        else:
            merge(store, job, merge_sender())
            synthesize(store, job, capture(synthesis_sender()))
    expected = {"effort": "low", **({"summary": summary} if summary != "none" else {})}
    assert any(url.endswith("input_tokens") for url, _ in sent)
    assert any(url.endswith("responses") for url, _ in sent)
    assert all(data.get("reasoning") == expected for _, data in sent)


class Channel(SimpleNamespace):
    def to_dict(self): return {"id": self.id, "username": self.username}


class EngineHistoryClient(PageClient):
    async def get_entity(self, ref):
        self.entities.append(ref)
        peer = 22 if ref == "@two" else 11
        return Channel(id=peer, username=f"peer{peer}", title=f"Peer {peer}")


def engine_provider(store, job, calls):
    def send(req, timeout):
        data = json.loads(req.data)
        assert not store.conn.in_transaction
        if req.full_url.endswith("input_tokens"):
            return Response({"input_tokens": 100})
        kind = data["text"]["format"]["name"]
        calls.append(kind)
        if kind == "telegram_evidence_v1":
            fragments = json.loads(data["input"].split("Сообщения: ", 1)[1])
            records = []
            for fragment in fragments:
                value = document({"messages": [fragment]})["records"][0]
                value.update(claim=fragment["message"]["text"], conditions=[], exceptions=[])
                records.append(value)
            output = {"records": records, "saturated": False}
        elif kind == "telegram_relations_v1":
            pairs = json.loads(data["input"].split("Кандидаты: ", 1)[1])["pairs"]
            output = {"decisions": [{"left_id": a, "right_id": b, "relation": "unrelated", "reason": "Разные факты"} for a, b in pairs], "saturated": False}
        elif kind == "telegram_merge_v1":
            inputs = json.loads(data["input"].split("Записи: ", 1)[1])["inputs"]
            ids = [value["node_id"] for value in inputs]
            output = {"groups": [{"title": "Условия", "summary": "Все факты сохранены в lineage", "input_ids": ids}],
                "dispositions": [{"input_id": key, "disposition": "merged", "reason": "Включено"} for key in ids], "saturated": False}
        else:
            assert kind == "telegram_synthesis_v1"
            records = json.loads(data["input"].split("Опорные записи: ", 1)[1])["records"]
            output = {"summary": "Условия из истории", "items": [{"title": "Факт", "text": r["payload"]["claim"],
                "conditions": [], "exceptions": [], "contradictions": [], "evidence_ids": [r["record_id"]]} for r in records],
                "limitations": [], "insufficient_information": not records, "saturated": False}
        return Response(model_response(output))
    return send


def execute(store, job, **kwargs):
    function = getattr(analysis, "execute_analysis_job", None)
    assert callable(function), "The worker needs one engine composing real bounded stages"
    return function(store, job, **kwargs)


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("limit", [1, 3])
def test_review_enabled_ocr_precedes_manifest_with_shared_scoped_limit(manifest_job, runtime, monkeypatch, command, enabled, limit):
    store, job = manifest_job
    seed(store, 22, [1, 2])
    seed(store, 22, [3], date="2026-10-01T00:00:00+00:00")
    seed(store, 11, [1])
    for peer, message in [(22, 2), (22, 3), (11, 1)]:
        store.conn.execute("""INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,local_path,ocr_status,created_at)
            VALUES (?,?,0,'photo',?,'pending','now')""", (peer, message, f"{peer}-{message}.png"))
    data = copy.deepcopy(store.get_job(job["job_id"])["request"])
    data["request"].update(command=command, mode="fast" if command == "search" else None, query="OCR" if command == "search" else None)
    data["settings"]["processing"]["ocr"] = enabled
    runtime.ocr_pending_default_limit = limit
    store.conn.execute("UPDATE analysis_jobs SET command=?,request_json=? WHERE job_id=?", (command, analysis.canonical_json(data), job["job_id"]))
    async def sync(*args, **kwargs):
        assert store.get_job(job["job_id"])["phase"] == "sync"
        store.conn.execute("""INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,local_path,ocr_status,created_at)
            VALUES (22,1,0,'photo','22-1.png','pending','now')""")
    monkeypatch.setattr(history, "ensure_analysis_history", sync)
    monkeypatch.setattr(history, "require_fts_ready", lambda *a: None)
    calls = []
    def recognize(binary, path, *, timeout_seconds):
        assert not store.conn.in_transaction and 0 < timeout_seconds <= 60
        calls.append(path)
        return "OCR: 9999 EUR"
    monkeypatch.setattr(history, "run_tesseract", recognize)
    class Selected(Exception):
        pass
    build = analysis.build_all_messages_manifest
    def select(*args, **kwargs):
        manifest = build(*args, generation=kwargs["generation"])
        rows = list(analysis.iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"]))
        assert ("OCR: 9999 EUR" in str(rows)) is enabled
        assert calls == (["22-1.png", "22-2.png", "11-1.png"][:limit] if enabled else [])
        assert ("ocr_partial" in manifest["reasons"]) is (enabled and limit == 1)
        raise Selected
    monkeypatch.setattr(analysis, "build_all_messages_manifest", select)
    monkeypatch.setattr(analysis, "build_search_manifest", select)
    with pytest.raises(Selected):
        execute(store, job, runtime=runtime, capabilities={"model": "fixture"}, api_key="fixture", access_provider=engine_access)
    assert store.conn.execute("SELECT COUNT(*) FROM media_assets WHERE ocr_status='pending'").fetchone()[0] == 4 - (limit if enabled else 0)


def test_review_source_edit_resume_sends_recomputed_text_not_old_plan(manifest_job, runtime, monkeypatch):
    from telegram_connector.tests.test_analysis_delivery import sourced_result
    from telegram_connector.tests.test_analysis_manifest import resume
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    presentation = analysis.prepare_analysis_presentation(store, job["job_id"], result, generation=job["generation"])
    store.prepare_delivery(job["job_id"], generation=job["generation"], chunks=presentation["chunks"],
        parse_mode="HTML", detail="normal", analysis_partial=False)
    store.conn.execute("UPDATE messages SET text=replace(text,'1250','9999') WHERE channel_id=22 AND message_id=1")
    sent = []
    with pytest.raises(ValueError, match="input_changed"):
        execute(store, job, token="fixture", access_provider=engine_access, send_func=lambda *a, **k: pytest.fail("Changed input must stop delivery"))
    resumed = resume(store, job)
    async def synced(*args, **kwargs):
        pass
    monkeypatch.setattr(history, "ensure_analysis_history", synced)
    execute(store, resumed, runtime=runtime, api_key="fixture", token="fixture", access_provider=engine_access,
        capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
        urlopen_func=engine_provider(store, resumed, []),
        send_func=lambda token, chat, text, mode, **kw: sent.append(text) or {"message_id": len(sent), "chat": {"id": 100}})
    assert sent and "9999 EUR" in "\n".join(sent) and "1250 EUR" not in "\n".join(sent)


def test_s24_engine_wiring_actual_stage_trace_and_first_middle_last_facts(manifest_job, runtime, tmp_path, monkeypatch, request):
    from common import sqlite as common_sqlite
    from telegram_connector.analysis_store import AnalysisStore
    store, job = manifest_job
    store.save_checkpoint(job["job_id"], {}, generation=job["generation"], max_bytes=10000)
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 2)
    change_setting(store, job, "analysis", "merge_fan_in", 2)
    values = posts([6, 5, 4, 3, 2, 1])
    for message in values:
        message.date = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
        message.message = f"Факт {message.id}: сумма {message.id * 100} EUR."
    client = EngineHistoryClient(store, {22: values, 11: []})
    calls, sent = [], []
    store.conn.execute("CREATE TABLE engine_phases (phase TEXT)")
    store.conn.execute("""CREATE TRIGGER trace_engine_phase AFTER UPDATE OF phase ON analysis_jobs
        BEGIN INSERT INTO engine_phases VALUES (NEW.phase); END""")
    path = tmp_path / "worker.sqlite3"
    conn = common_sqlite.connect_sqlite(path)
    store.conn.backup(conn)
    conn.close()
    engine = analysis.execute_analysis_job
    def wired(store, current):
        client.store = store
        return engine(store, current, runtime=runtime, client=client,
            api_key="fixture-key", token="fixture-token", capabilities={"model": "fixture-model", "context_window": 400000,
                "max_output_tokens": 128000, "source": "fixture"}, access_provider=engine_access,
            urlopen_func=engine_provider(store, current, calls), send_func=lambda token, chat, text, mode, **kw:
                sent.append(text) or {"message_id": len(sent), "chat": {"id": 100}})
    monkeypatch.setattr(analysis, "execute_analysis_job", wired)
    assert analysis.main(["--db", str(path), "--job-id", job["job_id"], "--generation", str(job["generation"]), "--worker-inner"]) == 0
    conn = common_sqlite.connect_sqlite(path)
    request.addfinalizer(conn.close)
    store = AnalysisStore(conn)
    saved = store.get_job(job["job_id"])
    assert saved["status"] == "completed" and saved["delivery"]["outcome"] == "confirmed"
    text = "\n".join(sent)
    assert all(f"Факт {n}: сумма {n * 100} EUR" in text for n in (1, 3, 6))
    assert calls.count("telegram_evidence_v1") >= 3
    assert {"telegram_relations_v1", "telegram_merge_v1", "telegram_synthesis_v1"} <= set(calls)
    phases = [row[0] for row in store.conn.execute("SELECT phase FROM engine_phases")]
    unique = [p for i, p in enumerate(phases) if i == 0 or phases[i-1] != p]
    assert unique == ["sync", "select", "extract", "link", "merge", "synthesize", "verify", "deliver"]


def engine_access():
    return {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}


def test_s24_engine_wiring_inner_cli_dispatches_real_saved_result_without_ai(manifest_job, tmp_path, monkeypatch):
    from common import sqlite as common_sqlite
    from telegram_connector.analysis_store import AnalysisStore
    from telegram_connector.tests.test_analysis_delivery import sourced_result
    store, job = manifest_job
    store.save_analysis_result(job["job_id"], sourced_result(store, job), generation=job["generation"])
    path = tmp_path / "worker.sqlite3"
    conn = common_sqlite.connect_sqlite(path)
    store.conn.backup(conn)
    conn.close()
    monkeypatch.setattr(analysis.bridge, "load_runtime_config", lambda: {"bridge": {
        "allowed_chat_ids": "100", "allowed_user_ids": "7", "allowed_usernames": ""}})
    monkeypatch.setattr(analysis.bridge, "require_token", lambda: "fixture-token")
    monkeypatch.setattr(analysis, "resolve_analysis_model_capabilities", lambda *a: pytest.fail("Saved result needs no model"))
    monkeypatch.setattr(analysis.history_client, "resolve_runtime", lambda: pytest.fail("Saved result needs no session"))
    sent = []
    monkeypatch.setattr(analysis.bridge, "send_analysis_text_once", lambda token, chat, text, mode, **kw:
        sent.append(text) or {"message_id": len(sent), "chat": {"id": 100}})
    assert analysis.main(["--db", str(path), "--job-id", job["job_id"], "--generation", str(job["generation"]), "--worker-inner"]) == 0
    conn = common_sqlite.connect_sqlite(path)
    try:
        saved = AnalysisStore(conn).get_job(job["job_id"])
        assert saved["status"] == "completed" and saved["delivery"]["outcome"] == "confirmed"
        assert saved["llm_calls"] == 0 and sent
    finally:
        conn.close()


@pytest.mark.parametrize("outcome", ["confirmed", "unknown"])
def test_s24_engine_wiring_saved_delivery_needs_no_token_or_resend(manifest_job, monkeypatch, outcome):
    from telegram_connector.tests.test_analysis_delivery import sourced_result
    from telegram_shared.errors import TelegramApiError
    store, job = manifest_job
    store.save_analysis_result(job["job_id"], sourced_result(store, job), generation=job["generation"])
    def send(*a, **kw):
        if outcome == "unknown":
            raise TelegramApiError("fixture unknown")
        return {"message_id": 1, "chat": {"id": 100}}
    execute(store, job, token="fixture", access_provider=engine_access, send_func=send)
    monkeypatch.setattr(analysis.bridge, "require_token", lambda: pytest.fail("No further send needs a token"))
    status = execute(store, job, access_provider=engine_access,
        send_func=lambda *a, **k: pytest.fail("Confirmed/unknown delivery must not repeat"))
    assert status == "completed"
    assert store.get_job(job["job_id"])["delivery"]["outcome"] == outcome


@pytest.mark.parametrize("failure,status,code", [(TimeoutError, "timed_out", 124), (InterruptedError, "cancelled", 0)])
def test_s24_engine_wiring_worker_boundary_classifies_stop(manifest_job, tmp_path, failure, status, code):
    store, job = manifest_job
    def stop(store, current):
        if failure is InterruptedError:
            store.request_cancel(job["job_id"], now=datetime.now(timezone.utc))
        raise failure("fixture stop")
    actual = analysis.run_analysis(store, tmp_path / "worker.sqlite3", job_id=job["job_id"], generation=job["generation"], executor=stop)
    assert actual == code and store.get_job(job["job_id"])["status"] == status


@pytest.mark.parametrize("boundary", ["sync", "select", "extract", "link", "merge", "synthesize", "verify", "deliver"])
def test_s24_engine_wiring_resume_each_boundary_keeps_completed_work(manifest_job, runtime, tmp_path, monkeypatch, boundary):
    from telegram_connector.tests.test_analysis_manifest import resume
    store, job = manifest_job
    store.save_checkpoint(job["job_id"], {}, generation=job["generation"], max_bytes=10000)
    values = posts([2, 1])
    for message in values:
        message.date = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
        message.message = f"Факт {message.id}: сумма {message.id * 100} EUR."
    client = EngineHistoryClient(store, {22: values, 11: []})
    original = store.set_analysis_phase
    stopped = []
    def phase(*args, **kwargs):
        if kwargs["phase"] == boundary and not stopped:
            stopped.append(True)
            raise InterruptedError("fixture boundary interruption")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "set_analysis_phase", phase)
    requests, calls, sent = [], [], []
    provider = engine_provider(store, job, calls)
    def send(req, timeout):
        if req.full_url.endswith("responses"):
            requests.append(req.data)
        return provider(req, timeout)
    def executor(store, current):
        return execute(store, current, runtime=runtime, client=client, api_key="fixture", token="fixture",
            capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
            access_provider=engine_access, urlopen_func=send,
            send_func=lambda token, chat, text, mode, **kw: sent.append(text) or {"message_id": 1, "chat": {"id": 100}})
    path = tmp_path / "worker.sqlite3"
    assert analysis.run_analysis(store, path, job_id=job["job_id"], generation=job["generation"], executor=executor) != 0
    assert store.get_job(job["job_id"])["status"] == "interrupted"
    continued = resume(store, job)
    assert analysis.run_analysis(store, path, job_id=job["job_id"], generation=continued["generation"], executor=executor) == 0
    current = store.get_job(job["job_id"])
    assert current["status"] == "completed" and current["delivery"]["outcome"] == "confirmed"
    assert len(requests) == len(set(requests)), "Completed inference must not repeat after a boundary interruption"
    assert len(sent) == 1


def test_s24_engine_wiring_artifact_failure_keeps_computation_resumable(manifest_job, tmp_path, monkeypatch):
    from telegram_connector.tests.test_analysis_artifacts import full_result
    store, job = manifest_job
    result = full_result(store, job)
    root = tmp_path / "repo" / "telegram_connector"
    root.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(root))
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", lambda *a: (_ for _ in ()).throw(OSError("fixture disk error")))
    def executor(store, current):
        return execute(store, current, token="fixture", access_provider=engine_access,
            send_func=lambda *a, **k: pytest.fail("No artifact means no path delivery"))
    assert analysis.run_analysis(store, tmp_path / "worker.sqlite3", job_id=job["job_id"], generation=job["generation"], executor=executor) == 1
    current = store.get_job(job["job_id"])
    assert current["status"] == "completed" and current["result"] == result
    assert current["delivery"]["outcome"] == "failed" and current["delivery"]["error"] == "artifact_unavailable"


@pytest.mark.parametrize("confirmed", [False, True])
def test_s24_engine_wiring_explicit_resume_restores_missing_full(artifact_job, monkeypatch, confirmed):
    import tomllib
    from telegram_shared.errors import TelegramApiError
    store, job = artifact_job
    def send(*args, **kwargs):
        if not confirmed:
            raise TelegramApiError("fixture refused", request_outcome="refused", error_code=403)
        return {"message_id": 1, "chat": {"id": 100}}
    execute(store, job, token="fixture", access_provider=engine_access,
        send_func=send)
    ready = store.get_job(job["job_id"])
    target, relative = analysis.resolve_analysis_artifact(ready)
    text = target.read_bytes()
    target.unlink()
    store.finish_job(job["job_id"], generation=job["generation"], status="completed", now=datetime.now(timezone.utc))
    folder = history.APP_DIR / "config"
    config = tomllib.loads((folder / "runtime.example.toml").read_text())
    config["processing"]["model"] = "fixture-model"
    config["sync"]["batch_size"] = 2
    event = {"message": {"chat": {"id": 100}, "from": {"id": 7}}}
    analysis.control_analysis_job(store, config, event, command="resume", job_id=job["job_id"],
        access=engine_access(), now=datetime.now(timezone.utc), config_dir=folder)
    continued = store.claim_job(job["job_id"], max_running_jobs=1, now=datetime.now(timezone.utc))
    monkeypatch.setattr(analysis.bridge, "require_token", lambda: pytest.fail("Token resolution is unnecessary"))
    def deliver(*args, **kwargs):
        assert not confirmed, "Confirmed receipt must not send"
        return {"message_id": 2, "chat": {"id": 100}}
    assert execute(store, continued, token=None if confirmed else "fixture", access_provider=engine_access,
        send_func=deliver) == "completed"
    saved = store.get_job(job["job_id"])
    assert target.read_bytes() == text and saved["delivery"]["outcome"] == "confirmed"
    if confirmed:
        assert saved["delivery"] == ready["delivery"]


def test_s24_engine_wiring_edited_full_rejects_resume_as_control_error(artifact_job):
    store, job = artifact_job
    execute(store, job, token="fixture", access_provider=engine_access,
        send_func=lambda *a, **k: {"message_id": 1, "chat": {"id": 100}})
    target, _ = analysis.resolve_analysis_artifact(store.get_job(job["job_id"]))
    target.write_text("Мои изменения")
    store.finish_job(job["job_id"], generation=job["generation"], status="completed", now=datetime.now(timezone.utc))
    with pytest.raises(analysis.AnalysisRejection, match="not_resumable"):
        analysis.control_analysis_job(store, {}, {"message": {"chat": {"id": 100}, "from": {"id": 7}}},
            command="resume", job_id=job["job_id"], access=engine_access(), now=datetime.now(timezone.utc), config_dir=history.APP_DIR / "config")
    assert target.read_text() == "Мои изменения"


@pytest.mark.parametrize("user_edit", [False, True])
def test_s24_engine_wiring_changed_result_recovers_only_owned_orphan(artifact_job, monkeypatch, user_edit):
    store, job = artifact_job
    original = analysis.common_json_io.write_text_atomic
    def mutate(path, text):
        original(path, text)
        result = copy.deepcopy(store.get_job(job["job_id"])["result"])
        result["summary"] = "Обновлённый результат"
        store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", mutate)
    with pytest.raises(ValueError, match="Artifact result changed"):
        analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
    target, _ = analysis.resolve_analysis_artifact(store.get_job(job["job_id"]))
    if user_edit:
        target.write_text("Мои изменения")
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", original)
    if user_edit:
        with pytest.raises(ValueError, match="artifact_hash_conflict"):
            analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
        assert target.read_text() == "Мои изменения"
    else:
        analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
        assert "Обновлённый результат" in target.read_text()
        assert analysis.verify_full_analysis_artifact(store.get_job(job["job_id"]))


def test_s24_engine_wiring_owned_orphan_replacement_retries_after_write_failure(artifact_job, monkeypatch):
    store, job = artifact_job
    original = analysis.common_json_io.write_text_atomic
    def change_result(path, text):
        original(path, text)
        result = copy.deepcopy(store.get_job(job["job_id"])["result"])
        result["summary"] = "Вторая версия"
        store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", change_result)
    with pytest.raises(ValueError, match="Artifact result changed"):
        analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", lambda *a: (_ for _ in ()).throw(OSError("fixture replace failure")))
    with pytest.raises(OSError, match="replace failure"):
        analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", original)
    analysis.write_full_analysis_artifact(store, job["job_id"], generation=job["generation"])
    target, _ = analysis.resolve_analysis_artifact(store.get_job(job["job_id"]))
    assert "Вторая версия" in target.read_text()


def test_s24_engine_wiring_full_source_edit_reanalyses_and_recovers_orphan(artifact_job, runtime, tmp_path, monkeypatch):
    from telegram_connector.tests.test_analysis_manifest import resume
    store, job = artifact_job
    writer = analysis.common_json_io.write_text_atomic
    def change_source(path, text):
        writer(path, text)
        store.conn.execute("UPDATE messages SET text='Новый минимум: 2500 EUR.' WHERE channel_id=22")
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", change_source)
    def executor(store, current):
        return execute(store, current, runtime=runtime, client=EngineHistoryClient(store, {22: [], 11: []}),
            token="fixture", api_key="fixture", access_provider=engine_access,
            capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
            urlopen_func=engine_provider(store, current, []),
            send_func=lambda *a, **k: {"message_id": 1, "chat": {"id": 100}})
    path = tmp_path / "worker.sqlite3"
    assert analysis.run_analysis(store, path, job_id=job["job_id"], generation=job["generation"], executor=executor) != 0
    assert store.get_job(job["job_id"])["status"] == "interrupted"
    monkeypatch.setattr(analysis.common_json_io, "write_text_atomic", writer)
    continued = resume(store, job)
    assert analysis.run_analysis(store, path, job_id=job["job_id"], generation=continued["generation"], executor=executor) == 0
    saved = store.get_job(job["job_id"])
    target, _ = analysis.resolve_analysis_artifact(saved)
    assert "2500 EUR" in target.read_text() and "1250 EUR" not in target.read_text()
    assert saved["delivery"]["outcome"] == "confirmed"


def test_s24_engine_wiring_cancellation_racing_error_keeps_cancelled_state(manifest_job, tmp_path):
    store, job = manifest_job
    def executor(store, current):
        store.request_cancel(job["job_id"], now=datetime.now(timezone.utc))
        raise ValueError("fixture error after cancel")
    assert analysis.run_analysis(store, tmp_path / "worker.sqlite3", job_id=job["job_id"],
        generation=job["generation"], executor=executor) == 1
    assert store.get_job(job["job_id"])["status"] == "cancelled"


def test_s24_engine_wiring_budget_stop_is_partial_at_engine_boundary(manifest_job, runtime):
    store, job = manifest_job
    seed(store, 22, [1])
    change_setting(store, job, "analysis", "max_llm_calls", 1)
    status = execute(store, job, runtime=runtime, client=EngineHistoryClient(store, {22: [], 11: []}),
        token="fixture", api_key="fixture", access_provider=engine_access,
        capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
        urlopen_func=engine_provider(store, job, []),
        send_func=lambda *a, **k: {"message_id": 1, "chat": {"id": 100}})
    saved = store.get_job(job["job_id"])
    assert status == "partial" and saved["result"]["partial"]
    assert saved["llm_calls"] <= 1 and "max_llm_calls" in saved["result"]["reasons"]


@pytest.mark.parametrize("failure", ["source", "batch_refusal"])
def test_s24_engine_wiring_partial_inputs_keep_successful_facts(manifest_job, runtime, failure):
    from telethon import errors
    store, job = manifest_job
    store.save_checkpoint(job["job_id"], {}, generation=job["generation"], max_bytes=10000)
    change_setting(store, job, "digest_ai", "messages_per_ai_pass", 1)
    values = posts([2, 1])
    for message in values:
        message.date = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
        message.message = f"Факт {message.id}: сумма {message.id * 100} EUR."
    class Client(EngineHistoryClient):
        async def get_entity(self, ref):
            if failure == "source" and ref == "@one":
                raise errors.ChannelPrivateError(request=None)
            return await super().get_entity(ref)
    provider = engine_provider(store, job, [])
    refused = []
    def send(req, timeout):
        data = json.loads(req.data)
        if (failure == "batch_refusal" and req.full_url.endswith("responses")
                and data["text"]["format"]["name"] == "telegram_evidence_v1" and not refused):
            refused.append(True)
            return Response({"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "fixture refusal"}]}], "usage": {"input_tokens": 100, "output_tokens": 10}})
        return provider(req, timeout)
    sent = []
    assert execute(store, job, runtime=runtime, client=Client(store, {22: values, 11: []}),
        token="fixture", api_key="fixture", access_provider=engine_access,
        capabilities={"model": "fixture-model", "context_window": 400000, "max_output_tokens": 128000, "source": "fixture"},
        urlopen_func=send, send_func=lambda token, chat, text, mode, **kw:
            sent.append(text) or {"message_id": 1, "chat": {"id": 100}}) == "partial"
    assert "Факт 2: сумма 200 EUR" in "\n".join(sent)
    assert store.get_job(job["job_id"])["result"]["partial"]


@pytest.mark.parametrize("invalid", ["phase", "generation", "cancel"])
def test_s24_engine_wiring_phase_writer_rejects_invalid_owners(manifest_job, invalid):
    from telegram_connector.analysis_store import StaleGenerationError
    store, job = manifest_job
    if invalid == "cancel":
        store.request_cancel(job["job_id"], now=datetime.now(timezone.utc))
    expected = ValueError if invalid == "phase" else StaleGenerationError if invalid == "generation" else InterruptedError
    before = store.get_job(job["job_id"])["phase"]
    with pytest.raises(expected):
        store.set_analysis_phase(job["job_id"], generation=job["generation"] - (invalid == "generation"),
                                 phase="invented" if invalid == "phase" else "merge")
    assert store.get_job(job["job_id"])["phase"] == before and not store.conn.in_transaction
