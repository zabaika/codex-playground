"""Analytics admission, worker control, presentation orchestration and delivery."""

import argparse
import asyncio
import codecs
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import html
import io
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time
from typing import Any, Callable

if __package__:
    from .analysis_store import AdmissionConflictError, AnalysisStore, BudgetExceededError, QueueFullError, StaleGenerationError, canonical_json
    from . import telegram_bridge as bridge
    from . import telegram_history_client as history_client
    from . import analysis_settings
    from . import analysis_evidence
    from . import analysis_rendering
    from . import analysis_pipeline
else:
    from analysis_store import AdmissionConflictError, AnalysisStore, BudgetExceededError, QueueFullError, StaleGenerationError, canonical_json
    import telegram_bridge as bridge
    import telegram_history_client as history_client
    import analysis_settings
    import analysis_evidence
    import analysis_rendering
    import analysis_pipeline

from common import process as common_process
from common import json_io as common_json_io
from telegram_shared.bot_api import DEFAULT_API_TIMEOUT_SECONDS
from telegram_shared.errors import TelegramApiError
from telegram_shared.paths import resolve_app_paths

# Explicit compatibility exports for existing bridge/evaluator callers.
if __package__:
    from .analysis_evidence import (
        ANALYSIS_NORMALIZATION_VERSION,
        analysis_text_view,
        build_analysis_message_link,
        validate_evidence_records,
        verify_analysis_sources,
    )
    from .analysis_pipeline import (
        build_all_messages_manifest,
        build_extraction_payload,
        build_search_manifest,
        build_synthesis_payload,
        compile_search_fts,
        extract_analysis_batch,
        extraction_cache_key,
        iter_analysis_batches,
        iter_analysis_manifest_messages,
        iter_merge_leaf_records,
        iter_relation_candidates,
        link_analysis_records,
        merge_analysis_records,
        normalize_search_query,
        prepare_analysis_payload,
        prepare_search_query,
        request_analysis_json,
        request_counted_analysis_payload,
        synthesize_analysis_result,
        try_reuse_extraction,
        try_reuse_query_expansion,
        validate_search_terms,
    )
    from .analysis_settings import (
        ANALYSIS_ALGORITHM_VERSION,
        ANALYSIS_SCHEMA_VERSION,
        PROMPT_FIELDS,
        load_analysis_prompts,
        render_analysis_prompt,
        resolve_analysis_model_capabilities,
        resolve_analysis_policy,
    )
else:
    from analysis_evidence import (
        ANALYSIS_NORMALIZATION_VERSION,
        analysis_text_view,
        build_analysis_message_link,
        validate_evidence_records,
        verify_analysis_sources,
    )
    from analysis_pipeline import (
        build_all_messages_manifest,
        build_extraction_payload,
        build_search_manifest,
        build_synthesis_payload,
        compile_search_fts,
        extract_analysis_batch,
        extraction_cache_key,
        iter_analysis_batches,
        iter_analysis_manifest_messages,
        iter_merge_leaf_records,
        iter_relation_candidates,
        link_analysis_records,
        merge_analysis_records,
        normalize_search_query,
        prepare_analysis_payload,
        prepare_search_query,
        request_analysis_json,
        request_counted_analysis_payload,
        synthesize_analysis_result,
        try_reuse_extraction,
        try_reuse_query_expansion,
        validate_search_terms,
    )
    from analysis_settings import (
        ANALYSIS_ALGORITHM_VERSION,
        ANALYSIS_SCHEMA_VERSION,
        PROMPT_FIELDS,
        load_analysis_prompts,
        render_analysis_prompt,
        resolve_analysis_model_capabilities,
        resolve_analysis_policy,
    )


APP_DIR = Path(__file__).resolve().parent


def _reduce_analysis_output(store, job, value, reason):
    value.update(partial=True, reasons=sorted(set(value.get("reasons", [])) | {reason}))
    if value.get("items"):
        value["items"].pop()
        value["summary"] = ""
        if not value["items"]:
            value = analysis_evidence.verify_analysis_sources(store, job["job_id"], value, generation=job["generation"])
    elif value.get("summary"):
        value["summary"] = ""
    elif value.get("limitations"):
        value["limitations"] = []
    elif value.get("sources"):
        value["sources"].pop()
        value["reasons"] = sorted(set(value["reasons"]) | {"presentation_sources_limited"})
    else:
        return None
    return value


def prepare_analysis_presentation(store, job_id, result, *, generation):
    job = store.require_running(job_id, generation=generation)
    limits = analysis_settings._synthesis_limits(job)
    value = analysis_evidence.verify_analysis_sources(store, job_id, result, generation=generation)
    value.update(command=job["command"], detail=limits["detail"], query=job["request"]["request"].get("query"))
    if value.get("kind") == "budget_stop":
        value.update(partial=True, reasons=sorted(set(value.get("reasons", [])) | {value["reason"]}))
        manifest = job["checkpoint"].get("manifest", {})
        value["coverage"] = {"selection_mode": value["selection_mode"], "selected_messages": manifest.get("selected", 0),
            "processed_messages": store.processed_analysis_messages(job_id), "known_skipped": manifest.get("known_skipped")}
    analysis_evidence._verify_presentation_urls(value)
    if limits["detail"] == "full":
        return {"detail": "full", "result": value, "chunks": [], "parse_mode": "HTML"}
    maximum = job["request"]["settings"]["analysis"]["output"]["max_messages"]
    while True:
        chunks = analysis_rendering._analysis_html_chunks(job, value)
        if len(chunks) <= maximum and sum(map(len, chunks)) <= limits["max_chars"]:
            return {"detail": limits["detail"], "result": value, "chunks": chunks, "parse_mode": "HTML"}
        reason = "max_chars" if sum(map(len, chunks)) > limits["max_chars"] else "max_telegram_messages"
        if reason == "max_chars" and value.get("query"):
            suffix = "… (запрос сокращён)"
            displayed = value.get("display_query", value["query"])
            original = displayed.removesuffix(suffix) if "display_query" in value else displayed
            if original:
                available = max(0, len(html.escape(displayed)) - (sum(map(len, chunks)) - limits["max_chars"])
                    - len(html.escape(suffix)))
                prefix, used = [], 0
                for char in original:
                    length = len(html.escape(char))
                    if used + length > available:
                        break
                    prefix.append(char)
                    used += length
                value.update(display_query="".join(prefix) + suffix, partial=True,
                    reasons=sorted(set(value.get("reasons", [])) | {reason}))
                continue
        value = _reduce_analysis_output(store, job, value, reason)
        if value is None:
            raise ValueError("presentation_budget_too_small")


