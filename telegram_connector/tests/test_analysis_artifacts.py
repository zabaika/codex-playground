from pathlib import Path
import copy
import hashlib
import stat

import pytest

from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_delivery import sourced_result, frozen_output_setting


def full_result(store, job, *, record_type="claim"):
    result = sourced_result(store, job, record_type=record_type)
    frozen_output_setting(store, job, lambda p: p["request"].update(detail="full"))
    result["detail"] = "full"
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    return result


def resolve(job):
    function = getattr(analysis, "resolve_analysis_artifact", None)
    assert callable(function), "Full artifacts must resolve relative to the canonical source repo"
    return function(job)


@pytest.mark.parametrize("copied", [False, True])
def test_s23_full_markdown_canonical_scratch_ignores_cwd_and_copied_runtime(manifest_job, tmp_path, monkeypatch, copied):
    store, job = manifest_job
    full_result(store, job)
    source = tmp_path / "repo" / "telegram_connector"
    source.mkdir(parents=True)
    runtime = tmp_path / "service" if copied else source
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(analysis, "__file__", str(runtime / "telegram_analysis.py"))
    monkeypatch.chdir(tmp_path)
    if copied:
        monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(source))
    else:
        monkeypatch.delenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", raising=False)
    absolute, relative = resolve(store.get_job(job["job_id"]))
    expected = f"scratch/telegram-connector-analytics/results/{job['job_id']}.md"
    assert absolute == source.parent / expected and relative == expected
    assert not absolute.parent.exists()


def test_s23_full_markdown_copied_runtime_without_source_root_fails_closed(manifest_job, tmp_path, monkeypatch):
    store, job = manifest_job
    full_result(store, job)
    monkeypatch.setattr(analysis, "__file__", str(tmp_path / "telegram_connector_service" / "telegram_analysis.py"))
    monkeypatch.delenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", raising=False)
    with pytest.raises(ValueError, match="project root"):
        resolve(store.get_job(job["job_id"]))


def test_s23_full_markdown_symlink_cannot_redirect_report_outside_repo(manifest_job, tmp_path, monkeypatch):
    store, job = manifest_job
    full_result(store, job)
    source = tmp_path / "repo" / "telegram_connector"
    source.mkdir(parents=True)
    elsewhere = tmp_path / "outside"
    elsewhere.mkdir()
    (source.parent / "scratch").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(source))
    with pytest.raises(ValueError, match="scratch"):
        resolve(store.get_job(job["job_id"]))
    assert list(elsewhere.iterdir()) == []


def render(store, job):
    function = getattr(analysis, "render_full_analysis_markdown", None)
    assert callable(function), "Full reports need a bounded Markdown view of verified structured data"
    return function(store, job["job_id"], generation=job["generation"])


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
def test_s23_full_markdown_contains_scope_question_facts_and_real_sources(manifest_job, command):
    store, job = manifest_job
    result = full_result(store, job, record_type="answer" if command == "qa" else "claim")
    store.conn.execute("UPDATE analysis_jobs SET command=? WHERE job_id=?", (command, job["job_id"]))
    frozen_output_setting(store, job, lambda p: p["request"].update(command=command,
        query="Какие условия у банка?" if command == "search" else None))
    result["items"][0].update(conditions=["Контракт обязателен"], exceptions=["Кроме нерезидентов"], contradictions=["Другая версия: 60 дней"])
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    output = render(store, job)
    text = output["text"]
    assert command in text and "@two" in text and "@one" in text
    assert "2026-10-10" in text and "2026-10-12" in text and "UTC" in text
    assert "1250 EUR" in text and "Контракт обязателен" in text and "Кроме нерезидентов" in text and "60 дней" in text
    assert "https://t.me/peer22/1" in text and "https://t.me/peer11/1" in text
    assert output["result"]["partial"] is False
    if command == "search": assert "Какие условия у банка?" in text


def test_s23_full_markdown_partial_candidates_and_unavailable_link_are_explicit(manifest_job):
    store, job = manifest_job
    result = full_result(store, job)
    result.update(partial=True, reasons=["extraction_incomplete"], limitations=["Ответ отражает личный опыт"])
    result["coverage"].update(selection_mode="candidates", known_skipped=None)
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    store.conn.execute("UPDATE channels SET channel_type='Chat',username=NULL WHERE channel_id=22")
    text = render(store, job)["text"]
    assert "Частичный" in text and "extraction" in text and "личный опыт" in text
    assert "отобранных кандидатов" in text and "неизвестно" in text
    assert "ссылка недоступна" in text and "22/1" in text