def resolve_analysis_artifact(job):
    """Return an absolute write target and its repo-relative notification path."""
    paths = resolve_app_paths(__file__, project_root_env_var="TELEGRAM_CONNECTOR_PROJECT_ROOT", runtime_base="app_dir")
    connector = paths.project_root.resolve()
    if connector.name != "telegram_connector" or not connector.is_dir():
        raise ValueError("Canonical Connector project root is required")
    if not re.fullmatch(r"[a-f0-9]{32}", job["job_id"]):
        raise ValueError("Invalid artifact job identity")
    folder = Path(job["request"]["settings"]["analysis"]["output"]["results_dir"])
    if folder.is_absolute() or not folder.parts or folder.parts[0] != "scratch" or ".." in folder.parts:
        raise ValueError("Artifact must remain under repo scratch")
    relative = (folder / (job["job_id"] + ".md")).as_posix()
    target = connector.parent / relative
    if target.is_symlink() or not target.resolve().is_relative_to(connector.parent / "scratch"):
        raise ValueError("Artifact path escapes repo scratch")
    return target, relative


def render_full_analysis_markdown(store, job_id, *, generation):
    job = store.require_running(job_id, generation=generation)
    if job["request"]["request"]["detail"] != "full" or job["result"] is None:
        raise ValueError("Full result is not prepared")
    value = prepare_analysis_presentation(store, job_id, job["result"], generation=generation)["result"]
    maximum = analysis_settings._synthesis_limits(job)["max_chars"]
    while True:
        text = analysis_rendering._full_analysis_markdown(job, value)
        if len(text) <= maximum:
            return {"text": text, "result": value}
        value = _reduce_analysis_output(store, job, value, "max_chars")
        if value is None:
            raise ValueError("markdown_budget_too_small")


def _analysis_artifact_digest(path, maximum):
    if path.exists() and not path.is_file():
        raise ValueError("Artifact is not a regular file")
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    chars = 0
    with path.open("rb") as handle:
        while chunk := handle.read(io.DEFAULT_BUFFER_SIZE):
            chars += len(decoder.decode(chunk))
            if chars > maximum:
                raise ValueError("Artifact exceeds Markdown size limit")
            digest.update(chunk)
        chars += len(decoder.decode(b"", final=True))
    if chars > maximum:
        raise ValueError("Artifact exceeds Markdown size limit")
    return digest.hexdigest()


def verify_full_analysis_artifact(job):
    target, relative = resolve_analysis_artifact(job)
    if relative != job["full_path"] or not re.fullmatch(r"[a-f0-9]{64}", job["full_hash"] or ""):
        raise ValueError("Artifact metadata is not ready")
    maximum = job["request"]["settings"]["analysis"]["output"]["profiles"]["full"]["max_chars"]
    if _analysis_artifact_digest(target, maximum) != job["full_hash"]:
        raise ValueError("artifact_hash_mismatch")
    result = job["result"]
    expected = hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest() if result is not None else None
    if job["checkpoint"].get("artifact", {}).get("result_digest") != expected or expected is None:
        raise ValueError("Artifact result changed")
    return relative


def write_full_analysis_artifact(store, job_id, *, generation, restore_missing=False):
    job = store.require_running(job_id, generation=generation)
    if job["request"]["request"]["detail"] != "full" or job["result"] is None:
        raise ValueError("Full result is not prepared")
    try:
        analysis_evidence.verify_analysis_sources(store, job_id, job["result"], generation=generation)
        target, relative = resolve_analysis_artifact(job)
        if job["full_path"] is not None:
            try:
                ready = verify_full_analysis_artifact(job)
                if job["delivery"].get("outcome") == "failed" and "parts" not in job["delivery"]:
                    artifact = job["checkpoint"]["artifact"]
                    store.save_full_artifact(job_id, generation=generation, relative_path=ready, content_hash=job["full_hash"],
                        result_digest=artifact["result_digest"], partial=artifact["partial"])
                return ready
            except FileNotFoundError:
                if not restore_missing:
                    raise
        rendered = render_full_analysis_markdown(store, job_id, generation=generation)
        content_hash = hashlib.sha256(rendered["text"].encode("utf-8")).hexdigest()
        maximum = job["request"]["settings"]["analysis"]["output"]["profiles"]["full"]["max_chars"]
        replace_owned = False
        previous_hash = None
        if target.exists():
            current_hash = _analysis_artifact_digest(target, maximum)
            if current_hash != content_hash:
                artifact = job["checkpoint"].get("artifact", {})
                if current_hash not in {artifact.get("content_hash"), artifact.get("previous_hash")}:
                    raise ValueError("artifact_hash_conflict")
                replace_owned = True
                previous_hash = current_hash
        store.save_full_artifact(job_id, generation=generation, relative_path=relative, content_hash=content_hash,
            result_digest=hashlib.sha256(canonical_json(job["result"]).encode("utf-8")).hexdigest(),
            partial=rendered["result"]["partial"], ready=False, previous_hash=previous_hash)
        if target.exists() and not replace_owned:
            # A previous process may have stopped after replace, before metadata.
            common_json_io.sync_directory(target.parent)
        else:
            common_json_io.write_text_atomic(target, rendered["text"])
        if _analysis_artifact_digest(target, maximum) != content_hash:
            raise OSError("Artifact read-back hash mismatch")
        analysis_evidence.verify_analysis_sources(store, job_id, job["result"], generation=generation)
        store.save_full_artifact(job_id, generation=generation, relative_path=relative, content_hash=content_hash,
            result_digest=hashlib.sha256(canonical_json(job["result"]).encode("utf-8")).hexdigest(),
            partial=rendered["result"]["partial"])
        return verify_full_analysis_artifact(store.get_job(job_id))
    except Exception:
        current = store.get_job(job_id)
        if (current is not None and current["generation"] == generation and current["status"] == "running"
                and not current["cancel_requested"]):
            store.stop_delivery(job_id, generation=generation, reason="artifact_unavailable")
        raise


def deliver_analysis_result(store, job_id, *, generation, token, access_provider, send_func=None,
                            sleep_func=time.sleep, monotonic_func=time.monotonic, deadline=None, artifact_verifier=None):
    """Send saved data with one durable intent per physical request, without inference."""
    job = store.require_running(job_id, generation=generation)
    policy = job["request"]["settings"]["bridge"]
    if deadline is None:
        deadline = monotonic_func() + policy["worker_process_timeout_seconds"]
    sender = bridge.send_analysis_text_once if send_func is None else send_func
    if not job["delivery"]:
        if job["result"] is None:
            raise ValueError("Analysis result is not prepared")
        prepared = prepare_analysis_presentation(store, job_id, job["result"], generation=generation)
        if prepared["detail"] == "full":
            if artifact_verifier is None or artifact_verifier(job) != job["full_path"]:
                raise ValueError("Full artifact is not verified")
            prepared.update(chunks=[job["full_path"]], parse_mode=None)
            prepared["result"]["partial"] |= job["checkpoint"].get("artifact", {}).get("partial", False)
        try:
            store.prepare_delivery(job_id, generation=generation, chunks=prepared["chunks"], parse_mode=prepared["parse_mode"],
                detail=prepared["detail"], analysis_partial=prepared["result"].get("partial", False))
        except BudgetExceededError as exc:
            if exc.reason != "max_intermediate_bytes" or prepared["detail"] == "full":
                raise
            store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
            fallback = store.budget_fallback(job_id, generation=generation, reason=exc.reason)
            prepared = prepare_analysis_presentation(store, job_id, fallback, generation=generation)
            store.prepare_delivery(job_id, generation=generation, chunks=prepared["chunks"], parse_mode=prepared["parse_mode"],
                detail=prepared["detail"], analysis_partial=True)
    while True:
        job = store.require_running(job_id, generation=generation)
        delivery = job["delivery"]
        if delivery["outcome"] in {"confirmed", "unknown"}:
            return delivery
        index = next(i for i, part in enumerate(delivery["parts"]) if part["outcome"] != "confirmed")
        part = delivery["parts"][index]
        if len(part["attempts"]) >= policy["send_message_retry_attempts"]:
            return delivery
        if part["attempts"]:
            previous = part["attempts"][-1]
            if previous["error_code"] != 429 and previous["generation"] == generation:
                return delivery
            delay = max(previous["retry_after"] or 0, policy["send_message_retry_backoff_seconds"] * len(part["attempts"]))
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["finished_at"])).total_seconds()
            delay = max(0, delay - elapsed)
            if delay >= deadline - monotonic_func():
                return store.stop_delivery(job_id, generation=generation, reason="deadline_exceeded")
            if delay:
                sleep_func(delay)
        job = store.require_running(job_id, generation=generation)
        event = {"message": {"chat": {"id": job["destination"]}, "from": {
            "id": job["requester_id"], "username": job["request"]["principal"]["username"]}}}
        try:
            require_analysis_access(event, access_provider())
        except AnalysisRejection:
            return store.stop_delivery(job_id, generation=generation, reason="forbidden")
        if delivery["detail"] == "full":
            try:
                if (artifact_verifier is None or delivery["artifact_hash"] != job["full_hash"]
                        or artifact_verifier(job) != part["text"]):
                    raise ValueError("Full artifact changed")
            except (OSError, ValueError):
                return store.stop_delivery(job_id, generation=generation, reason="artifact_unavailable")
        analysis_evidence.verify_analysis_sources(store, job_id, job["result"], generation=generation)
        remaining = deadline - monotonic_func()
        if remaining <= 0:
            return store.stop_delivery(job_id, generation=generation, reason="deadline_exceeded")
        attempt = store.begin_delivery_attempt(job_id, generation=generation, part_index=index)
        try:
            response = sender(token, delivery["destination"], part["text"], delivery["parse_mode"],
                              timeout_seconds=min(DEFAULT_API_TIMEOUT_SECONDS, remaining))
        except TelegramApiError as exc:
            outcome = "refused" if exc.request_outcome == "refused" else "unknown"
            store.settle_delivery_attempt(job_id, generation=generation, part_index=index, attempt_id=attempt["attempt_id"],
                outcome=outcome, error_code=exc.error_code, retry_after=exc.retry_after)
        except Exception:
            store.settle_delivery_attempt(job_id, generation=generation, part_index=index, attempt_id=attempt["attempt_id"], outcome="unknown")
        else:
            chat = response.get("chat") if isinstance(response, dict) else None
            identity = response.get("message_id") if isinstance(response, dict) else None
            valid = type(identity) is int and 0 < identity <= (1 << 63) - 1 and isinstance(chat, dict) and str(chat.get("id")) == job["destination"]
            store.settle_delivery_attempt(job_id, generation=generation, part_index=index, attempt_id=attempt["attempt_id"],
                outcome="confirmed" if valid else "unknown", message_id=identity if valid else None,
                chat_id=chat["id"] if valid else None)