def test_s23_full_markdown_model_markup_remains_literal_text(manifest_job):
    store, job = manifest_job
    result = full_result(store, job)
    result["items"][0].update(title="<b>Банк *А*</b>", text="## Правило [А] < 1250 EUR")
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    text = render(store, job)["text"]
    assert "&lt;b&gt;Банк \\*А\\*&lt;/b&gt;" in text
    assert "\\#\\# Правило \\[А\\] &lt; 1250 EUR" in text
    assert "<b>" not in text


def test_s23_full_markdown_caps_drop_whole_items_without_mutating_saved_result(manifest_job):
    store, job = manifest_job
    result = full_result(store, job)
    result["items"] = [{**copy.deepcopy(result["items"][0]), "title": f"Пункт {i}", "text": "Сведения " * 50,
        "conditions": [f"Условие {i} обязательно целиком"]} for i in range(4)]
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    frozen_output_setting(store, job, lambda p: p["settings"]["analysis"]["output"]["profiles"]["full"].update(max_chars=1400))
    output = render(store, job)
    assert len(output["text"]) <= 1400 and output["result"]["partial"] is True
    assert len(output["result"]["items"]) < 4 and "Частичный" in output["text"]
    for item in output["result"]["items"]:
        assert item["conditions"][0] in output["text"]
    assert store.get_job(job["job_id"])["result"] == result


@pytest.fixture
def artifact_job(manifest_job, tmp_path, monkeypatch):
    store, job = manifest_job
    full_result(store, job)
    source = tmp_path / "repo" / "telegram_connector"
    source.mkdir(parents=True)
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", str(source))
    return store, job


def write_report(store, job, **kwargs):
    function = getattr(analysis, "write_full_analysis_artifact", None)
    assert callable(function), "A full path is ready only after durable write/read-back/hash and metadata commit"
    return function(store, job["job_id"], generation=job["generation"], **kwargs)


def verify_report(job):
    function = getattr(analysis, "verify_full_analysis_artifact", None)
    assert callable(function), "Notification must verify the actual stored artifact"
    return function(job)


def test_s23_full_markdown_writer_verifies_bytes_before_metadata_and_reuses_ready_file(artifact_job, monkeypatch):
    from common import json_io
    store, job = artifact_job
    target, relative = resolve(store.get_job(job["job_id"]))
    expected = render(store, job)["text"]
    calls = []
    original = json_io.write_text_atomic
    def write(path, text):
        assert not store.conn.in_transaction and store.get_job(job["job_id"])["full_path"] is None
        calls.append(1)
        original(path, text)
    monkeypatch.setattr(json_io, "write_text_atomic", write)
    assert write_report(store, job) == relative
    saved = store.get_job(job["job_id"])
    assert saved["full_path"] == relative and saved["full_hash"] == hashlib.sha256(expected.encode()).hexdigest()
    assert target.read_text() == expected and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert saved["checkpoint"]["artifact"]["partial"] is False
    assert verify_report(saved) == relative
    assert write_report(store, job) == relative and calls == [1]


@pytest.mark.parametrize("phase", ["replace", "directory_sync"])
def test_s23_full_markdown_io_failure_has_no_success_metadata_or_notification(artifact_job, monkeypatch, phase):
    from common import json_io
    store, job = artifact_job
    target, _ = resolve(store.get_job(job["job_id"]))
    def fail(*args):
        raise OSError("artifact IO fault")
    monkeypatch.setattr(json_io.os if phase == "replace" else json_io, "replace" if phase == "replace" else "sync_directory", fail)
    with pytest.raises(OSError, match="artifact IO fault"):
        write_report(store, job)
    saved = store.get_job(job["job_id"])
    assert saved["full_path"] is None and saved["full_hash"] is None
    assert saved["delivery"]["outcome"] == "failed" and saved["delivery"]["error"] == "artifact_unavailable"
    assert target.exists() is (phase == "directory_sync")
    assert not list(target.parent.glob("*.tmp"))