def _reused_settings(config: dict[str, Any]) -> dict[str, Any]:
    get = history_client.get_config_value
    try:
        settings = {
            "processing": {"model": get(config, "processing", "model")},
            "digest": {key: get(config, "digest", key) or "yesterday" for key in ("since", "until")},
            "sync": {
                key: history_client.require_int_config_value(config, "sync", key, min_value=0)
                for key in ("sync_limit", "batch_size")
            },
            "digest_ai": {
                key: history_client.require_int_config_value(config, "digest_ai", key, min_value=1)
                for key in ("messages_per_ai_pass", "message_block_max_chars")
            },
        }
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    if not settings["processing"]["model"]:
        raise ValueError("Missing processing.model")
    ai = settings["digest_ai"]
    # Keep the existing digest scalar fallbacks/clamps; no digest prompts, keys,
    # per-profile sync limits or text truncation are needed by analytics.
    for key, default in (("max_output_tokens", 1200), ("openai_timeout_seconds", 120), ("openai_retry_attempts", 3)):
        try:
            ai[key] = max(1, int(get(config, "digest_ai", key) or default))
        except ValueError as exc:
            raise ValueError(f"Invalid digest_ai.{key}") from exc
    ai["openai_retry_attempts"] = min(5, ai["openai_retry_attempts"])
    try:
        backoff = float(get(config, "digest_ai", "openai_retry_backoff_seconds") or 1)
        ai["openai_retry_backoff_seconds"] = min(60.0, max(0.0, backoff))
    except ValueError as exc:
        raise ValueError("Invalid digest_ai.openai_retry_backoff_seconds") from exc
    for key, allowed in (
        ("reasoning_effort", {"none", "low", "medium", "high", "xhigh", "max"}),
        ("reasoning_summary", {"auto", "concise", "detailed", "none"}),
    ):
        value = get(config, "digest_ai", key).lower()
        if value not in allowed:
            raise ValueError(f"Missing or invalid digest_ai.{key}")
        ai[key] = value
    # Match legacy permissive boolean parsing, without resolving any secrets.
    for section, key, default in (("processing", "ocr", True), ("digest_ai", "store_prompt_text", False)):
        raw = get(config, section, key).lower()
        value = default
        if raw in {"1", "true", "yes", "on"}:
            value = True
        elif raw in {"0", "false", "no", "off"}:
            value = False
        settings[section][key] = value
    settings["telethon"] = {
        key: history_client.parse_int_config_value(config, "telethon", key, default=default, min_value=minimum)
        for key, default, minimum in (
            ("connection_timeout_seconds", history_client.DEFAULT_CONNECTION_TIMEOUT_SECONDS, 1),
            ("connection_attempts", history_client.DEFAULT_CONNECTION_ATTEMPTS, 1),
            ("connection_retry_backoff_seconds", history_client.DEFAULT_CONNECTION_RETRY_BACKOFF_SECONDS, 0),
            ("connection_cleanup_timeout_seconds", history_client.DEFAULT_CONNECTION_CLEANUP_TIMEOUT_SECONDS, 1),
        )
    }
    settings["bridge"] = {
        "worker_process_timeout_seconds": bridge.resolve_worker_process_timeout_seconds(config),
        "text_chunk_size": bridge.resolve_text_chunk_size(config),
        "send_message_retry_attempts": bridge.resolve_send_message_retry_attempts(config),
        "send_message_retry_backoff_seconds": bridge.resolve_send_message_retry_backoff_seconds(config),
    }
    settings["ocr"] = {"tesseract_timeout_seconds": history_client.parse_int_config_value(
        config, "ocr", "tesseract_timeout_seconds", default=history_client.DEFAULT_TESSERACT_TIMEOUT_SECONDS, min_value=1,
    )}
    settings["auth"] = {
        key: get(config, "auth", key) or default for key, default in (
            ("default_mode", "auto"), ("public_channel_mode", "bot"), ("private_channel_mode", "user"),
        )
    }
    for key, value in settings["auth"].items():
        try:
            history_client.normalize_auth_mode(value)
        except SystemExit:
            raise ValueError(f"Invalid auth.{key}") from None
        if key != "default_mode" and value == "auto":
            raise ValueError(f"Invalid auth.{key}: a concrete mode is required")
    settings["process"] = asdict(common_process.load_process_config())
    settings["sqlite"] = asdict(history_client.SQLITE_CONFIG)
    return settings


def resolve_analysis_settings(config: dict[str, Any], *, config_dir: Path) -> dict[str, Any]:
    """Validate policy before reading legacy settings, without resolving secrets."""
    settings = analysis_settings.resolve_analysis_policy(config)
    reused = _reused_settings(config)
    return analysis_settings.finalize_analysis_settings(settings, config, reused, config_dir=config_dir)


class AnalysisRejection(ValueError):
    MESSAGES = {
        "forbidden": "Нет доступа к аналитическим командам.",
        "invalid_request": "Некорректны параметры аналитики: проверьте каналы, период и настройки.",
        "conflict": "Идентификатор команды уже принят с другими параметрами.",
        "queue_full": "Очередь аналитики заполнена. Повторите команду позже.",
        "not_found": "Задача не найдена или недоступна.",
        "invalid_control": "Используйте /status, /cancel или /resume с идентификатором задачи.",
        "still_running": "Задача занята. Дождитесь освобождения worker или завершения другой команды.",
        "not_resumable": "Это состояние задачи не допускает продолжение.",
        "expired": "Срок хранения checkpoint истёк. Создайте новую задачу.",
        "budget_exhausted": "Бюджет задачи исчерпан. Продолжение не сбрасывает лимиты.",
        "unknown_delivery": "Исход доставки неизвестен. Автоматическая повторная отправка отключена; требуется решение оператора.",
        "already_complete": "Результат уже доставлен.",
        "policy_changed": "Настройки или версия анализа изменились. Создайте новую задачу.",
        "state_changed": "Состояние задачи изменилось. Проверьте /status.",
    }

    def __init__(self, reason: str):
        super().__init__(reason)
        self.public_message = self.MESSAGES[reason]


def require_analysis_access(update: dict[str, Any], access: dict[str, frozenset[str]]) -> tuple[str, str]:
    chat_id = bridge.extract_chat_id(update)
    user_id = bridge.extract_user_id(update)
    if (chat_id is None or user_id is None or str(chat_id) not in access["chats"]
            or not bridge.is_user_allowed(update, allowed_user_ids=access["users"], allowed_usernames=access["usernames"])):
        raise AnalysisRejection("forbidden")
    return str(user_id), str(chat_id)


def admit_analysis_job(store: AnalysisStore, config: dict[str, Any], update: dict[str, Any], *,
                       access: dict[str, frozenset[str]], bot_namespace: str,
                       now: datetime, config_dir: Path) -> tuple[dict[str, Any], bool]:
    requester_id, destination = require_analysis_access(update, access)
    update_id = update.get("update_id")
    if type(update_id) is not int or update_id < 0 or not bot_namespace:
        raise AnalysisRejection("invalid_request")
    raw = bridge.extract_text(update)
    # The transport input, not a newly resolved clock or policy, identifies replay.
    identity_payload = {"text": raw, "requester_id": requester_id, "destination": destination}
    identity_json = canonical_json(identity_payload)
    request_hash = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    admission_key = f"{bot_namespace}:{update_id}"
    existing = store.get_by_admission(admission_key)
    if existing:
        if existing["request_hash"] != request_hash:
            raise AnalysisRejection("conflict")
        return existing, False
    try:
        settings = resolve_analysis_settings(config, config_dir=config_dir)
        request = bridge.parse_analysis_request(raw, policy=settings, now=now)
        payload = {
            "request": request.to_payload(), "settings": settings,
            "principal": {"namespace": bot_namespace, "username": bridge.extract_username(update) or None},
        }
        return store.admit_job(
            payload, command=request.command, requester_id=requester_id, destination=destination,
            admission_key=admission_key, request_hash=request_hash,
            accepted_at=request.accepted_at.isoformat(), cutoff_at=request.cutoff_at.isoformat(),
            max_queued_jobs=settings["analysis"]["max_queued_jobs"],
            max_bytes=settings["analysis"]["max_intermediate_bytes"],
        )
    except AdmissionConflictError as exc:
        raise AnalysisRejection("conflict") from exc
    except QueueFullError as exc:
        raise AnalysisRejection("queue_full") from exc
    except ValueError as exc:
        raise AnalysisRejection("invalid_request") from exc


SEARCH_INDEX_ERRORS = {
    "fts_not_ready": "Индекс поиска ещё не готов. Можно выбрать --mode full.",
    "fts_rebuild_required": "Индекс поиска требует пересборки. Можно выбрать --mode full.",
    "fts_unavailable": "Полнотекстовый поиск недоступен в этой установке. Используйте --mode full.",
}


def analysis_status_view(job: dict[str, Any]) -> dict[str, Any]:
    fields = ("job_id", "status", "generation", "cancel_requested", "accepted_at", "cutoff_at", "updated_at",
              "llm_calls", "provider_requests", "used_tokens", "reserved_tokens", "downloaded_messages",
              "intermediate_bytes", "evidence_records")
    view = {key: job[key] for key in fields}
    phase = job["phase"]
    view["phase"] = phase if phase in {"sync", "select", "extract", "link", "merge", "synthesize", "verify", "deliver"} else "unknown"
    outcome = job["delivery"].get("outcome", "pending")
    view["delivery"] = outcome if isinstance(outcome, str) and outcome in {"pending", "prepared", "confirmed", "partial", "unknown", "failed"} else "unknown"
    view["has_result"] = job["result"] is not None
    reason = job["checkpoint"].get("last_stop", {}).get("reason")
    error = SEARCH_INDEX_ERRORS.get(reason) if isinstance(reason, str) else None
    if error:
        view["error"] = error
    return view


def control_analysis_job(store: AnalysisStore, config: dict[str, Any], update: dict[str, Any], *,
                         command: str, job_id: str, access: dict[str, frozenset[str]],
                         now: datetime, config_dir: Path) -> dict[str, Any]:
    requester, destination = require_analysis_access(update, access)
    if command not in {"status", "cancel", "resume"} or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise AnalysisRejection("invalid_control")
    job = store.get_job(job_id)
    if (job is None or job["kind"] != "analysis" or job["requester_id"] != requester
            or job["destination"] != destination):
        raise AnalysisRejection("not_found")
    if command == "status":
        return analysis_status_view(job)
    try:
        if command == "cancel":
            return analysis_status_view(store.request_cancel(job_id, now=now))
        if job["status"] == "queued":
            return analysis_status_view(job)
        if job["status"] == "running":
            raise AnalysisRejection("still_running")
        view = analysis_status_view(job)
        if view["delivery"] == "unknown":
            raise AnalysisRejection("unknown_delivery")
        restore_artifact = False
        if job["result"] is not None and job["full_path"] is not None and job["request"]["request"]["detail"] == "full":
            try:
                verify_full_analysis_artifact(job)
            except FileNotFoundError:
                restore_artifact = True
            except (OSError, ValueError) as exc:
                raise AnalysisRejection("not_resumable") from exc
        if job["result"] is not None and view["delivery"] == "confirmed":
            if not restore_artifact:
                raise AnalysisRejection("already_complete")
        if (job["status"] not in {"interrupted", "timed_out", "partial", "completed", "failed"}
                or (job["status"] == "failed" and not job["checkpoint"])
                or (job["status"] == "completed" and job["result"] is None)
                or (job["status"] == "partial" and job["result"] is None and job["checkpoint"].get("resumable") is not True)):
            raise AnalysisRejection("not_resumable")
        frozen = job["request"]["settings"]
        last_change = datetime.fromisoformat(job["updated_at"])
        if now.astimezone(timezone.utc) - last_change > timedelta(days=frozen["analysis"]["retention_days"]):
            raise AnalysisRejection("expired")
        try:
            current = resolve_analysis_settings(config, config_dir=config_dir)
        except ValueError as exc:
            raise AnalysisRejection("policy_changed") from exc
        # A changed prompt file does not silently rewrite a partially analysed job:
        # resume keeps its complete frozen bundle. Changed execution policy needs a new request.
        informational = {"settings_sources", "analysis_prompts"}
        if ({key: value for key, value in current.items() if key not in informational}
                != {key: value for key, value in frozen.items() if key not in informational}):
            raise AnalysisRejection("policy_changed")
        policy = frozen["analysis"]
        if job["result"] is None and (job["llm_calls"] >= policy["max_llm_calls"]
                or job["provider_requests"] >= policy["max_provider_requests"]
                or job["used_tokens"] + job["reserved_tokens"] >= policy["max_total_tokens"]):
            raise AnalysisRejection("budget_exhausted")
        db_file = store.conn.execute("PRAGMA database_list").fetchone()[2]
        # In-memory databases are connection-local and cannot have an external worker.
        ownership = bridge.analysis_ownership_lock(Path(db_file), owner=job_id) if db_file else nullcontext(True)
        with ownership as owned:
            if owned is None:
                raise AnalysisRejection("still_running")
            resumed = store.resume_job(job_id, generation=job["generation"],
                                       phase="verify" if job["result"] is not None else job["phase"],
                                       max_queued_jobs=current["analysis"]["max_queued_jobs"], now=now,
                                       restore_artifact=restore_artifact)
        return analysis_status_view(resumed)
    except QueueFullError as exc:
        raise AnalysisRejection("queue_full") from exc
    except StaleGenerationError as exc:
        raise AnalysisRejection("state_changed") from exc