def test_s23_full_markdown_missing_file_is_explicit_and_can_be_restored_without_ai(artifact_job, monkeypatch):
    store, job = artifact_job
    relative = write_report(store, job)
    target, _ = resolve(store.get_job(job["job_id"]))
    digest = store.get_job(job["job_id"])["full_hash"]
    target.unlink()
    with pytest.raises(FileNotFoundError):
        verify_report(store.get_job(job["job_id"]))
    with pytest.raises(FileNotFoundError):
        write_report(store, job)
    monkeypatch.setattr(analysis, "synthesize_analysis_result", lambda *a, **k: pytest.fail("Restore must use saved data"))
    assert write_report(store, job, restore_missing=True) == relative
    assert store.get_job(job["job_id"])["full_hash"] == digest and target.is_file()


def test_s23_full_markdown_changed_user_file_is_not_overwritten(artifact_job):
    store, job = artifact_job
    write_report(store, job)
    target, _ = resolve(store.get_job(job["job_id"]))
    target.write_text("Пользовательская редакция\n")
    with pytest.raises(ValueError, match="hash"):
        verify_report(store.get_job(job["job_id"]))
    with pytest.raises(ValueError, match="hash"):
        write_report(store, job, restore_missing=True)
    assert target.read_text() == "Пользовательская редакция\n"


def test_s23_full_markdown_artifact_metadata_fits_reserved_headroom_at_byte_cap(artifact_job):
    store, job = artifact_job
    cap = store.storage_usage(job["job_id"])["allocated_bytes"] + 16
    frozen_output_setting(store, job, lambda p: p["settings"]["analysis"].update(max_intermediate_bytes=cap))
    assert write_report(store, job) == verify_report(store.get_job(job["job_id"]))
    assert store.storage_usage(job["job_id"])["allocated_bytes"] <= cap


def notify(store, job, sender):
    return analysis.deliver_analysis_result(store, job["job_id"], generation=job["generation"], token="fixture-token",
        access_provider=lambda: {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()},
        send_func=sender, artifact_verifier=analysis.verify_full_analysis_artifact)


@pytest.mark.parametrize("limited", [False, True])
def test_s23_full_markdown_actual_file_delivery_is_path_only_and_preserves_partial(artifact_job, limited):
    store, job = artifact_job
    if limited:
        result = store.get_job(job["job_id"])["result"]
        result["items"] = [{**copy.deepcopy(result["items"][0]), "text": "Сведения " * 100} for _ in range(4)]
        store.save_analysis_result(job["job_id"], result, generation=job["generation"])
        frozen_output_setting(store, job, lambda p: p["settings"]["analysis"]["output"]["profiles"]["full"].update(max_chars=1400))
    relative = write_report(store, job)
    calls = []
    def send(token, chat, text, mode, **kwargs):
        assert verify_report(store.get_job(job["job_id"])) == relative
        assert text == relative and mode is None and chat == "100"
        calls.append(text)
        return {"message_id": 70, "chat": {"id": 100}}
    journal = notify(store, job, send)
    assert journal["outcome"] == "confirmed" and journal["analysis_partial"] is limited
    assert notify(store, job, send)["outcome"] == "confirmed" and calls == [relative]


def test_s23_full_markdown_recovered_read_error_does_not_leave_a_stale_failed_plan(artifact_job, monkeypatch):
    store, job = artifact_job
    relative = write_report(store, job)
    original = analysis._analysis_artifact_digest
    def fail(*args):
        raise OSError("temporary read error")
    monkeypatch.setattr(analysis, "_analysis_artifact_digest", fail)
    with pytest.raises(OSError, match="temporary read error"):
        write_report(store, job)
    monkeypatch.setattr(analysis, "_analysis_artifact_digest", original)
    assert write_report(store, job) == relative
    assert notify(store, job, lambda *a, **k: {"message_id": 71, "chat": {"id": 100}})["outcome"] == "confirmed"