def prepare_analysis_ocr(store, job_id, *, generation, runtime, deadline, monotonic_func):
    """Apply the existing OCR writer before freezing inputs, within one job-wide cap."""
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    if job["checkpoint"].get("manifest"):
        return  # Existing manifest digests must not be changed by a resume.
    history = job["checkpoint"]["history"]
    peers = list(dict.fromkeys(history.get("refs", {}).get(ref) for ref in request["channels"]))
    peers = [peer for peer in peers if peer is not None and history.get("sources", {}).get(str(peer), {}).get("state") != "failed"]
    limit = min(runtime.ocr_pending_default_limit, settings["analysis"]["max_messages"])
    state = job["checkpoint"].setdefault("ocr", {"processed": 0, "partial": False})
    window = {"since": request["since"], "until": min(request["until"], request["cutoff_at"])}
    partial = False
    for peer in peers:
        while state["processed"] < limit:
            store.require_running(job_id, generation=generation)
            remaining = deadline - monotonic_func()
            if remaining <= 0:
                raise TimeoutError("Analytic run deadline exceeded")
            count = history_client.process_pending_ocr(store.conn, runtime.tesseract_binary, limit=1,
                channel_id=peer, **window, tesseract_timeout_seconds=min(settings["ocr"]["tesseract_timeout_seconds"], remaining))
            if not count:
                break
            state["processed"] += count
            store.save_checkpoint(job_id, job["checkpoint"], generation=generation,
                max_bytes=settings["analysis"]["max_intermediate_bytes"])
        pending = history_client.iter_pending_ocr(store.conn, 1, channel_id=peer, **window)
        failed = store.conn.execute("""SELECT 1 FROM media_assets ma JOIN messages m
            ON m.channel_id=ma.channel_id AND m.message_id=ma.message_id
            WHERE ma.channel_id=? AND ma.ocr_status='error' AND ma.local_path IS NOT NULL
              AND (ma.media_kind='photo' OR ma.media_kind LIKE 'image/%')
              AND m.date_utc>=? AND m.date_utc<=? LIMIT 1""", (peer, window["since"], window["until"])).fetchone()
        partial |= bool(pending or failed)
    state["partial"] = partial
    store.save_checkpoint(job_id, job["checkpoint"], generation=generation,
        max_bytes=settings["analysis"]["max_intermediate_bytes"])


def execute_analysis_job(store, job, *, runtime=None, client=None, api_key=None, token=None,
                         capabilities=None, access_provider=None, urlopen_func=None, send_func=None,
                         deadline=None, monotonic_func=common_process.continuous_time, sleep_func=time.sleep):
    """Compose the bounded stages over one frozen job and one run deadline."""
    job_id, generation = job["job_id"], job["generation"]
    job = store.require_running(job_id, generation=generation)
    settings = job["request"]["settings"]
    if deadline is None:
        deadline = monotonic_func() + settings["bridge"]["worker_process_timeout_seconds"]
    if access_provider is None:
        access_provider = lambda: bridge.resolve_bridge_access(bridge.load_runtime_config())
    event = {"message": {"chat": {"id": job["destination"]}, "from": {
        "id": job["requester_id"], "username": job["request"]["principal"].get("username")}}}
    require_analysis_access(event, access_provider())
    store.maintain_analysis_storage(now=datetime.now(timezone.utc), max_cache_bytes=settings["analysis"]["cache"]["max_bytes"])

    def phase(value):
        if monotonic_func() >= deadline:
            raise TimeoutError("Analytic run deadline exceeded")
        store.set_analysis_phase(job_id, generation=generation, phase=value)

    if job["result"] is None or job["checkpoint"].get("manifest", {}).get("input_changed"):
        if job["command"] == "search" and job["request"]["request"]["mode"] == "fast" and not job["checkpoint"].get("manifest"):
            history_client.require_fts_ready(store.conn)
        capabilities = capabilities or analysis_settings.resolve_analysis_model_capabilities(settings["processing"]["model"])
        if api_key is None:
            config = bridge.load_runtime_config()
            api_key = bridge.resolve_secret_value(os.environ.get("OPENAI_API_KEY", "").strip()
                or bridge.get_config_value(config, "secrets", "openai_api_key"), "OpenAI API key")
            if not api_key:
                raise ValueError("Missing OpenAI API key")
        transport = {"api_key": api_key, "capabilities": capabilities, "deadline": deadline,
                     "monotonic_func": monotonic_func, "sleep_func": sleep_func}
        if urlopen_func is not None:
            transport["urlopen_func"] = urlopen_func
        phase("sync")
        if runtime is None:
            runtime = history_client.resolve_runtime()
        asyncio.run(history_client.ensure_analysis_history(store, job_id, runtime, generation=generation,
                                                           client=client, deadline=deadline))
        if settings["processing"]["ocr"]:
            prepare_analysis_ocr(store, job_id, generation=generation, runtime=runtime,
                                 deadline=deadline, monotonic_func=monotonic_func)
        phase("select")
        if job["command"] == "search" and job["request"]["request"]["mode"] == "fast":
            analysis_pipeline.build_search_manifest(store, job_id, generation=generation, **transport)
        else:
            analysis_pipeline.build_all_messages_manifest(store, job_id, generation=generation)
        phase("extract")
        for batch in analysis_pipeline.iter_analysis_batches(store, job_id, generation=generation,
                build_payload=lambda messages, context: analysis_pipeline.build_extraction_payload(
                    store.require_running(job_id, generation=generation), messages, context), **transport):
            analysis_pipeline.extract_analysis_batch(store, job_id, batch, generation=generation, **transport)
        phase("link")
        analysis_pipeline.link_analysis_records(store, job_id, generation=generation, **transport)
        phase("merge")
        analysis_pipeline.merge_analysis_records(store, job_id, generation=generation, **transport)
        phase("synthesize")
        analysis_pipeline.synthesize_analysis_result(store, job_id, generation=generation, **transport)
    phase("verify")
    job = store.require_running(job_id, generation=generation)
    analysis_evidence.verify_analysis_sources(store, job_id, job["result"], generation=generation)
    if job["request"]["request"]["detail"] == "full":
        write_full_analysis_artifact(store, job_id, generation=generation,
            restore_missing=job["checkpoint"].get("artifact", {}).get("restore_missing") is True)
    job = store.require_running(job_id, generation=generation)
    if token is None and job["delivery"].get("outcome") not in {"confirmed", "unknown"}:
        token = bridge.require_token()
    phase("deliver")
    delivery = deliver_analysis_result(store, job_id, generation=generation, token=token,
        access_provider=access_provider, send_func=send_func, deadline=deadline,
        monotonic_func=monotonic_func, sleep_func=sleep_func, artifact_verifier=verify_full_analysis_artifact)
    job = store.require_running(job_id, generation=generation)
    return "partial" if job["result"].get("partial") or delivery.get("analysis_partial") else "completed"