def test_s23_full_markdown_metadata_commit_fault_adopts_existing_file_without_rewrite(artifact_job, monkeypatch):
    from common import json_io
    import sqlite3
    store, job = artifact_job
    target, relative = resolve(store.get_job(job["job_id"]))
    store.conn.execute("""CREATE TEMP TRIGGER fail_artifact BEFORE UPDATE OF full_path ON analysis_jobs
        BEGIN SELECT RAISE(ABORT,'artifact metadata fault'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="artifact metadata fault"):
        write_report(store, job)
    assert target.is_file() and store.get_job(job["job_id"])["full_path"] is None
    store.conn.execute("DROP TRIGGER fail_artifact")
    monkeypatch.setattr(json_io, "write_text_atomic", lambda *a: pytest.fail("Verified existing artifact must be adopted"))
    assert write_report(store, job) == relative
    assert verify_report(store.get_job(job["job_id"])) == relative


def test_s23_full_markdown_readback_mismatch_does_not_commit_success(artifact_job, monkeypatch):
    from common import json_io
    store, job = artifact_job
    original = json_io.write_text_atomic
    def corrupt(path, text):
        original(path, text + "Изменено после записи")
    monkeypatch.setattr(json_io, "write_text_atomic", corrupt)
    with pytest.raises(OSError, match="read-back"):
        write_report(store, job)
    saved = store.get_job(job["job_id"])
    assert saved["full_path"] is None and saved["delivery"]["outcome"] == "failed"


@pytest.mark.parametrize("mutation", ["source", "result", "cancel", "generation"])
def test_s23_full_markdown_mutation_during_write_cannot_publish_stale_metadata(artifact_job, monkeypatch, mutation):
    from common import json_io
    from datetime import datetime, timezone
    from telegram_connector.analysis_store import StaleGenerationError
    store, job = artifact_job
    original = json_io.write_text_atomic
    def mutate(path, text):
        original(path, text)
        if mutation == "source":
            store.conn.execute("UPDATE messages SET text='Новые условия' WHERE channel_id=22")
        elif mutation == "result":
            result = store.get_job(job["job_id"])["result"]
            result["summary"] = "Другой результат"
            store.save_analysis_result(job["job_id"], result, generation=job["generation"])
        elif mutation == "cancel":
            store.request_cancel(job["job_id"], now=datetime.now(timezone.utc))
        else:
            store.conn.execute("UPDATE analysis_jobs SET generation=generation+1 WHERE job_id=?", (job["job_id"],))
    monkeypatch.setattr(json_io, "write_text_atomic", mutate)
    expected = {"source": ValueError, "result": ValueError, "cancel": InterruptedError, "generation": StaleGenerationError}[mutation]
    with pytest.raises(expected):
        write_report(store, job)
    saved = store.get_job(job["job_id"])
    assert saved["full_path"] is None and saved["full_hash"] is None
    assert saved["delivery"].get("outcome") != "confirmed"


def test_s23_full_markdown_fifo_is_rejected_without_opening_it(artifact_job, monkeypatch):
    import os
    store, job = artifact_job
    target, _ = resolve(store.get_job(job["job_id"]))
    target.parent.mkdir(parents=True)
    os.mkfifo(target)
    original = Path.open
    def open_file(path, *args, **kwargs):
        if path == target:
            pytest.fail("Artifact verification must not open a FIFO")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(ValueError, match="regular file"):
        write_report(store, job)


def test_s23_full_markdown_hash_verification_streams_split_utf8_codepoints(artifact_job, monkeypatch):
    store, job = artifact_job
    result = store.get_job(job["job_id"])["result"]
    result["items"][0]["text"] = "Я😀" * 5000
    store.save_analysis_result(job["job_id"], result, generation=job["generation"])
    relative = write_report(store, job)
    target, _ = resolve(store.get_job(job["job_id"]))
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    original = Path.open
    sizes = []
    monkeypatch.setattr(analysis.io, "DEFAULT_BUFFER_SIZE", 7)  # Force UTF-8 codepoints across read boundaries.
    class Reader:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def read(self, size=-1):
            assert 0 < size <= 7
            sizes.append(size)
            return self.handle.read(size)
    def open_file(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return Reader(handle) if path == target else handle
    monkeypatch.setattr(Path, "open", open_file)
    assert verify_report(store.get_job(job["job_id"])) == relative
    assert store.get_job(job["job_id"])["full_hash"] == expected and len(sizes) > 2