def _worker_failure_diagnostic(exc, phase):
    """Persist only application-owned locations and fixed exception categories."""
    category = next((kind.__name__ for kind in (sqlite3.Error, ValueError, OSError, RuntimeError)
                     if isinstance(exc, kind)), "Exception")
    diagnostic = {"phase": phase, "exception": category}
    frame = exc.__traceback__
    while frame is not None:
        code = frame.tb_frame.f_code
        path = Path(code.co_filename).resolve()
        if path.parent == APP_DIR:
            diagnostic["location"] = {"module": path.stem, "function": code.co_name, "line": frame.tb_lineno}
        frame = frame.tb_next
    return diagnostic


def run_analysis(store: AnalysisStore, db_path: Path, *, job_id: str, generation: int,
                 executor: Callable[[AnalysisStore, dict[str, Any]], str] | None = None) -> int:
    """Own a claimed generation and dispatch its bounded production workflow."""
    with bridge.analysis_ownership_lock(db_path, owner=job_id) as owned:
        if owned is None:
            return os.EX_TEMPFAIL
        job = store.get_job(job_id)
        if job is None or job["generation"] != generation or job["status"] != "running":
            return os.EX_DATAERR
        if job["cancel_requested"]:
            store.finish_job(job_id, generation=generation, status="cancelled", now=datetime.now(timezone.utc))
            return 0
        try:
            if executor is None:
                if job["kind"] == "fts_backfill":
                    executor = run_fts_backfill
                else:
                    executor = execute_analysis_job
            status = executor(store, job)
            current = store.get_job(job_id)
            if current is None or current["generation"] != generation:
                raise StaleGenerationError("Worker generation changed")
            if current["status"] == "running":
                store.finish_job(job_id, generation=generation, status=status, now=datetime.now(timezone.utc))
            elif current["status"] != status:
                raise StaleGenerationError("Worker terminal state changed")
            return 0
        except StaleGenerationError:
            return os.EX_DATAERR
        except (TimeoutError, InterruptedError) as exc:
            current = store.get_job(job_id)
            if current is None or current["generation"] != generation:
                return os.EX_DATAERR
            cancelled = current["cancel_requested"]
            if current["status"] == "running":
                status = "cancelled" if cancelled else "timed_out" if isinstance(exc, TimeoutError) else "interrupted"
                store.finish_job(job_id, generation=generation, status=status, now=datetime.now(timezone.utc))
            return 0 if cancelled else job["request"]["settings"]["process"]["timeout_exit_code"] if isinstance(exc, TimeoutError) else os.EX_DATAERR
        except Exception as exc:
            try:
                current = store.get_job(job_id)
                if current is None or current["generation"] != generation or current["status"] != "running":
                    raise StaleGenerationError("Worker terminal state changed")
                if isinstance(exc, ValueError) and str(exc) in SEARCH_INDEX_ERRORS:
                    checkpoint = {**current["checkpoint"], "last_stop": {"reason": str(exc), "phase": current["phase"]}}
                    store.save_checkpoint(job_id, checkpoint, generation=generation,
                        max_bytes=current["request"]["settings"]["analysis"]["max_intermediate_bytes"])
                else:
                    checkpoint = {**current["checkpoint"], "last_failure": _worker_failure_diagnostic(exc, current["phase"])}
                    try:
                        store.save_checkpoint(job_id, checkpoint, generation=generation,
                                              max_bytes=current["request"]["settings"]["analysis"]["max_intermediate_bytes"])
                    except BudgetExceededError:
                        pass  # A full checkpoint must not prevent the terminal state commit.
                status = "failed"
                if current["result"] is not None:
                    status = "partial" if current["result"].get("partial") or current["delivery"].get("analysis_partial") else "completed"
                if current["cancel_requested"]:
                    status = "cancelled"
                store.finish_job(job_id, generation=generation, status=status, now=datetime.now(timezone.utc))
            except StaleGenerationError:
                return os.EX_DATAERR
            print("Analytic worker failed; durable checkpoint retained.", file=sys.stderr)
            return 1
        finally:
            if job["kind"] == "analysis":
                store.maintain_analysis_storage(now=datetime.now(timezone.utc),
                    max_cache_bytes=job["request"]["settings"]["analysis"]["cache"]["max_bytes"])


class WorkerTermination(BaseException):
    """SIGTERM received by the outer worker while it owns a child group."""


def _worker_sigterm(signum, frame):
    raise WorkerTermination()


def run_analysis_with_ttl(store: AnalysisStore, db_path: Path, *, job_id: str, generation: int,
                          worker_command: list[str] | None = None) -> int:
    from common import ttl_runner
    job = store.get_job(job_id)
    if job is None or job["generation"] != generation or job["status"] != "running":
        return os.EX_DATAERR
    settings = job["request"]["settings"]
    process = settings["process"]
    command = worker_command if worker_command is not None else [
        sys.executable, str(APP_DIR / "telegram_analysis.py"), "--worker-inner",
        "--db", str(db_path.resolve()), "--job-id", job_id, "--generation", str(generation),
    ]

    def stop_requested():
        return store.worker_stop_requested(job_id, generation=generation)

    interrupted_reason = None
    try:
        code = ttl_runner.run_with_ttl(
            command, timeout_seconds=settings["bridge"]["worker_process_timeout_seconds"],
            grace_seconds=process["default_termination_grace_seconds"],
            poll_interval_seconds=process["poll_interval_seconds"], timeout_exit_code=process["timeout_exit_code"],
            term_signal=process["term_signal"], kill_signal=process["kill_signal"],
            audit_file=None, timeout_reason="process_ttl_expired", use_caffeinate=False, sleep_policy="interrupt",
            stop_requested=stop_requested,
            env=bridge.build_history_client_subprocess_env({
                key: os.environ.get(key, "") for key in bridge.HISTORY_CLIENT_SECRET_ENV_MAP
            }),
        )
    except WorkerTermination:
        code, interrupted_reason = 128 + signal.SIGTERM, "worker_terminated"
    except KeyboardInterrupt:
        code, interrupted_reason = 128 + signal.SIGINT, "worker_interrupted"
    except Exception:
        code, interrupted_reason = 1, "worker_supervision_failed"
    # run_with_ttl has reaped its child and cleaned its owned group before this
    # boundary. A terminal commit still requires the generation and free job lock.
    with bridge.analysis_ownership_lock(db_path, owner=job_id) as owned:
        if owned is None:
            return os.EX_TEMPFAIL
        current = store.get_job(job_id)
        if current is None or current["generation"] != generation or current["status"] != "running":
            return code
        if current["cancel_requested"]:
            status, reason = "cancelled", "cancel_requested"
        elif interrupted_reason:
            status = "failed" if interrupted_reason == "worker_supervision_failed" else "interrupted"
            reason = interrupted_reason
        elif code == process["timeout_exit_code"]:
            status, reason = "timed_out", "process_ttl_expired"
        elif code == process["sleep_interruption_exit_code"]:
            status, reason = "interrupted", "host_sleep_interrupted"
        else:
            status, reason = ("interrupted" if code == 0 else "failed"), "worker_exited_without_terminal_state"
        checkpoint = {**current["checkpoint"], "last_stop": {
            "reason": reason, "phase": current["phase"], "exit_code": code,
        }}
        try:
            store.save_checkpoint(job_id, checkpoint, generation=generation,
                                  max_bytes=settings["analysis"]["max_intermediate_bytes"])
        except ValueError:
            # Retain the previous checkpoint if optional stop metadata cannot fit.
            print("Analytic stop metadata exceeded the checkpoint byte cap.", file=sys.stderr)
        store.finish_job(job_id, generation=generation, status=status, now=datetime.now(timezone.utc))
    return code


def enqueue_fts_backfill(store: AnalysisStore, config: dict[str, Any], *, rebuild: bool, now: datetime):
    if not history_client._message_fts_enabled(store.conn):
        raise ValueError("fts_unavailable")
    policy = analysis_settings.resolve_analysis_policy(config)["analysis"]
    try:
        batch = history_client.require_int_config_value(config, "sync", "batch_size", min_value=0)
    except SystemExit as exc:
        raise ValueError("Invalid sync.batch_size") from exc
    settings = {"schema_version": analysis_settings.ANALYSIS_SCHEMA_VERSION, "algorithm_version": analysis_settings.ANALYSIS_ALGORITHM_VERSION,
                "sync": {"batch_size": batch}, "analysis": {"max_intermediate_bytes": policy["max_intermediate_bytes"]},
                "process": asdict(common_process.load_process_config()),
                "bridge": {"worker_process_timeout_seconds": bridge.resolve_worker_process_timeout_seconds(config)}}
    payload = {"settings": settings, "internal": {"kind": "fts_backfill", "version": history_client.FTS_INDEX_VERSION}}
    return store.queue_index_job(payload, index_key=history_client.FTS_INDEX_ADMISSION_KEY,
                                  index_version=history_client.FTS_INDEX_VERSION, rebuild=rebuild,
                                  max_queued_jobs=policy["max_queued_jobs"], now=now)


def run_fts_backfill(store: AnalysisStore, job: dict[str, Any]) -> str:
    validate_fts_backfill_job(job)
    while True:
        state = history_client.backfill_fts_page(store, job["job_id"], generation=job["generation"])
        if state["state"] == "ready":
            return "completed"


def validate_fts_backfill_job(job: dict[str, Any]) -> None:
    settings = job["request"].get("settings", {})
    if (job["kind"] != "fts_backfill" or job["admission_key"] != history_client.FTS_INDEX_ADMISSION_KEY
            or job["requester_id"] is not None or job["destination"] is not None
            or job["request"].get("internal") != {"kind": "fts_backfill", "version": history_client.FTS_INDEX_VERSION}
            or settings.get("schema_version") != analysis_settings.ANALYSIS_SCHEMA_VERSION
            or settings.get("algorithm_version") != analysis_settings.ANALYSIS_ALGORITHM_VERSION):
        raise ValueError("Invalid internal index contract")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one durably admitted analytics job.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--worker-inner", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from common import sqlite as common_sqlite
    try:
        conn = common_sqlite.connect_sqlite(args.db)
        try:
            store = AnalysisStore(conn)
            if args.worker_inner:
                return run_analysis(store, args.db, job_id=args.job_id, generation=args.generation)
            previous = signal.signal(signal.SIGTERM, _worker_sigterm)
            try:
                return run_analysis_with_ttl(store, args.db, job_id=args.job_id, generation=args.generation)
            finally:
                signal.signal(signal.SIGTERM, previous)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        print("Analytic worker could not open its admitted job.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
