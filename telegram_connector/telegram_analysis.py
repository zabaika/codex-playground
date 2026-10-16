"""Connector-owned configuration and (incrementally implemented) analytics engine."""

import copy
import argparse
import asyncio
import codecs
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
from string import Formatter
import tomllib
import sys
import time
import uuid
import unicodedata
from typing import Any, Callable

if __package__:
    from . import telegram_bridge as bridge
    from . import telegram_history_client as history_client
    from . import analysis_evidence
else:
    import telegram_bridge as bridge
    import telegram_history_client as history_client
    import analysis_evidence

from common import process as common_process
from common import json_io as common_json_io
from telegram_shared import openai_api
from telegram_shared.bot_api import DEFAULT_API_TIMEOUT_SECONDS, split_text_chunks
from telegram_shared.errors import TelegramApiError
from telegram_shared.formatting import build_message_link
from telegram_shared.paths import resolve_app_paths
from telegram_shared.openai_usage import build_prompt_cache_info, extract_usage, log_openai_usage

if __package__:
    from .analysis_store import AdmissionConflictError, AnalysisStore, BudgetExceededError, QueueFullError, StaleGenerationError, canonical_json
else:
    from analysis_store import AdmissionConflictError, AnalysisStore, BudgetExceededError, QueueFullError, StaleGenerationError, canonical_json


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = APP_DIR / "config" / "runtime.example.toml"
_MISSING = object()
# Versioned application/template contracts, not operator sizing defaults.
ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ALGORITHM_VERSION = 7
ANALYSIS_NORMALIZATION_VERSION = 1


def build_analysis_message_link(channel, message_id):
    if channel.get("channel_type") != "Channel" or type(message_id) is not int or message_id <= 0:
        return None
    peer = channel.get("channel_id")
    if type(peer) is not int:
        return None
    if peer < 0:
        peer = -peer - 1000000000000
    # MTProto channel/monoforum ranges: https://core.telegram.org/api/bots/ids
    if not (1 <= peer <= 997852516352 or 1002147483649 <= peer <= 3000000000000):
        return None
    username = channel.get("username")
    username = username.strip().removeprefix("@") if isinstance(username, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", username):
        username = ""
    return build_message_link({"channel_id": peer, "username": username, "message_id": message_id})


def _verified_analysis_source(store, job, ref, *, supporting):
    pair = (ref["channel_id"], ref["message_id"])
    manifest = store.manifest_reference(job["job_id"], *pair)
    scope = job["checkpoint"].get("history", {}).get("refs", {}).values()
    if pair[0] not in scope or manifest is None or manifest["content_digest"] != ref["content_digest"]:
        raise ValueError("final_source_scope")
    request = job["request"]["request"]
    date = datetime.fromisoformat(manifest["date_utc"])
    if not datetime.fromisoformat(request["since"]) <= date <= min(
            datetime.fromisoformat(request["until"]), datetime.fromisoformat(request["cutoff_at"])):
        raise ValueError("final_source_period")
    message = _verified_source_snapshot(store, job, ref)
    if supporting:
        start, end = ref["fragment_start"], ref["fragment_end"]
        text = analysis_text_view(message)
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != ref["quote"] or ref["date_utc"] != manifest["date_utc"]):
            raise ValueError("final_source_span")
    channel = store.source_channel(pair[0])
    if channel is None:
        raise ValueError("final_source_channel")
    return {"channel_id": pair[0], "message_id": pair[1], "date_utc": manifest["date_utc"],
            "content_digest": manifest["content_digest"], "title": channel["title"],
            "quote": ref["quote"] if supporting else None,
            "url": build_analysis_message_link(channel, pair[1])}


def _qa_answer_pairs(relations):
    return {frozenset((relation["left_id"], relation["right_id"])) for relation in relations if relation["relation"] == "answers"}


def _validate_qa_support(ids, known, answer_pairs):
    answers = {key for key in ids if known[key]["record_type"] == "answer"}
    if not answers:
        raise ValueError("qa_answer_missing_support")
    questions = {key for key in ids if known[key]["record_type"] in {"question", "problem"}}
    if any(not any(frozenset((question, answer)) in answer_pairs for answer in answers) for question in questions):
        raise ValueError("qa_question_without_answer_link")


def verify_analysis_sources(store, job_id, result, *, generation):
    """Resolve application-owned provenance; this does not prove semantic entailment."""
    job = store.require_running(job_id, generation=generation)
    limit = _synthesis_limits(job)["sources_per_item"]
    verified = copy.deepcopy(result)
    verified.pop("sources", None)
    answer_pairs = _qa_answer_pairs(store.iter_link_decisions(job_id)) if job["command"] == "qa" else set()
    for item in verified.get("items", []):
        sources, records = {}, {}
        for identity in item["evidence_ids"]:
            record = store.leaf_evidence(job_id, identity)
            if record is None or not record["payload"]["source_refs"]:
                raise ValueError("final_evidence_scope")
            records[identity] = record["payload"]
            for ref in record["payload"]["source_refs"]:
                source = _verified_analysis_source(store, job, ref, supporting=True)
                pair = (source["channel_id"], source["message_id"])
                if pair not in sources or len(source["quote"]) > len(sources[pair]["quote"]):
                    sources[pair] = source
        if not sources:
            raise ValueError("final_evidence_without_support")
        if job["command"] == "qa":
            _validate_qa_support(item["evidence_ids"], records, answer_pairs)
        # Keep the latest update; remaining slots retain substantive backing
        # rather than letting a chain of short acknowledgements displace it.
        ordered = sorted(sources.values(), key=lambda s: (
            -datetime.fromisoformat(s["date_utc"]).timestamp() if job["command"] == "topics" else 0,
            -len(s["quote"]), s["channel_id"], s["message_id"]))
        if job["command"] == "topics":
            ordered = ordered[:1] + sorted(ordered[1:], key=lambda s: -len(s["quote"]))
        item["sources"] = ordered[:limit]
    if not verified.get("items"):
        verified["sources"] = [_verified_analysis_source(store, job, ref, supporting=False)
            for ref in store.processed_source_refs(job_id, limit=limit)]
        verified["source_role"] = "processed_inputs"
        verified.pop("source_refs", None)
    return verified


_PRESENTATION_URL = re.compile(r"(?:https?://|tg://|www\.|t\.me/)[^\s<>\"']+", re.IGNORECASE)


def _verify_presentation_urls(result):
    sources = [source for item in result.get("items", []) for source in item["sources"]] + result.get("sources", [])
    allowed = {s["url"] for s in sources if s["url"]}
    allowed.update(url for source in sources for url in _PRESENTATION_URL.findall(source["quote"] or ""))
    texts = [result.get("summary", ""), *result.get("limitations", [])]
    for item in result.get("items", []):
        texts.extend([item["title"], item["text"], *item["conditions"], *item["exceptions"], *item["contradictions"]])
    if any(url not in allowed for text in texts for url in _PRESENTATION_URL.findall(text)):
        raise ValueError("final_unverified_url")


def _telegram_units(text):
    return len(text.encode("utf-16-le")) // 2


def _escaped_analysis_parts(text, size, *, bold=False):
    rendered = html.escape(text)
    if bold:
        rendered = f"<b>{rendered}</b>"
    if _telegram_units(rendered) <= size:
        yield rendered
        return
    for part in split_text_chunks(text, max(1, len(text) // 2)):
        yield from _escaped_analysis_parts(part, size, bold=bold)


def _search_answer_notice(result):
    coverage = result.get("coverage", {})
    candidates = coverage.get("selection_mode") == "candidates"
    if result.get("partial") or result.get("kind") == "budget_stop":
        return "Полный подтверждённый ответ не сформирован: анализ или вывод ограничен."
    if candidates and coverage.get("selected_messages") == 0:
        return "В предварительной выборке сведения по запросу не найдены."
    scope = "предварительной выборке" if candidates else "выбранной истории"
    return f"В {scope} недостаточно сведений для полного подтверждённого ответа."


def _analysis_brief(job, result):
    if job["command"] == "search" and (not result.get("items") or not result.get("summary")):
        return _search_answer_notice(result)
    return result.get("summary", "")


def _analysis_coverage_lines(coverage):
    mode = coverage.get("selection_mode", "not_selected")
    selected, processed = coverage.get("selected_messages", 0), coverage.get("processed_messages", 0)
    lines = ["Режим: последовательный анализ" if mode == "all_messages" else
        "Отбор сообщений не завершён" if mode == "not_selected" else "Режим: анализ отобранных кандидатов",
        f'Обработано сообщений: {processed}; выбрано: {selected}; '
        f'не обработано из выбранных: {max(0, selected - processed)}.',
        f'Не включено в выборку: {coverage.get("known_skipped") if coverage.get("known_skipped") is not None else "неизвестно"}.']
    if mode == "candidates" and "candidate_messages" in coverage:
        lines.append(f'Кандидатов: {coverage["candidate_messages"]}; сообщений контекста: {coverage["context_messages"]}.')
    return lines


def _analysis_item_labels(job, item):
    return ("Вопрос: " + item["title"], "Ответ") if job["command"] == "qa" else (item["title"], "")


def _analysis_insufficiency_notice(job, result):
    if job["command"] == "search":
        return _search_answer_notice(result)
    if result.get("items"):
        return ("Сведений недостаточно для полной сводки." if job["command"] == "topics"
                else "Сведений недостаточно для полного ответа.")
    return "Недостаточно сведений для ответа."


def _analysis_html_chunks(job, result):
    size = min(4096, job["request"]["settings"]["bridge"]["text_chunk_size"])
    blocks = []
    def text(value, *, bold=False):
        if value and value.strip():
            blocks.extend(_escaped_analysis_parts(value, size, bold=bold))
    def sources(values, *, processed=False):
        if not values:
            return
        text("Источники обработанных сообщений" if processed else "Источники", bold=True)
        for number, source in enumerate(values, 1):
            if source["url"]:
                blocks.append(f'<a href="{html.escape(source["url"], quote=True)}">{number}</a>')
            else:
                text(f'{source["channel_id"]}/{source["message_id"]}: ссылка недоступна')
    request = job["request"]["request"]
    text({"topics": "Главные темы", "search": "Результат поиска", "qa": "Практические вопросы и ответы"}[job["command"]], bold=True)
    text(f'{request["since"][:10]} — {request["until"][:10]} UTC')
    if request.get("query"):
        text("Запрос: " + result.get("display_query", request["query"]))
    if result.get("partial") or result.get("kind") == "budget_stop":
        text("Частичный результат", bold=True)
        text("Причины: " + ", ".join(result.get("reasons", []) or [result.get("reason", "incomplete")]))
    if job["command"] == "search":
        text("Краткий ответ", bold=True)
    text(_analysis_brief(job, result))
    if job["command"] == "search" and result.get("items"):
        text("Подробности", bold=True)
    for item in result.get("items", []):
        title, answer_label = _analysis_item_labels(job, item)
        text(title, bold=True)
        text(answer_label, bold=True)
        text(item["text"])
        for field, label in (("conditions", "Условия"), ("exceptions", "Исключения"), ("contradictions", "Противоречия")):
            if item[field]:
                text(label, bold=True)
                for value in item[field]:
                    text(value)
        sources(item["sources"])
    if not result.get("items"):
        if job["command"] != "search":
            text("Проверенные утверждения не сформированы.")
        sources(result.get("sources", []), processed=True)
    if result.get("insufficient_information") and (result.get("items") or job["command"] != "search"):
        text(_analysis_insufficiency_notice(job, result))
    for value in result.get("limitations", []):
        text("Ограничение: " + value)
    for line in _analysis_coverage_lines(result.get("coverage", {})):
        text(line)
    chunks = []
    for block in blocks:
        if _telegram_units(block) > size:
            raise ValueError("presentation_block_too_large")
        if chunks and _telegram_units(chunks[-1] + "\n\n" + block) <= size:
            chunks[-1] += "\n\n" + block
        else:
            chunks.append(block)
    return chunks


def _reduce_analysis_output(store, job, value, reason):
    value.update(partial=True, reasons=sorted(set(value.get("reasons", [])) | {reason}))
    if value.get("items"):
        value["items"].pop()
        value["summary"] = ""
        if not value["items"]:
            value = verify_analysis_sources(store, job["job_id"], value, generation=job["generation"])
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
    limits = _synthesis_limits(job)
    value = verify_analysis_sources(store, job_id, result, generation=generation)
    value.update(command=job["command"], detail=limits["detail"], query=job["request"]["request"].get("query"))
    if value.get("kind") == "budget_stop":
        value.update(partial=True, reasons=sorted(set(value.get("reasons", [])) | {value["reason"]}))
        manifest = job["checkpoint"].get("manifest", {})
        value["coverage"] = {"selection_mode": value["selection_mode"], "selected_messages": manifest.get("selected", 0),
            "processed_messages": store.processed_analysis_messages(job_id), "known_skipped": manifest.get("known_skipped")}
    _verify_presentation_urls(value)
    if limits["detail"] == "full":
        return {"detail": "full", "result": value, "chunks": [], "parse_mode": "HTML"}
    maximum = job["request"]["settings"]["analysis"]["output"]["max_messages"]
    while True:
        chunks = _analysis_html_chunks(job, value)
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


def _markdown_text(text):
    return re.sub(r"([\\`*_{}\[\]()#+!|~])", r"\\\1", html.escape(text, quote=False))


def _full_analysis_markdown(job, result):
    request = job["request"]["request"]
    blocks = [f'# Аналитика Telegram: /{job["command"]}',
        "Каналы: " + _markdown_text(", ".join(request["channels"])),
        f'Период (UTC): {request["since"]} — {request["until"]}',
        f'Срез истории (UTC): {request["cutoff_at"]}']
    if request.get("query"):
        blocks.append("Запрос: " + _markdown_text(request["query"]))
    if result.get("partial"):
        blocks.extend(["**Частичный результат**", "Причины: " + _markdown_text(", ".join(result.get("reasons", [])))])
    if job["command"] == "search":
        blocks.append("## Краткий ответ")
    brief = _analysis_brief(job, result)
    if brief:
        blocks.append(_markdown_text(brief))
    if job["command"] == "search" and result.get("items"):
        blocks.append("## Подробности")
    def sources(values, *, processed=False):
        if not values:
            return
        blocks.append("Источники обработанных сообщений:" if processed else "Источники:")
        for source in values:
            identity = f'{source["channel_id"]}/{source["message_id"]}'
            link = f'[{identity}]({source["url"]})' if source["url"] else f'{identity}: ссылка недоступна'
            blocks.append(f'- {link} · {source["date_utc"]}')
    for item in result.get("items", []):
        title, answer_label = _analysis_item_labels(job, item)
        blocks.append("## " + _markdown_text(title.replace("\n", " ")))
        if answer_label:
            blocks.append("### " + answer_label)
        blocks.append(_markdown_text(item["text"]))
        for field, label in (("conditions", "Условия"), ("exceptions", "Исключения"), ("contradictions", "Противоречия")):
            if item[field]:
                blocks.append("### " + label)
                blocks.extend("- " + _markdown_text(value) for value in item[field])
        sources(item["sources"])
    if not result.get("items"):
        if job["command"] != "search":
            blocks.append("Проверенные утверждения не сформированы.")
        sources(result.get("sources", []), processed=True)
    if result.get("insufficient_information") and (result.get("items") or job["command"] != "search"):
        blocks.append(_markdown_text(_analysis_insufficiency_notice(job, result)))
    if result.get("limitations"):
        blocks.append("## Ограничения")
        blocks.extend("- " + _markdown_text(value) for value in result["limitations"])
    blocks.extend(["## Охват", *map(_markdown_text, _analysis_coverage_lines(result.get("coverage", {})))])
    return "\n\n".join(blocks).rstrip() + "\n"


def render_full_analysis_markdown(store, job_id, *, generation):
    job = store.require_running(job_id, generation=generation)
    if job["request"]["request"]["detail"] != "full" or job["result"] is None:
        raise ValueError("Full result is not prepared")
    value = prepare_analysis_presentation(store, job_id, job["result"], generation=generation)["result"]
    maximum = _synthesis_limits(job)["max_chars"]
    while True:
        text = _full_analysis_markdown(job, value)
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
        verify_analysis_sources(store, job_id, job["result"], generation=generation)
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
        verify_analysis_sources(store, job_id, job["result"], generation=generation)
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
        verify_analysis_sources(store, job_id, job["result"], generation=generation)
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


def request_analysis_json(store, job_id, batch_id, *, generation, endpoint, payload, api_key,
                          counted_input_tokens, deadline, urlopen_func=None, sleep_func=time.sleep,
                          monotonic_func=time.monotonic):
    """Bind the shared physical-attempt owner to this job's frozen policy."""
    if endpoint not in openai_api.OPENAI_JSON_ENDPOINTS:
        raise ValueError("Unsupported OpenAI endpoint")
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id:
        raise ValueError("Provider step outside job scope")
    settings = job["request"]["settings"]
    if payload.get("model") != settings["processing"]["model"]:
        raise ValueError("Provider model differs from frozen job settings")
    ai = settings["digest_ai"]
    request_kind = "inference" if endpoint == "responses" else "token_count"
    serialized = canonical_json(payload)
    cache_info = build_prompt_cache_info(cache_key=f"analysis:{batch_id}:{endpoint}",
        system_instructions=payload.get("instructions", ""), prompt_text=serialized, shared_prefix="")

    def before_attempt(_index):
        identity = uuid.uuid4().hex
        store.reserve_provider_attempt(job_id, batch_id, generation=generation, attempt_id=identity,
            request_kind=request_kind, input_tokens=counted_input_tokens if request_kind == "inference" else 0,
            output_tokens=payload.get("max_output_tokens", 0) if request_kind == "inference" else 0)
        return identity

    def after_attempt(identity, response, exc, latency):
        usage = None
        if request_kind == "inference" and response is not None:
            raw = response.get("usage")
            if isinstance(raw, dict):
                details = raw.get("input_tokens_details") or {}
                cached = details.get("cached_tokens", 0) if isinstance(details, dict) else None
                values = (raw.get("input_tokens"), raw.get("output_tokens"), cached)
                if all(type(value) is int and value >= 0 for value in values) and cached <= values[0]:
                    usage = dict(zip(("input_tokens", "output_tokens", "cached_input_tokens"), values))
        outcome = "succeeded" if exc is None else "unknown" if exc.error_type in {"network_error", "invalid_response"} else "failed"
        try:
            store.settle_provider_attempt(job_id, batch_id, generation=generation, attempt_id=identity,
                                          outcome=outcome, usage=usage)
        finally:
            current = store.get_job(job_id)
            log_openai_usage(store.conn, feature=job["command"], created_at=datetime.now(timezone.utc).isoformat(),
                stage=f"{batch['kind']}:{request_kind}", channel="", since=None, until=None,
                model=settings["processing"]["model"], request_index=current["provider_requests"], message_count=0,
                status=outcome, cache_info=cache_info, prompt_text=serialized,
                usage=extract_usage({"usage": {**usage, "total_tokens": usage["input_tokens"] + usage["output_tokens"],
                                    "input_tokens_details": {"cached_tokens": usage["cached_input_tokens"]}}},
                                    latency_ms=latency) if usage is not None else None,
                response_id=None, error=(f"http_{exc.status_code}" if exc.status_code is not None else "provider_error") if exc else None,
                store_prompt_text=ai["store_prompt_text"], correlation=(job_id, batch_id, identity), latency_ms=latency)

    kwargs = {"urlopen_func": urlopen_func} if urlopen_func is not None else {}
    return openai_api.post_json(payload, api_key, endpoint=endpoint,
        timeout_seconds=ai["openai_timeout_seconds"], retry_attempts=ai["openai_retry_attempts"],
        retry_backoff_seconds=ai["openai_retry_backoff_seconds"], before_attempt=before_attempt,
        after_attempt=after_attempt, deadline=deadline, monotonic_func=monotonic_func, sleep_func=sleep_func, **kwargs)


def resolve_analysis_model_capabilities(model):
    # Reviewed provider metadata, not inferred from the model name or system limits.
    # Endpoint/account availability is a separate rollout check.
    if model == "gpt-5.6-luna":
        return {"model": model, "context_window": 1050000, "max_input_tokens": 922000, "max_output_tokens": 128000,
            "source": "https://developers.openai.com/api/docs/models/gpt-5.6-luna", "verified_at": "2026-10-17"}
    # Example-config model; official page fetched 2026-10-16.
    if model not in {"gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"}:
        raise ValueError("model_capability_unverified")
    return {"model": model, "context_window": 400000, "max_input_tokens": 272000, "max_output_tokens": 128000,
        "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini", "verified_at": "2026-10-16"}


def prepare_analysis_payload(store, job_id, batch_id, *, generation, payload, capabilities, **transport):
    """Count exact input; capability metadata must be supplied by its verified owner."""
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id:
        raise ValueError("Preflight step outside active job scope")
    settings = job["request"]["settings"]
    model = settings["processing"]["model"]
    if (not isinstance(capabilities, dict) or capabilities.get("model") != model or not capabilities.get("source")
            or any(type(capabilities.get(k)) is not int or capabilities[k] <= 0 for k in ("context_window", "max_output_tokens"))):
        raise ValueError("model_capability_unverified")
    model_input = capabilities.get("max_input_tokens", capabilities["context_window"])
    if type(model_input) is not int or model_input <= 0:
        raise ValueError("model_capability_unverified")
    data = copy.deepcopy(payload)
    if data.get("model") != model:
        raise ValueError("Provider model differs from frozen job settings")
    # Versioned protocol subset used by this fixed workflow. Unknown input-affecting
    # fields must be supported explicitly rather than silently omitted from count.
    input_fields = {"model", "input", "instructions", "text", "reasoning"}
    if set(data) - input_fields - {"max_output_tokens", "store", "metadata"}:
        raise ValueError("unsupported_preflight_field")
    output = data.get("max_output_tokens")
    profile = settings["analysis"]["output"]["profiles"][job["request"]["request"]["detail"]]
    cap = profile["max_output_tokens"] if batch["kind"] == "synthesize" else settings["digest_ai"]["max_output_tokens"]
    if type(output) is not int or output <= 0 or output > min(cap, capabilities["max_output_tokens"]):
        raise ValueError("model_output_limit")
    count_payload = {key: value for key, value in data.items() if key in input_fields}
    digest = hashlib.sha256(canonical_json(count_payload).encode("utf-8")).hexdigest()
    count = batch["payload"].get("token_counts", {}).get(digest)
    if count is None:
        response = request_analysis_json(store, job_id, batch_id, generation=generation,
            endpoint="input_tokens", payload=count_payload, counted_input_tokens=0, **transport)
        count = response.response.get("input_tokens")
        if type(count) is not int or count < 0:
            raise ValueError("input_token_count_unavailable")
        store.save_token_count(job_id, batch_id, generation=generation, payload_digest=digest, input_tokens=count)
    if count > min(settings["analysis"]["max_input_tokens"], model_input):
        raise ValueError("max_input_tokens")
    if count + output > capabilities["context_window"]:
        raise ValueError("model_context_limit")
    return {"payload": data, "input_tokens": count, "input_digest": digest}


def _extraction_fragments(fragments):
    return [{"message": f["message"], "input": {key: value for key, value in f["input"].items() if key != "batch_id"}}
            for f in fragments]


def extraction_cache_key(job, batch):
    settings = job["request"]["settings"]
    request = job["request"]["request"]
    identity = {"principal": {"namespace": job["request"]["principal"]["namespace"],
        "requester": job["requester_id"], "destination": job["destination"], "reader": settings.get("auth", {})},
        "scope": {key: request.get(key) for key in ("since", "until")},
        "schema_version": settings["schema_version"], "algorithm_version": settings["algorithm_version"],
        "normalization_version": ANALYSIS_NORMALIZATION_VERSION,
        "prompt_hash": settings["analysis_prompts"]["hash"],
        "payload": build_extraction_payload(job, batch["messages"], batch["context"])}
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _cache_provenance(job, snapshot, now):
    source = snapshot["job"]
    settings, original = job["request"]["settings"], source["request"]["settings"]
    if (not original["analysis"]["cache"]["enabled"]
            or job["request"]["principal"]["namespace"] != source["request"]["principal"]["namespace"]
            or settings["processing"]["model"] != original["processing"]["model"]
            or settings["analysis_prompts"]["hash"] != original["analysis_prompts"]["hash"]
            or settings.get("auth", {}) != original.get("auth", {})
            or any(settings[key] != original[key] for key in ("schema_version", "algorithm_version"))):
        return None
    provenance = snapshot["batch"]["payload"].get("provenance")
    try:
        inferred_at = datetime.fromisoformat(provenance["inferred_at"])
    except (KeyError, TypeError, ValueError):
        return None
    retention = min(settings["analysis"]["retention_days"], original["analysis"]["retention_days"])
    if inferred_at.tzinfo is None or not 0 <= (now - inferred_at).total_seconds() <= retention * 24 * 60 * 60:
        return None
    return provenance


def try_reuse_query_expansion(store, job_id, batch_id, *, generation, validator, now):
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if (not job["request"]["settings"]["analysis"]["cache"]["enabled"] or batch is None
            or batch["job_id"] != job_id or batch["kind"] != "expand_query" or not batch["cache_key"]
            or any(a["request_kind"] == "inference" for a in batch["attempts"])):
        return False
    snapshot = store.load_leaf_cache(job_id, batch["cache_key"], kind="expand_query")
    if snapshot is None or snapshot["batch"]["payload"]["plan"] != batch["payload"]["plan"]:
        return False
    provenance = _cache_provenance(job, snapshot, now)
    if provenance is None:
        return False
    try:
        output = validator(copy.deepcopy(snapshot["batch"]["payload"]["output"]))
    except BudgetExceededError:
        raise
    except (KeyError, TypeError, ValueError):
        return False
    store.complete_batch(job_id, batch_id, generation=generation, records=[], inputs=[],
                          json_output=output, provenance={**provenance, "cache_hit": True})
    return True


def try_reuse_extraction(store, job_id, batch, *, generation, now):
    job = store.require_running(job_id, generation=generation)
    if not job["request"]["settings"]["analysis"]["cache"]["enabled"]:
        return False
    target = store.get_batch(batch["batch_id"])
    if target is None or target["job_id"] != job_id or any(a["request_kind"] == "inference" for a in target["attempts"]):
        return False
    identity = extraction_cache_key(job, batch)
    snapshot = store.load_leaf_cache(job_id, identity, kind="extract")
    if snapshot is None:
        return False
    provenance = _cache_provenance(job, snapshot, now)
    if provenance is None:
        return False
    properties = analysis_evidence.EVIDENCE_SCHEMA["properties"]["records"]["items"]["properties"]
    ref_fields = properties["source_refs"]["items"]["properties"]
    try:
        values = []
        saved_payloads = []
        for row in snapshot["records"]:
            value = json.loads(row["payload_json"])
            saved_payloads.append(value)
            item = {key: copy.deepcopy(value[key]) for key in properties}
            item["source_refs"] = [{key: ref[key] for key in ref_fields} for ref in item["source_refs"]]
            values.append(item)
        records = validate_evidence_records(store, job_id, batch["batch_id"], generation=generation,
            document={"records": values, "saturated": False}, fragments=batch["messages"] + batch["context"])
        if len(records) != len(snapshot["records"]):
            return False
        mapping = {old["record_id"]: new["record_id"] for old, new in zip(snapshot["records"], records)}
        for value, record in zip(saved_payloads, records):
            record["payload"]["related_record_ids"] = [mapping[key] for key in value["related_record_ids"] if key in mapping]
    except (KeyError, TypeError, ValueError) as exc:
        if str(exc) == "input_changed" or isinstance(exc, BudgetExceededError):
            raise
        return False
    store.complete_batch(job_id, batch["batch_id"], generation=generation, records=records,
                          inputs=[f["input"] for f in batch["messages"] + batch["context"]],
                          provenance={**provenance, "cache_hit": True})
    return True


def _analysis_reasoning(settings):
    ai = settings["digest_ai"]
    reasoning = {"effort": ai["reasoning_effort"]}
    if ai["reasoning_summary"] != "none":
        reasoning["summary"] = ai["reasoning_summary"]
    return reasoning


def normalize_search_query(query, policy):
    if not isinstance(query, str) or not query.strip() or len(query) > policy["max_query_chars"]:
        raise ValueError("invalid_search_query")
    normalized = " ".join(unicodedata.normalize("NFC", query).split())
    phrases = [next(value for value in match if value) for match in re.findall(r'"([^"\n]+)"|«([^»]+)»|“([^”]+)”', normalized)]
    tokens = re.findall(r"[+-]?[^\W_]+(?:[’'./:-][^\W_]+)*", normalized)
    terms, seen, limited = [], set(), False
    for value in [*phrases, *tokens]:
        value = value.strip()
        identity = value.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        if len(value) > policy["max_term_chars"] or len(terms) >= policy["max_query_terms"]:
            limited = True
        else:
            terms.append(value)
    return {"query": query, "normalized_query": normalized, "original_terms": terms, "expanded_terms": [],
            "expansion_status": "not_requested", "reasons": ["original_terms_limited"] if limited else []}


def _analysis_query(job):
    query = job["request"]["request"].get("query") or ""
    if job["command"] == "search":
        return normalize_search_query(query, job["request"]["settings"]["analysis"]["search"])["normalized_query"]
    return query


def validate_search_terms(value, policy, original_terms):
    analysis_evidence.validate_shape(value, analysis_evidence.QUERY_TERMS_SCHEMA)
    if len(value["terms"]) > policy["max_query_terms"]:
        raise ValueError("query_terms_limit")
    terms, seen = [], {term.casefold() for term in original_terms}
    for term in value["terms"]:
        term = " ".join(unicodedata.normalize("NFC", term).split())
        if not term or len(term) > policy["max_term_chars"]:
            raise ValueError("query_term_limit")
        if term.casefold() not in seen:
            terms.append(term)
            seen.add(term.casefold())
    return {"terms": terms}


def prepare_search_query(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    if job["command"] != "search":
        raise ValueError("Query preparation requires search")
    policy, bundle = settings["analysis"]["search"], settings["analysis_prompts"]
    prepared = normalize_search_query(request["query"], policy)
    if request["mode"] == "full":
        return prepared
    payload = {"model": settings["processing"]["model"],
        "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
        "input": render_analysis_prompt(bundle, "prepare_query", query=prepared["normalized_query"],
            max_terms=policy["max_query_terms"], max_term_chars=policy["max_term_chars"]),
        "text": {"format": analysis_evidence.query_terms_format()}, "reasoning": _analysis_reasoning(settings),
        "max_output_tokens": settings["digest_ai"]["max_output_tokens"], "store": False}
    digest = hashlib.sha256(canonical_json({"query": request["query"], "payload": payload,
        "prompt_hash": bundle["hash"], "schema_version": settings["schema_version"],
        "algorithm_version": settings["algorithm_version"]}).encode("utf-8")).hexdigest()
    plan = {"query": prepared["normalized_query"], "original_terms": prepared["original_terms"],
        "max_terms": policy["max_query_terms"], "max_term_chars": policy["max_term_chars"]}
    batch = None
    validator = lambda value: validate_search_terms(value, policy, prepared["original_terms"])
    def fallback(outcome):
        if batch["state"] != "failed":
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=outcome)
        return {**prepared, "expansion_status": "fallback", "reasons": [*prepared["reasons"], "query_expansion_" + outcome]}
    try:
        batch = store.plan_batch(job_id, generation=generation, step_key="expand_query/" + digest,
            kind="expand_query", payload=plan, input_digest=digest,
            cache_key=digest if settings["analysis"]["cache"]["enabled"] else None)
        if batch["state"] not in {"succeeded", "failed"} and try_reuse_query_expansion(
                store, job_id, batch["batch_id"], generation=generation, validator=validator, now=datetime.now(timezone.utc)):
            batch = store.get_batch(batch["batch_id"])
        if batch["state"] == "succeeded":
            output = validator(batch["payload"]["output"])
        elif batch["state"] == "failed":
            return fallback(batch["payload"]["outcome"])
        else:
            try:
                result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation,
                    payload=payload, **transport)
            except openai_api.OpenAIRequestError:
                return fallback("provider_error")
            except ValueError as exc:
                if str(exc) in {"max_input_tokens", "model_context_limit"}:
                    return fallback("saturated")
                if str(exc) == "input_token_count_unavailable":
                    return fallback("provider_error")
                raise
            try:
                outcome, text = analysis_evidence.response_text(result["response"])
                output = validator(analysis_evidence.parse_document(text)) if outcome == "completed" else None
            except ValueError:
                return fallback("invalid_schema")
            if outcome != "completed":
                return fallback(outcome)
            attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"])
            store.complete_batch(job_id, batch["batch_id"], generation=generation, records=[], inputs=[], json_output=output,
                attempt_id=result["attempt_id"], usage=attempt["usage"], provenance={
                    "actual_model": result["response"].get("model"), "inferred_at": attempt["created_at"], "cache_hit": False})
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        prepared.update(expansion_status="fallback")
        prepared["reasons"].append(exc.reason)
        return prepared
    prepared.update(expanded_terms=output["terms"], expansion_status="completed")
    return prepared


def build_extraction_payload(job, messages, context):
    settings = job["request"]["settings"]
    bundle = settings["analysis_prompts"]
    return {"model": settings["processing"]["model"],
            "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
            "input": render_analysis_prompt(bundle, "extract", command_rubric=bundle["commands"][job["command"]],
                query=_analysis_query(job), messages=canonical_json(_extraction_fragments(messages)),
                context=canonical_json(_extraction_fragments(context))),
            "reasoning": _analysis_reasoning(settings), "text": {"format": analysis_evidence.evidence_format()},
            "max_output_tokens": settings["digest_ai"]["max_output_tokens"], "store": False}


def _relation_keys(record, store):
    payload = record["payload"]
    keys = {("entity", " ".join(value.casefold().split())) for value in payload["entities"] if value.strip()}
    if payload["topic_key"]:
        keys.add(("topic", " ".join(payload["topic_key"].casefold().split())))
    for ref in payload["source_refs"] + payload["related_message_refs"]:
        keys.add(("message", f"{ref['channel_id']}:{ref['message_id']}"))
    for ref in payload["source_refs"]:
        reply = store.reply_message_id(ref["channel_id"], ref["message_id"])
        if reply is not None:
            keys.add(("message", f"{ref['channel_id']}:{reply}"))
    return sorted(keys)


def iter_relation_candidates(store, job_id, *, generation):
    job = store.require_running(job_id, generation=generation)
    policy = job["request"]["settings"]["analysis"]
    count, limited = 0, False
    def qa_role(record):
        if job["command"] == "qa":
            if record["record_type"] in {"question", "problem"}:
                return "question"
            if record["record_type"] == "answer":
                return "answer"
        return None
    def index_keys(record):
        keys = _relation_keys(record, store)
        role = qa_role(record)
        return keys + ([("qa_role", role)] if role else [])
    with store.relation_index(job_id, generation=generation, key_func=index_keys):
        for left in store.iter_leaf_evidence(job_id, page_size=max(1, job["request"]["settings"]["sync"]["batch_size"])):
            store.require_running(job_id, generation=generation)
            ids = store.relation_candidate_ids(job_id, left["record_id"], keys=_relation_keys(left, store),
                                               limit=policy["relation_candidates_per_record"])
            role = qa_role(left)
            if role and len(ids) <= policy["relation_candidates_per_record"]:
                # Keep indexed hints first, then propose cross-language/wording
                # pairs without requiring identical extracted entity/topic keys.
                extra = store.relation_candidate_ids(job_id, left["record_id"],
                    keys=[("qa_role", "answer" if role == "question" else "question")],
                    limit=policy["relation_candidates_per_record"])
                ids += [record_id for record_id in extra if record_id not in ids]
            limited |= len(ids) > policy["relation_candidates_per_record"]
            for record_id in ids[:policy["relation_candidates_per_record"]]:
                if count >= policy["max_relation_pairs"]:
                    limited = True
                    break
                right = store.leaf_evidence(job_id, record_id)
                if right is not None:
                    count += 1
                    yield {"left": left, "right": right}
            if count >= policy["max_relation_pairs"] and limited:
                break
    current = store.require_running(job_id, generation=generation)
    checkpoint = {**current["checkpoint"], "relations": {**current["checkpoint"].get("relations", {}), "candidate_pairs": count, "limited": limited}}
    store.save_checkpoint(job_id, checkpoint, generation=generation, max_bytes=policy["max_intermediate_bytes"])


def _technical_record_identity(record):
    properties = analysis_evidence.EVIDENCE_SCHEMA["properties"]["records"]["items"]["properties"]
    value = {key: copy.deepcopy(record["payload"][key]) for key in properties}
    ref_keys = properties["source_refs"]["items"]["properties"]
    value["source_refs"] = [{key: ref[key] for key in ref_keys} for ref in value["source_refs"]]
    return canonical_json(value)


def _verify_relation_sources(store, job, records):
    checked = set()
    for record in records.values():
        for ref in record["payload"]["source_refs"]:
            identity = (ref["channel_id"], ref["message_id"], ref["content_digest"])
            if identity not in checked:
                _verified_source_snapshot(store, job, ref)
                checked.add(identity)


def _validate_relation_document(document, wanted, *, max_bytes):
    analysis_evidence.validate_shape(document, analysis_evidence.RELATION_SCHEMA)
    if len(canonical_json(document).encode("utf-8")) > max_bytes:
        raise ValueError("relation_output_limit")
    seen = set()
    for decision in document["decisions"]:
        pair = tuple(sorted((decision["left_id"], decision["right_id"])))
        if pair not in wanted or pair in seen:
            raise ValueError("relation_pair_scope")
        seen.add(pair)
    if not document["saturated"] and seen != wanted:
        raise ValueError("relation_missing_disposition")
    return document


def _link_pair_group(store, job_id, pairs, *, generation, depth=0, **transport):
    job = store.require_running(job_id, generation=generation)
    settings = job["request"]["settings"]
    policy = settings["analysis"]
    records = {record["record_id"]: record for pair in pairs for record in (pair["left"], pair["right"])}
    _verify_relation_sources(store, job, records)
    identities = [[pair["left"]["record_id"], pair["right"]["record_id"]] for pair in pairs]
    plan = {"pairs": identities, "record_digests": {key: hashlib.sha256(canonical_json(value["payload"]).encode("utf-8")).hexdigest()
                                                    for key, value in sorted(records.items())}}
    digest = hashlib.sha256(canonical_json(plan).encode("utf-8")).hexdigest()
    batch = store.plan_batch(job_id, generation=generation, step_key=f"link/{digest}", kind="link", payload=plan, input_digest=digest)
    if batch["state"] == "succeeded":
        return False
    if batch["state"] == "failed":
        outcome = batch["payload"].get("outcome")
        output_limited = outcome == "saturated" or (outcome == "incomplete" and any(
            a["request_kind"] == "inference" and a.get("usage")
            and a["usage"]["output_tokens"] >= a["output_limit"] for a in batch["attempts"]))
        if not output_limited or len(pairs) <= 1 or depth >= policy["max_split_depth"]:
            return True
    if batch["state"] in {"failed", "skipped"}:
        middle = len(pairs) // 2
        left = _link_pair_group(store, job_id, pairs[:middle], generation=generation, depth=depth + 1, **transport)
        right = _link_pair_group(store, job_id, pairs[middle:], generation=generation, depth=depth + 1, **transport)
        return left or right
    technical, semantic = [], []
    for pair in pairs:
        if _technical_record_identity(pair["left"]) == _technical_record_identity(pair["right"]):
            technical.append({"left_id": pair["left"]["record_id"], "right_id": pair["right"]["record_id"],
                              "relation": "duplicate", "reason": "Идентичное извлечение из тех же версий и фрагментов источников."})
        else:
            semantic.append([pair["left"]["record_id"], pair["right"]["record_id"]])
    result = None
    if semantic:
        bundle = settings["analysis_prompts"]
        data = {"pairs": semantic, "records": [{"record_id": key, "payload": value["payload"]} for key, value in sorted(records.items())]}
        payload = {"model": settings["processing"]["model"], "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
            "input": render_analysis_prompt(bundle, "link", command_rubric=bundle["commands"][job["command"]],
                query=_analysis_query(job), records=canonical_json(data)),
            "reasoning": _analysis_reasoning(settings),
            "text": {"format": analysis_evidence.relation_format()}, "max_output_tokens": settings["digest_ai"]["max_output_tokens"], "store": False}
        while True:
            try:
                result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
            except BudgetExceededError:
                raise
            except ValueError as exc:
                if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                    raise
                attempted = store.get_batch(batch["batch_id"])["attempts"]
                if len(pairs) > 1 and depth < policy["max_split_depth"] and not any(a["request_kind"] == "inference" for a in attempted):
                    store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
                    return _link_pair_group(store, job_id, pairs, generation=generation, depth=depth, **transport)
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="max_split_depth")
                return True
            invalid = ""
            try:
                outcome, invalid = analysis_evidence.response_text(result["response"])
                if outcome != "completed":
                    store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=outcome)
                    return _link_pair_group(store, job_id, pairs, generation=generation, depth=depth, **transport)
                document = _validate_relation_document(analysis_evidence.parse_document(invalid),
                    {tuple(sorted(pair)) for pair in semantic}, max_bytes=policy["max_intermediate_bytes"])
                if document["saturated"] and len(semantic) == 1:
                    raise ValueError("relation_saturated")
            except BudgetExceededError:
                raise
            except ValueError as exc:
                if store.claim_repair(job_id, batch["batch_id"], generation=generation):
                    payload = {**payload, "input": render_analysis_prompt(bundle, "repair", invalid_output=canonical_json({"invalid_output": invalid, "candidates": data}), validation_errors=str(exc))}
                    continue
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation,
                    outcome="saturated" if str(exc) == "relation_saturated" else "invalid_schema")
                return True
            if document["saturated"]:
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="saturated")
                return _link_pair_group(store, job_id, pairs, generation=generation, depth=depth, **transport)
            break
    else:
        document = {"decisions": [], "saturated": False}
    document["decisions"].extend(technical)
    _verify_relation_sources(store, job, records)
    attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"]) if result else None
    store.complete_batch(job_id, batch["batch_id"], generation=generation, records=[], inputs=[], json_output=document,
        attempt_id=result["attempt_id"] if result else None, usage=attempt["usage"] if attempt else None)
    return False


def link_analysis_records(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    policy = job["request"]["settings"]["analysis"]
    partial, pending = False, []
    try:
        for pair in iter_relation_candidates(store, job_id, generation=generation):
            pending.append(pair)
            if len(pending) >= policy["relation_candidates_per_record"]:
                partial |= _link_pair_group(store, job_id, pending, generation=generation, **transport)
                pending = []
        if pending:
            partial |= _link_pair_group(store, job_id, pending, generation=generation, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        partial = True
    current = store.require_running(job_id, generation=generation)
    partial |= current["checkpoint"].get("relations", {}).get("limited", False)
    checkpoint = {**current["checkpoint"], "relations": {**current["checkpoint"].get("relations", {}), "partial": partial}}
    store.save_checkpoint(job_id, checkpoint, generation=generation, max_bytes=policy["max_intermediate_bytes"])
    return {"outcome": "partial" if partial else "succeeded", "partial": partial}


def _merge_reference(kind, batch_id, node_id, payload):
    return {"kind": kind, "batch_id": batch_id, "node_id": node_id,
            "digest": hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()}


def _synthesis_limits(job):
    request, settings = job["request"]["request"], job["request"]["settings"]
    output = settings["analysis"]["output"]
    profile = output["profiles"][request["detail"]]
    limit, sources = request["limit"], request["sources"]
    if type(limit) is not int or not 1 <= limit <= min(profile["max_items"], output["max_items"]):
        raise ValueError("synthesis_limit")
    if type(sources) is not int or not 1 <= sources <= output["sources_per_item"]:
        raise ValueError("synthesis_sources_limit")
    return {"detail": request["detail"], "max_items": limit, "sources_per_item": sources,
            "sentences_per_item": profile["sentences_per_item"], "max_chars": profile["max_chars"],
            "max_output_tokens": profile["max_output_tokens"]}


def build_synthesis_payload(job, evidence):
    settings, request = job["request"]["settings"], job["request"]["request"]
    bundle = settings["analysis_prompts"]
    profile = settings["analysis"]["output"]["profiles"][request["detail"]]
    # Keep classifier explanations in the ledger. The final model needs the
    # verified edges and source facts, without repeating each link diagnostic.
    wire_evidence = {**evidence, "relations": [
        {key: relation[key] for key in ("left_id", "right_id", "relation")}
        for relation in evidence.get("relations", [])]}
    return {"model": settings["processing"]["model"], "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
        "input": render_analysis_prompt(bundle, "synthesize", command_rubric=bundle["commands"][job["command"]],
            query=_analysis_query(job), detail_instructions=bundle["details"][request["detail"]],
            limits=canonical_json(_synthesis_limits(job)), evidence=canonical_json(wire_evidence)),
        "reasoning": _analysis_reasoning(settings),
        "text": {"format": analysis_evidence.synthesis_format()}, "max_output_tokens": profile["max_output_tokens"], "store": False}


def _synthesis_evidence(store, job):
    state = job["checkpoint"].get("merge")
    if state is None or state["state"] not in {"ready", "partial"}:
        raise ValueError("synthesis_merge_not_ready")
    for _ in iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"]):
        pass
    groups, records, seen, included = [], [], set(), set()
    reasons = set(state["reasons"]) | set(job["checkpoint"]["manifest"].get("reasons", []))
    stop = job["checkpoint"].get("budget_stop")
    if stop:
        reasons.add(stop["reason"])
    for ref in state["frontier"]:
        node = _resolve_merge_node(store, job["job_id"], ref)
        value = node["payload"]
        groups.append({"node_id": ref["node_id"], "title": value.get("title", value.get("topic_key")),
                       "summary": value.get("summary", value.get("claim")), "evidence_ids": []})
    size = len(canonical_json(groups))
    guard = job["request"]["settings"]["digest_ai"]["message_block_max_chars"]
    def members():
        # Give each ranked group a representative before filling from the first
        # group. Only one lineage iterator is live; do not materialize all facts.
        for first_pass in (True, False):
            for ref, group in zip(state["frontier"], groups):
                for index, record in enumerate(iter_merge_leaf_records(store, job["job_id"], ref, generation=job["generation"])):
                    if first_pass:
                        yield group, record
                        break
                    if index:
                        yield group, record

    for group, record in members():
        identity = record["record_id"]
        seen.add(identity)
        value = {key: record[key] for key in ("record_id", "record_type", "payload")}
        length = (0 if identity in included else len(canonical_json(value))) + len(canonical_json([identity])) - 1
        if size + length <= guard:
            if identity not in included:
                records.append(value)
                included.add(identity)
            group["evidence_ids"].append(identity)
            size += length
        else:
            reasons.add("synthesis_evidence_limited")
    selected = {record["record_id"] for record in records}
    relations = [value for value in store.iter_link_decisions(job["job_id"])
                 if value["left_id"] in selected and value["right_id"] in selected]
    manifest = job["checkpoint"]["manifest"]
    return {"groups": [group for group in groups if group["evidence_ids"]], "records": records, "relations": relations,
        "partial": state["partial"] or bool(reasons) or manifest.get("partial", False), "reasons": sorted(reasons),
        "coverage": {"selection_mode": manifest["selection_mode"], "selected_messages": manifest["selected"],
            "processed_messages": store.processed_analysis_messages(job["job_id"]), "known_skipped": manifest.get("known_skipped"),
            "ranked_evidence_records": len(seen), "synthesis_evidence_records": len(records),
            **({"candidate_messages": manifest["selected_candidates"], "context_messages": manifest["context_messages"]}
               if manifest["selection_mode"] == "candidates" else {})}}


def _empty_synthesis_document(*, insufficient=False):
    return {"summary": "", "items": [], "limitations": [], "insufficient_information": insufficient, "saturated": False}


def _unique_analysis_details(values):
    """Remove formatting duplicates without conflating different conditions."""
    result, seen = [], set()
    for text in values:
        key = " ".join(unicodedata.normalize("NFC", text).split())
        if key.endswith(".") and not key.endswith(".."):
            key = key[:-1]
        if key and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _synthesis_result(job, evidence, document, *, reason=None):
    analysis_evidence.validate_shape(document, analysis_evidence.SYNTHESIS_SCHEMA)
    limits = _synthesis_limits(job)
    known = {value["record_id"]: value["payload"] for value in evidence["records"]}
    answer_pairs = _qa_answer_pairs(evidence.get("relations", [])) if job["command"] == "qa" else set()
    value = copy.deepcopy(document)
    for item in value["items"]:
        ids = item["evidence_ids"]
        if not ids or len(set(ids)) != len(ids) or not set(ids) <= set(known):
            raise ValueError("synthesis_evidence_scope")
        original_ids = set(ids)
        for relation in evidence.get("relations", []):
            pair = [relation["left_id"], relation["right_id"]]
            if relation["relation"] == "contradicts" and original_ids.intersection(pair) and set(pair) <= set(known):
                ids.extend(key for key in pair if key not in ids)
                item["contradictions"] = list(dict.fromkeys([*item["contradictions"], *(known[key]["claim"] for key in pair)]))
        if job["command"] == "qa":
            _validate_qa_support(ids, known, answer_pairs)
        for field in ("conditions", "exceptions"):
            item[field] = _unique_analysis_details(text for key in ids for text in known[key][field])
    value["items"] = value["items"][:limits["max_items"]]
    if job["command"] == "search" and value["items"] and not value["summary"].strip():
        raise ValueError("search_answer_missing_summary")
    reasons = set(evidence["reasons"])
    if reason:
        reasons.add(reason)
    if value["saturated"]:
        reasons.add("output_saturated")
    def text_size():
        return len(value["summary"]) + sum(map(len, value["limitations"])) + sum(
            len(item["title"]) + len(item["text"]) + sum(len(text) for field in ("conditions", "exceptions", "contradictions") for text in item[field])
            for item in value["items"])
    while text_size() > limits["max_chars"] and value["items"]:
        value["items"].pop()
        value["summary"] = ""
        reasons.add("max_chars")
    if text_size() > limits["max_chars"]:
        value.update(summary="", limitations=[])
        reasons.add("max_chars")
    if not value["items"]:
        value["summary"] = ""
        if job["command"] in {"search", "qa"}:
            value["insufficient_information"] = True
    return {key: value[key] for key in ("summary", "items", "limitations", "insufficient_information")} | {
        "command": job["command"], "query": job["request"]["request"].get("query"), "detail": job["request"]["request"]["detail"],
        "partial": evidence["partial"] or bool(reasons), "reasons": sorted(reasons), "coverage": copy.deepcopy(evidence["coverage"])}


def synthesize_analysis_result(store, job_id, *, generation, **transport):
    try:
        return _synthesize_analysis_result(store, job_id, generation=generation, **transport)
    except BudgetExceededError as exc:
        return store.record_budget_stop(job_id, generation=generation, reason=exc.reason)


def _synthesize_analysis_result(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    evidence = _synthesis_evidence(store, job)
    if job["result"] is not None and job["result"].get("detail") == job["request"]["request"]["detail"]:
        return job["result"]
    policy = job["request"]["settings"]["analysis"]
    split_depth = 0
    while evidence["records"]:
        payload = build_synthesis_payload(job, evidence)
        original_task = payload["input"]
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        plan = {"evidence": [{"record_id": value["record_id"], "digest": hashlib.sha256(canonical_json(value["payload"]).encode("utf-8")).hexdigest()}
                            for value in evidence["records"]], "detail": job["request"]["request"]["detail"], "input_digest": digest}
        batch = store.plan_batch(job_id, generation=generation, step_key=f"synthesize/{digest}", kind="synthesize", payload=plan, input_digest=digest)
        if batch["state"] == "failed":
            final = _synthesis_result(job, evidence, _empty_synthesis_document(), reason=batch["payload"]["outcome"])
            store.save_analysis_result(job_id, final, generation=generation)
            return final
        try:
            prepared = prepare_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
        except BudgetExceededError:
            raise
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
            if len(evidence["records"]) <= 1 or split_depth >= policy["max_split_depth"]:
                evidence["partial"] = True
                evidence["reasons"] = sorted(set(evidence["reasons"]) | {str(exc)})
                break
            evidence["records"] = evidence["records"][:len(evidence["records"]) // 2]
            selected = {value["record_id"] for value in evidence["records"]}
            for group in evidence["groups"]:
                group["evidence_ids"] = [key for key in group["evidence_ids"] if key in selected]
            evidence["groups"] = [group for group in evidence["groups"] if group["evidence_ids"]]
            evidence["relations"] = [value for value in evidence["relations"] if value["left_id"] in selected and value["right_id"] in selected]
            evidence["coverage"]["synthesis_evidence_records"] = len(selected)
            evidence.update(partial=True, reasons=sorted(set(evidence["reasons"]) | {"synthesis_evidence_limited"}))
            split_depth += 1
            continue
        while True:
            result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=prepared["payload"], **transport)
            for _ in iter_analysis_manifest_messages(store, job_id, generation=generation):
                pass
            invalid = ""
            reason = None
            try:
                outcome, invalid = analysis_evidence.response_text(result["response"])
                if outcome != "completed":
                    reason = outcome
                    final = _synthesis_result(job, evidence, _empty_synthesis_document(), reason=reason)
                else:
                    document = analysis_evidence.parse_document(invalid)
                    final = _synthesis_result(job, evidence, document)
            except ValueError as exc:
                diagnostic = str(exc)
                if diagnostic not in {"synthesis_evidence_scope", "qa_answer_missing_support",
                                      "qa_question_without_answer_link", "search_answer_missing_summary"}:
                    diagnostic = "invalid_schema"
                print(f"Analytic synthesis rejected: job={job_id} code={diagnostic}", file=sys.stderr)
                reason = "invalid_schema"
                if store.claim_repair(job_id, batch["batch_id"], generation=generation):
                    payload = {**payload, "input": render_analysis_prompt(job["request"]["settings"]["analysis_prompts"], "repair",
                        invalid_output=canonical_json({"invalid_output": invalid, "original_task": original_task}), validation_errors=str(exc))}
                    try:
                        prepared = prepare_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
                    except BudgetExceededError:
                        raise
                    except ValueError as limit:
                        if str(limit) not in {"max_input_tokens", "model_context_limit"}:
                            raise
                        evidence["reasons"] = sorted(set(evidence["reasons"]) | {str(limit)})
                        reason = "incomplete"
                    else:
                        continue
                final = _synthesis_result(job, evidence, _empty_synthesis_document(), reason=reason)
            if reason:
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=reason)
                store.save_analysis_result(job_id, final, generation=generation)
            else:
                for record in evidence["records"]:
                    _verify_relation_sources(store, job, {record["record_id"]: record})
                attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"])
                store.complete_batch(job_id, batch["batch_id"], generation=generation, records=[], inputs=[], json_output=document,
                    attempt_id=result["attempt_id"], usage=attempt["usage"], final_result=final)
            return final
    final = _synthesis_result(job, evidence, _empty_synthesis_document(insufficient=evidence["coverage"]["ranked_evidence_records"] == 0))
    store.save_analysis_result(job_id, final, generation=generation)
    return final


def _resolve_merge_node(store, job_id, reference):
    if reference["kind"] == "evidence":
        record = store.leaf_evidence(job_id, reference["node_id"])
        if record is None or record["batch_id"] != reference["batch_id"]:
            raise ValueError("merge_lineage_scope")
        payload = record["payload"]
    elif reference["kind"] == "merge":
        batch = store.get_batch(reference["batch_id"])
        if batch is None or batch["job_id"] != job_id or batch["kind"] != "merge" or batch["state"] != "succeeded":
            raise ValueError("merge_lineage_scope")
        payload = next((node for node in batch["payload"]["output"]["groups"] if node["node_id"] == reference["node_id"]), None)
        if payload is None:
            raise ValueError("merge_lineage_scope")
    else:
        raise ValueError("merge_lineage_kind")
    if _merge_reference(reference["kind"], reference["batch_id"], reference["node_id"], payload) != reference:
        raise ValueError("merge_lineage_digest")
    return {"node_id": reference["node_id"], "kind": reference["kind"], "payload": payload}


def iter_merge_leaf_records(store, job_id, reference, *, generation):
    job = store.require_running(job_id, generation=generation)
    emitted = set()
    stack = [(reference, ())]
    while stack:
        ref, path = stack.pop()
        store.require_running(job_id, generation=generation)
        if ref["node_id"] in path or len(path) > job["request"]["settings"]["analysis"]["max_merge_depth"]:
            raise ValueError("merge_lineage_cycle_or_depth")
        node = _resolve_merge_node(store, job_id, ref)
        if ref["kind"] == "evidence":
            if ref["node_id"] not in emitted:
                record = store.leaf_evidence(job_id, ref["node_id"])
                _verify_relation_sources(store, job, {ref["node_id"]: record})
                emitted.add(ref["node_id"])
                yield record
        else:
            batch = store.get_batch(ref["batch_id"])
            parents = {value["node_id"]: value for value in batch["payload"]["plan"]["inputs"]}
            for key in reversed(node["payload"]["input_ids"]):
                if key not in parents:
                    raise ValueError("merge_lineage_scope")
                stack.append((parents[key], (*path, ref["node_id"])))


def _validate_merge_document(value, references, *, max_bytes):
    analysis_evidence.validate_shape(value, analysis_evidence.MERGE_SCHEMA)
    if len(canonical_json(value).encode("utf-8")) > max_bytes:
        raise ValueError("merge_output_limit")
    wanted = {ref["node_id"] for ref in references}
    used = set()
    for group in value["groups"]:
        if not group["input_ids"] or len(set(group["input_ids"])) != len(group["input_ids"]) or not set(group["input_ids"]) <= wanted:
            raise ValueError("merge_lineage_scope")
        used.update(group["input_ids"])
    dispositions = {item["input_id"]: item["disposition"] for item in value["dispositions"]}
    if set(dispositions) != wanted or len(dispositions) != len(value["dispositions"]):
        raise ValueError("merge_missing_disposition")
    if any((key in used) != (status in {"used", "merged", "duplicate"}) for key, status in dispositions.items()):
        raise ValueError("merge_disposition_lineage")
    return value


def _save_merge_state(store, job, state):
    current = store.require_running(job["job_id"], generation=job["generation"])
    store.save_checkpoint(job["job_id"], {**current["checkpoint"], "merge": state}, generation=job["generation"],
        max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])


def _advance_merge_frontier(state, references, produced, *, terminal):
    advanced = copy.deepcopy(state)
    advanced["cursor"] += len(references)
    advanced["next_frontier"].extend(produced)
    if advanced["cursor"] == len(state["frontier"]):
        advanced.update(frontier=advanced["next_frontier"], next_frontier=[], cursor=0, depth=state["depth"] + 1)
        if terminal or not advanced["frontier"]:
            advanced["state"] = "ready"
        elif len(advanced["frontier"]) >= len(state["frontier"]):
            advanced.update(state="partial", partial=True, reasons=sorted(set(advanced["reasons"]) | {"merge_no_progress"}))
    return advanced


def _stop_merge_state(state, reason):
    return {**state, "state": "partial", "partial": True, "reasons": sorted(set(state["reasons"]) | {reason}),
            "frontier": state["next_frontier"] + state["frontier"][state["cursor"]:], "cursor": 0, "next_frontier": []}


def _merge_group(store, job_id, state, references, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    settings, policy = job["request"]["settings"], job["request"]["settings"]["analysis"]
    operation = "rank" if state["cursor"] == 0 and len(references) == len(state["frontier"]) else "merge"
    plan = {"inputs": references, "depth": state["depth"] + 1, "operation": operation}
    digest = hashlib.sha256(canonical_json(plan).encode("utf-8")).hexdigest()
    batch = store.plan_batch(job_id, generation=generation, step_key=f"merge/{digest}", kind="merge", payload=plan, input_digest=digest)
    if batch["state"] == "failed":
        return None, batch["payload"]["outcome"]
    nodes = [_resolve_merge_node(store, job_id, ref) for ref in references]
    leaf_roots = {}
    for ref in references:
        for record in iter_merge_leaf_records(store, job_id, ref, generation=generation):
            leaf_roots.setdefault(record["record_id"], []).append(ref["node_id"])
    relations = []
    for decision in store.iter_link_decisions(job_id):
        for left in leaf_roots.get(decision["left_id"], []):
            for right in leaf_roots.get(decision["right_id"], []):
                relations.append({**decision, "left_id": left, "right_id": right,
                                  "leaf_ids": [decision["left_id"], decision["right_id"]]})
    bundle = settings["analysis_prompts"]
    payload = {"model": settings["processing"]["model"], "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
        "input": render_analysis_prompt(bundle, "merge", command_rubric=bundle["commands"][job["command"]],
            query=_analysis_query(job), records=canonical_json({"operation": operation, "inputs": nodes, "relations": relations})),
        "reasoning": _analysis_reasoning(settings),
        "text": {"format": analysis_evidence.merge_format()}, "max_output_tokens": settings["digest_ai"]["max_output_tokens"], "store": False}
    result = None
    if batch["state"] == "succeeded":
        cached = batch["payload"]["output"]
        properties = analysis_evidence.MERGE_SCHEMA["properties"]["groups"]["items"]["properties"]
        value = {**cached, "groups": [{key: group[key] for key in properties} for group in cached["groups"]]}
        _validate_merge_document(value, references, max_bytes=policy["max_intermediate_bytes"])
    while batch["state"] != "succeeded":
        try:
            result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
        except BudgetExceededError:
            raise
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            attempted = store.get_batch(batch["batch_id"])["attempts"]
            if (len(references) > 2 and state.get("input_splits", 0) < policy["max_split_depth"]
                    and not any(a["request_kind"] == "inference" for a in attempted)):
                store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
                adaptive = {**state, "fan_in": max(2, len(references) // 2), "input_splits": state.get("input_splits", 0) + 1}
                _save_merge_state(store, job, adaptive)
                return adaptive, None
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="incomplete")
            return None, str(exc)
        invalid = ""
        try:
            outcome, invalid = analysis_evidence.response_text(result["response"])
            if outcome != "completed":
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=outcome)
                return None, outcome
            value = _validate_merge_document(analysis_evidence.parse_document(invalid), references, max_bytes=policy["max_intermediate_bytes"])
        except ValueError as exc:
            if store.claim_repair(job_id, batch["batch_id"], generation=generation):
                payload = {**payload, "input": render_analysis_prompt(bundle, "repair", invalid_output=canonical_json({"invalid_output": invalid, "inputs": nodes}), validation_errors=str(exc))}
                continue
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="invalid_schema")
            return None, "invalid_schema"
        break
    if value["saturated"]:
        store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="saturated")
        return None, "saturated"
    output = copy.deepcopy(value)
    for index, group in enumerate(output["groups"]):
        group["node_id"] = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-analysis-merge:{job_id}:{batch['batch_id']}:{index}").hex
    advanced = _advance_merge_frontier(state, references,
        [_merge_reference("merge", batch["batch_id"], group["node_id"], group) for group in output["groups"]], terminal=operation == "rank")
    if any(item["disposition"] == "excluded" for item in value["dispositions"]):
        advanced["partial"] = True
        advanced["reasons"] = sorted(set(advanced["reasons"]) | {"merge_excluded"})
    for ref in references:
        for _ in iter_merge_leaf_records(store, job_id, ref, generation=generation):
            pass
    attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"]) if result else None
    store.complete_merge_batch(job_id, batch["batch_id"], generation=generation, expected_state=state,
        next_state=advanced, output=output, attempt_id=result["attempt_id"] if result else None, usage=attempt["usage"] if attempt else None)
    return advanced, None


def merge_analysis_records(store, job_id, *, generation, **transport):
    try:
        return _merge_analysis_records(store, job_id, generation=generation, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        current = store.require_running(job_id, generation=generation)
        state = current["checkpoint"].get("merge", {"frontier": [], "next_frontier": [], "cursor": 0, "depth": 0, "reasons": []})
        return _stop_merge_state(state, exc.reason)


def _merge_analysis_records(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    policy = job["request"]["settings"]["analysis"]
    state = job["checkpoint"].get("merge")
    if state is None:
        frontier = [_merge_reference("evidence", row["batch_id"], row["record_id"], row["payload"])
                    for row in store.iter_leaf_evidence(job_id, page_size=max(1, job["request"]["settings"]["sync"]["batch_size"]))]
        reasons = store.incomplete_evidence_reasons(job_id)
        if job["checkpoint"].get("relations", {}).get("partial") and "relations_incomplete" not in reasons:
            reasons.append("relations_incomplete")
        state = {"state": "running" if frontier else "ready", "frontier": frontier, "next_frontier": [],
                 "cursor": 0, "depth": 0, "partial": bool(reasons), "reasons": reasons}
        _save_merge_state(store, job, state)
    for ref in state["frontier"] + state["next_frontier"]:
        for _ in iter_merge_leaf_records(store, job_id, ref, generation=generation):
            pass
    while state["state"] == "running":
        if len(state["frontier"]) == 1 and state["cursor"] == 0 and not state["next_frontier"]:
            state = {**state, "state": "ready"}
            _save_merge_state(store, job, state)
            break
        if state["depth"] >= policy["max_merge_depth"]:
            state = _stop_merge_state(state, "max_merge_depth")
            _save_merge_state(store, job, state)
            break
        references = state["frontier"][state["cursor"]:state["cursor"] + state.get("fan_in", policy["merge_fan_in"])]
        if len(references) == 1 and len(state["frontier"]) > 1:
            state = _advance_merge_frontier(state, references, references, terminal=False)
            _save_merge_state(store, job, state)
            continue
        try:
            advanced, reason = _merge_group(store, job_id, state, references, generation=generation, **transport)
        except BudgetExceededError as exc:
            store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
            advanced, reason = None, exc.reason
        if advanced is None:
            state = _stop_merge_state(state, reason)
            _save_merge_state(store, job, state)
            break
        state = advanced
    store.finalize_leaf_dispositions(job_id, generation=generation)
    return state


def extract_analysis_batch(store, job_id, batch, *, generation, **transport):
    try:
        return _extract_analysis_batch(store, job_id, batch, generation=generation, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        return {"outcome": "budget_exhausted", "partial": True, "batch_id": batch["batch_id"]}


def _verified_source_snapshot(store, job, ref):
    try:
        message, digest = _analysis_message_snapshot(store, ref["channel_id"], ref["message_id"],
            job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
    except ValueError:
        message, digest = None, None
    if digest != ref["content_digest"]:
        store.interrupt_changed_input(job["job_id"], generation=job["generation"], source=ref)
        raise ValueError("input_changed")
    return message


def _restore_extraction_batch(store, job, batch_id):
    current = store.get_batch(batch_id)
    if current is None or current["job_id"] != job["job_id"] or current["kind"] != "extract":
        raise ValueError("Split child outside job scope")
    restored = {"batch_id": batch_id}
    sources = {}
    for name, key in (("messages", "inputs"), ("context", "context")):
        fragments = []
        for ref in current["payload"]["plan"][key]:
            pair = (ref["channel_id"], ref["message_id"])
            if pair not in sources:
                sources[pair] = _verified_source_snapshot(store, job, ref)
            message = sources[pair]
            text = analysis_text_view(message)[ref["fragment_start"]:ref["fragment_end"]]
            fields = {key: value for key, value in message.items() if key not in {"text", "media"}}
            fragments.append({"message": {**fields, "text": text, "text_view_version": 1}, "input": ref})
        restored[name] = fragments
    return restored


def _continue_extraction_split(store, job, parent, *, generation, **transport):
    results = [extract_analysis_batch(store, job["job_id"], _restore_extraction_batch(store, job, child),
               generation=generation, **transport) for child in parent["payload"]["split_children"]]
    return {"outcome": "split", "partial": any(result["partial"] for result in results), "batch_id": parent["batch_id"]}


def _split_extraction_output(store, job, batch, *, generation, **transport):
    current = store.get_batch(batch["batch_id"])
    depth = current["payload"]["plan"].get("split_depth", 0)
    messages = batch["messages"]
    if depth >= job["request"]["settings"]["analysis"]["max_split_depth"] or not messages or (len(messages) == 1 and len(messages[0]["message"]["text"]) <= 1):
        store.set_batch_outcome(job["job_id"], batch["batch_id"], generation=generation, outcome="max_split_depth")
        return {"outcome": "max_split_depth", "partial": True, "batch_id": batch["batch_id"]}
    if len(messages) > 1:
        middle = len(messages) // 2
        parts = [messages[:middle], messages[middle:]]
    else:
        item = messages[0]
        text = item["message"]["text"]
        middle, offset = len(text) // 2, item["input"]["fragment_start"]
        parts = [[{"message": {**item["message"], "text": text[start:end]},
                   "input": {**item["input"], "fragment_start": offset + start, "fragment_end": offset + end}}]
                 for start, end in ((0, middle), (middle, len(text)))]
    children = [{"plan": {"inputs": [f["input"] for f in part], "context": [f["input"] for f in batch["context"]], "split_depth": depth + 1},
                 "cache_key": extraction_cache_key(job, {"messages": part, "context": batch["context"]}) if job["request"]["settings"]["analysis"]["cache"]["enabled"] else None,
                 "input_digest": hashlib.sha256(canonical_json(build_extraction_payload(job, part, batch["context"])).encode("utf-8")).hexdigest()}
                for part in parts]
    store.split_extraction_batch(job["job_id"], batch["batch_id"], generation=generation, children=children)
    return _continue_extraction_split(store, job, store.get_batch(batch["batch_id"]), generation=generation, **transport)


def _extract_analysis_batch(store, job_id, batch, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    batch_id = batch["batch_id"]
    current = store.get_batch(batch_id)
    if current is None or current["job_id"] != job_id or current["kind"] != "extract":
        raise ValueError("Extraction step outside job scope")
    if current["state"] == "succeeded":
        return {"outcome": "succeeded", "partial": False, "batch_id": batch_id}
    if current["state"] == "failed":
        return {"outcome": current["payload"]["outcome"], "partial": True, "batch_id": batch_id}
    if current["payload"].get("split_children"):
        return _continue_extraction_split(store, job, current, generation=generation, **transport)
    validate_evidence_records(store, job_id, batch_id, generation=generation,
                             document={"records": [], "saturated": False}, fragments=batch["messages"] + batch["context"])
    if try_reuse_extraction(store, job_id, batch, generation=generation, now=datetime.now(timezone.utc)):
        return {"outcome": "succeeded", "partial": False, "batch_id": batch_id}
    payload = build_extraction_payload(job, batch["messages"], batch["context"])
    while True:
        try:
            result = request_counted_analysis_payload(store, job_id, batch_id, generation=generation, payload=payload, **transport)
        except BudgetExceededError:
            raise
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            return _split_extraction_output(store, job, batch, generation=generation, **transport)
        response = result["response"]
        text = ""
        try:
            outcome, text = analysis_evidence.response_text(response)
            if outcome == "refused":
                store.set_batch_outcome(job_id, batch_id, generation=generation, outcome="refused")
                return {"outcome": "refused", "partial": True, "batch_id": batch_id}
            if outcome == "incomplete":
                details = response.get("incomplete_details")
                if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
                    return _split_extraction_output(store, job, batch, generation=generation, **transport)
                store.set_batch_outcome(job_id, batch_id, generation=generation, outcome="incomplete")
                return {"outcome": "incomplete", "partial": True, "batch_id": batch_id}
            document = analysis_evidence.parse_document(text)
            records = validate_evidence_records(store, job_id, batch_id, generation=generation, document=document,
                                                fragments=batch["messages"] + batch["context"], bind_model_sources=True)
        except BudgetExceededError:
            raise
        except ValueError as exc:
            if str(exc) == "input_changed":
                raise
            if store.claim_repair(job_id, batch_id, generation=generation):
                payload = {**payload, "input": render_analysis_prompt(job["request"]["settings"]["analysis_prompts"], "repair",
                           invalid_output=canonical_json({"invalid_output": text, "messages": batch["messages"],
                                                          "context": batch["context"]}), validation_errors=str(exc))}
                continue
            store.set_batch_outcome(job_id, batch_id, generation=generation, outcome="invalid_schema")
            return {"outcome": "invalid_schema", "partial": True, "batch_id": batch_id}
        break
    if document["saturated"]:
        return _split_extraction_output(store, job, batch, generation=generation, **transport)
    attempt = next(a for a in store.get_batch(batch_id)["attempts"] if a["attempt_id"] == result["attempt_id"])
    store.complete_batch(job_id, batch_id, generation=generation, records=records,
                         inputs=[f["input"] for f in batch["messages"] + batch["context"]],
                         attempt_id=result["attempt_id"], usage=attempt["usage"],
                         provenance={"actual_model": response.get("model") if isinstance(response.get("model"), str) else None,
                                     "inferred_at": attempt["created_at"], "cache_hit": False})
    return {"outcome": "succeeded", "partial": False, "batch_id": batch_id}


def validate_evidence_records(store, job_id, batch_id, *, generation, document, fragments, bind_model_sources=False):
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id or batch["kind"] != "extract":
        raise ValueError("Evidence step outside job scope")
    analysis_evidence.validate_shape(document)
    policy = job["request"]["settings"]["analysis"]
    if len(document["records"]) > policy["max_evidence_records"] or len(canonical_json(document).encode("utf-8")) > policy["max_intermediate_bytes"]:
        raise ValueError("evidence_output_limit")
    sources = {}
    for fragment in fragments:
        ref = fragment["input"]
        pair = (ref["channel_id"], ref["message_id"])
        if pair not in sources:
            manifest = store.manifest_reference(job_id, *pair)
            if manifest is None or manifest["content_digest"] != ref["content_digest"]:
                raise ValueError("evidence_source_scope")
            source = _verified_source_snapshot(store, job, ref)
            sources[pair] = {"text": analysis_text_view(source), "manifest": manifest, "fragments": []}
        source = sources[pair]
        start, end = ref["fragment_start"], ref["fragment_end"]
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(source["text"])
                or source["text"][start:end] != fragment["message"]["text"]):
            raise ValueError("evidence_fragment_mismatch")
        source["fragments"].append(ref)
    records = []
    for index, proposed in enumerate(document["records"]):
        item = copy.deepcopy(proposed)
        if not item["source_refs"]:
            raise ValueError("evidence_without_support")
        if item["event_at"] is not None:
            try:
                datetime.fromisoformat(item["event_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("evidence_event_date") from exc
        for ref in item["source_refs"]:
            source = sources.get((ref["channel_id"], ref["message_id"]))
            if source is not None and bind_model_sources:
                # Model chooses the source and exact quote; mechanical metadata
                # comes only from the verified manifest and supplied fragments.
                matches = set()
                for fragment in source["fragments"]:
                    lower, upper = fragment["fragment_start"], fragment["fragment_end"]
                    start = source["text"].find(ref["quote"], lower, upper)
                    while start != -1:
                        matches.add((start, start + len(ref["quote"])))
                        start = source["text"].find(ref["quote"], start + 1, upper)
                if len(matches) != 1:
                    raise ValueError("evidence_supporting_span")
                start, end = next(iter(matches))
                ref.update(content_digest=source["manifest"]["content_digest"], fragment_start=start, fragment_end=end)
            if source is None or ref["content_digest"] != source["manifest"]["content_digest"]:
                raise ValueError("evidence_source_scope")
            start, end = ref["fragment_start"], ref["fragment_end"]
            parent = next((f for f in source["fragments"] if f["fragment_start"] <= start < end <= f["fragment_end"]), None)
            if parent is None or source["text"][start:end] != ref["quote"]:
                raise ValueError("evidence_supporting_span")
            ref.update(role=parent["role"], date_utc=source["manifest"]["date_utc"], text_view_version=1)
        if any((r["channel_id"], r["message_id"]) not in sources for r in item["related_message_refs"]):
            raise ValueError("evidence_related_scope")
        selected = next((ref for ref in item["source_refs"] if ref["role"] == "selected"), None)
        if selected is None:
            continue
        if item["topic_key"] is not None:
            item["topic_key"] = item["topic_key"].strip() or None
        item.update(reported_at=selected["date_utc"], related_record_ids=[], schema_version=analysis_evidence.EVIDENCE_SCHEMA_VERSION)
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-analysis:{job_id}:{batch_id}:{index}:{canonical_json(item)}").hex
        records.append({"record_id": identity, "record_type": item["record_type"], "topic_key": item["topic_key"], "payload": item})
    return records


def request_counted_analysis_payload(store, job_id, batch_id, *, generation, payload, capabilities, **transport):
    prepared = prepare_analysis_payload(store, job_id, batch_id, generation=generation,
                                        payload=payload, capabilities=capabilities, **transport)
    response = request_analysis_json(store, job_id, batch_id, generation=generation,
        endpoint="responses", payload=prepared["payload"], counted_input_tokens=prepared["input_tokens"], **transport)
    return {**prepared, "response": response.response, "latency_ms": response.latency_ms, "attempt_id": response.attempt_id}


def analysis_text_view(message):
    """Version 1 fragment offsets address text followed by ordinal OCR blocks."""
    parts = [message.get("text") or ""]
    for asset in sorted(message.get("media", []), key=lambda value: value["ordinal"]):
        if asset.get("ocr_text"):
            parts.append(f"\n[OCR {asset['ordinal']}]\n{asset['ocr_text']}")
    return "".join(parts)


def iter_analysis_batches(store, job_id, *, generation, build_payload, capabilities, **transport):
    try:
        yield from _iter_analysis_batches(store, job_id, generation=generation, build_payload=build_payload,
                                         capabilities=capabilities, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)


def _iter_analysis_batches(store, job_id, *, generation, build_payload, capabilities, **transport):
    """Stream the fixed manifest; recursively recount splits and bounded context."""
    job = store.require_running(job_id, generation=generation)
    settings = job["request"]["settings"]
    policy, ai = settings["analysis"], settings["digest_ai"]
    maximum, guard = ai["messages_per_ai_pass"], ai["message_block_max_chars"]
    overlap = min(policy["overlap_messages"], maximum - 1)
    previous, stopped, seen = [], False, set()

    def counted(selected, context, key, depth=0):
        data = build_payload(selected, context)
        plan = {"inputs": [item["input"] for item in selected], "context": [item["input"] for item in context], "split_depth": depth}
        digest = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
        existing = store.find_extraction_plan(job_id, input_digest=digest, plan=plan)
        if existing is None and store.get_batch_by_step(job_id, key) is not None:
            key += f"/input/{digest}"
        cache_key = (existing["cache_key"] if existing else extraction_cache_key(job, {"messages": selected, "context": context})
                     if policy["cache"]["enabled"] else None)
        batch = existing or store.plan_batch(job_id, generation=generation, step_key=key, kind="extract",
            payload=plan, input_digest=digest, cache_key=cache_key)
        seen.add(batch["batch_id"])
        ready = prepare_analysis_payload(store, job_id, batch["batch_id"], generation=generation,
                                         payload=data, capabilities=capabilities, **transport)
        return {**ready, "batch_id": batch["batch_id"], "messages": selected, "context": context}

    try:
        minimal = counted([], [], "extract/preflight")
    except ValueError as exc:
        if str(exc) in {"max_input_tokens", "model_context_limit"}:
            raise ValueError("minimal_payload_does_not_fit") from exc
        raise
    store.skip_counted_batch(job_id, minimal["batch_id"], generation=generation)

    def emit(selected, key, depth=0, *, use_context=True):
        nonlocal previous, stopped
        if stopped:
            return
        context = [{"message": item["message"], "input": {**item["input"], "role": "context"}}
                   for item in previous[-overlap:]] if use_context and overlap else []
        if context and context[-1]["input"]["channel_id"] != selected[0]["input"]["channel_id"]:
            context = []
        if sum(len(item["message"]["text"]) for item in selected + context) > guard:
            context = []
        try:
            ready = counted(selected, context, key, depth)
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            batch = store.get_batch_by_step(job_id, key)
            store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
            if context:
                yield from emit(selected, key + "/without-context", depth, use_context=False)
            elif depth < policy["max_split_depth"] and len(selected) > 1:
                middle = len(selected) // 2
                yield from emit(selected[:middle], key + "/0", depth + 1)
                yield from emit(selected[middle:], key + "/1", depth + 1)
            elif depth < policy["max_split_depth"] and len(selected[0]["message"]["text"]) > 1:
                item = selected[0]
                middle = len(item["message"]["text"]) // 2
                offset = item["input"]["fragment_start"]
                for index, (start, end) in enumerate(((0, middle), (middle, len(item["message"]["text"])))):
                    fragment = {"message": {**item["message"], "text": item["message"]["text"][start:end]},
                                "input": {**item["input"], "fragment_start": offset + start, "fragment_end": offset + end}}
                    yield from emit([fragment], key + f"/{index}", depth + 1)
            else:
                stopped = True
                store.record_budget_stop(job_id, generation=generation, reason="max_split_depth")
            return
        previous = selected[-overlap:] if overlap else []
        if store.get_batch(ready["batch_id"])["state"] != "succeeded":
            yield ready

    pending, size, page = [], 0, 0
    for source in iter_analysis_manifest_messages(store, job_id, generation=generation):
        if stopped:
            return
        text = analysis_text_view(source["message"])
        for start in range(0, max(1, len(text)), guard):
            store.require_running(job_id, generation=generation)
            end = min(start + guard, len(text))
            message = {key: value for key, value in source["message"].items() if key not in {"text", "media"}}
            fragment = {"message": {**message, "text": text[start:end], "text_view_version": 1},
                        "input": {**source["input"], "fragment_start": start, "fragment_end": end}}
            if pending and (len(pending) >= maximum - min(overlap, len(previous)) or size + end - start > guard
                            or pending[0]["input"]["channel_id"] != fragment["input"]["channel_id"]):
                yield from emit(pending, f"extract/{page:08d}")
                if stopped:
                    return
                page += 1
                pending, size = [], 0
            pending.append(fragment)
            size += end - start
    if pending and not stopped:
        yield from emit(pending, f"extract/{page:08d}")
    if not stopped:
        store.reconcile_extraction_batches(job_id, generation=generation, seen=seen)


PROMPT_FIELDS = {
    "prepare_query": frozenset({"query", "max_terms", "max_term_chars"}),
    "extract": frozenset({"command_rubric", "query", "messages", "context"}),
    "link": frozenset({"command_rubric", "query", "records"}),
    "merge": frozenset({"command_rubric", "query", "records"}),
    "synthesize": frozenset({"command_rubric", "detail_instructions", "query", "evidence", "limits"}),
    "repair": frozenset({"invalid_output", "validation_errors"}),
}


def _leaf_paths(data: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.extend(_leaf_paths(value, path) if isinstance(value, dict) else [path])
    return paths


def _resolve_new_settings(default: Any, supplied: Any, path: str, sources: dict[str, str]) -> Any:
    if isinstance(default, dict):
        if supplied is _MISSING:
            supplied = {}
        if not isinstance(supplied, dict) or supplied.keys() - default.keys():
            raise ValueError(f"Invalid section or unknown setting: {path}")
        return {
            key: _resolve_new_settings(value, supplied.get(key, _MISSING), f"{path}.{key}", sources)
            for key, value in default.items()
        }
    sources[path] = "default" if supplied is _MISSING else "config"
    value = copy.deepcopy(default) if supplied is _MISSING else supplied
    if type(default) is bool:
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        if type(value) is not bool:
            raise ValueError(f"Expected boolean: {path}")
    elif type(default) is int:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
            value = int(value)
        minimum = 0 if path in {"analysis.overlap_messages", "analysis.max_repair_attempts"} else 1
        if path == "analysis.merge_fan_in":
            minimum = 2
        if type(value) is not int or value < minimum:
            raise ValueError(f"Expected integer >= {minimum}: {path}")
    elif isinstance(default, list):
        if (not isinstance(value, list) or len(value) != 2
                or any(type(item) is not int for item in value)
                or not 1 <= value[0] <= value[1]):
            raise ValueError(f"Expected sentence range [min,max]: {path}")
        value = list(value)
    elif not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected nonempty string: {path}")
    return value


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


def load_analysis_prompts(config: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    """Use the digest bundle's local-TOML pattern with explicit field validation."""
    filename = config["analysis_prompts"]["file"]
    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    try:
        blob = path.read_bytes()
        values = tomllib.loads(blob.decode("utf-8"))["analysis_prompts"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("Missing or invalid analysis_prompts bundle") from exc
    required_strings = ("version", "system_instructions", "shared_prefix")
    required_keys = set(required_strings) | {"templates", "commands", "details"}
    if not isinstance(values, dict) or set(values) != required_keys or any(
        not isinstance(values.get(key), str) or not values[key].strip()
        for key in required_strings
    ):
        raise ValueError("Invalid analysis_prompts metadata or instructions")
    for section, names in (
        ("templates", set(PROMPT_FIELDS)), ("commands", {"topics", "search", "qa"}),
        ("details", {"short", "normal", "full"}),
    ):
        data = values.get(section)
        if not isinstance(data, dict) or set(data) != names or any(
            not isinstance(value, str) or not value.strip() for value in data.values()
        ):
            raise ValueError(f"Invalid analysis_prompts.{section}")
    for stage, required in PROMPT_FIELDS.items():
        try:
            parts = list(Formatter().parse(values["templates"][stage]))
        except ValueError as exc:
            raise ValueError(f"Invalid prompt template: {stage}") from exc
        actual = {field for _, field, _, _ in parts if field is not None}
        if actual != required or any(spec or conversion for _, _, spec, conversion in parts):
            raise ValueError(f"Invalid prompt placeholders: {stage}")
    return {**values, "file": filename, "hash": hashlib.sha256(blob).hexdigest()}


def render_analysis_prompt(bundle: dict[str, Any], stage: str, **values: str | int) -> str:
    required = PROMPT_FIELDS.get(stage)
    if required is None or set(values) != required or any(type(value) not in {str, int} for value in values.values()):
        raise ValueError("Invalid analysis prompt arguments")
    return bundle["templates"][stage].format_map(values)


def resolve_analysis_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Validate new limits independently of inference credentials/model settings."""
    defaults = tomllib.loads(DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    sources: dict[str, str] = {}
    settings = {
        key: _resolve_new_settings(defaults[key], config.get(key, _MISSING), key, sources)
        for key in ("analysis", "analysis_prompts")
    }
    analysis = settings["analysis"]
    output = analysis["output"]
    if analysis["search"]["mode"] not in {"fast", "full"}:
        raise ValueError("Invalid analysis.search.mode")
    if output["default_detail"] not in output["profiles"]:
        raise ValueError("Invalid analysis.output.default_detail")
    for name, profile in output["profiles"].items():
        if not profile["default_limit"] <= profile["max_items"] <= output["max_items"]:
            raise ValueError(f"Incompatible analysis.output.profiles.{name} limits")
    results_path = Path(output["results_dir"])
    if (results_path.is_absolute() or not results_path.parts
            or results_path.parts[0] != "scratch" or ".." in results_path.parts):
        raise ValueError("analysis.output.results_dir must remain under repo scratch")
    settings["settings_sources"] = sources
    return settings


def resolve_analysis_settings(config: dict[str, Any], *, config_dir: Path) -> dict[str, Any]:
    """Build a secret-free policy; tracked example owns new compatibility defaults."""
    settings = resolve_analysis_policy(config)
    sources = settings["settings_sources"]
    reused = _reused_settings(config)
    settings.update(reused)
    for path in _leaf_paths(reused):
        section, key = path.split(".", 1)
        sources[path] = "common-config" if section in {"process", "sqlite"} else (
            "config" if key in config.get(section, {}) else "legacy-default"
        )
    settings["settings_sources"] = sources
    settings["analysis_prompts"] = load_analysis_prompts(settings, base_dir=config_dir)
    settings["schema_version"] = ANALYSIS_SCHEMA_VERSION
    settings["algorithm_version"] = ANALYSIS_ALGORITHM_VERSION
    return settings


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
        capabilities = capabilities or resolve_analysis_model_capabilities(settings["processing"]["model"])
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
            build_search_manifest(store, job_id, generation=generation, **transport)
        else:
            build_all_messages_manifest(store, job_id, generation=generation)
        phase("extract")
        for batch in iter_analysis_batches(store, job_id, generation=generation,
                build_payload=lambda messages, context: build_extraction_payload(
                    store.require_running(job_id, generation=generation), messages, context), **transport):
            extract_analysis_batch(store, job_id, batch, generation=generation, **transport)
        phase("link")
        link_analysis_records(store, job_id, generation=generation, **transport)
        phase("merge")
        merge_analysis_records(store, job_id, generation=generation, **transport)
        phase("synthesize")
        synthesize_analysis_result(store, job_id, generation=generation, **transport)
    phase("verify")
    job = store.require_running(job_id, generation=generation)
    verify_analysis_sources(store, job_id, job["result"], generation=generation)
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


def _analysis_message_snapshot(store, channel_id, message_id, maximum):
    payload = store.analysis_source(channel_id, message_id, max_bytes=maximum)
    if payload is None:
        return None, None
    payload["normalization_version"] = ANALYSIS_NORMALIZATION_VERSION
    encoded = canonical_json(payload)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("manifest_byte_cap")
    return payload, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def enqueue_fts_backfill(store: AnalysisStore, config: dict[str, Any], *, rebuild: bool, now: datetime):
    if not history_client._message_fts_enabled(store.conn):
        raise ValueError("fts_unavailable")
    policy = resolve_analysis_policy(config)["analysis"]
    try:
        batch = history_client.require_int_config_value(config, "sync", "batch_size", min_value=0)
    except SystemExit as exc:
        raise ValueError("Invalid sync.batch_size") from exc
    settings = {"schema_version": ANALYSIS_SCHEMA_VERSION, "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
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
            or settings.get("schema_version") != ANALYSIS_SCHEMA_VERSION
            or settings.get("algorithm_version") != ANALYSIS_ALGORITHM_VERSION):
        raise ValueError("Invalid internal index contract")


def compile_search_fts(terms):
    # FTS strings use doubled embedded quotes; model text never supplies grammar.
    return " OR ".join('"' + term.replace("\x00", " ").replace('"', '""') + '"'
        for term in terms if re.search(r"[^\W_]", term))


def _search_candidate_refs(store, job, scope, prepared):
    request, settings = job["request"]["request"], job["request"]["settings"]
    cap = min(settings["analysis"]["search"]["max_candidates"], settings["analysis"]["max_messages"])
    original, expanded = (compile_search_fts(prepared[key]) for key in ("original_terms", "expanded_terms"))
    expressions = [original, f"({expanded}) NOT ({original})" if expanded and original else expanded]
    refs, limited = [], False
    for expression in expressions:
        remaining = cap - len(refs)
        rows = store.search_candidates(expression, since=request["since"], until=min(request["until"], request["cutoff_at"]),
            ceilings=scope["message_ceilings"], limit=remaining + 1)
        refs.extend([[row["channel_id"], row["message_id"]] for row in rows[:remaining]])
        limited |= len(rows) > remaining
        if limited:
            break
    return refs, limited


def _search_context_refs(store, job, scope, candidate):
    settings, request = job["request"]["settings"], job["request"]["request"]
    cap = settings["analysis"]["search"]["context_messages"]
    if not cap:
        return [], False
    peer, identity = candidate
    maximum = settings["analysis"]["max_intermediate_bytes"]
    anchor = store.analysis_source(peer, identity, max_bytes=maximum)
    if anchor is None:
        return [], False
    upper, ceiling = min(request["until"], request["cutoff_at"]), scope["message_ceilings"][str(peer)]
    found, seen, source = [], {identity}, anchor
    while len(found) <= cap:
        parent = source["reply_to_message_id"]
        if parent is None or parent in seen or parent > ceiling:
            break
        source = store.analysis_source(peer, parent, max_bytes=maximum)
        if source is None or not request["since"] <= source["date_utc"] <= upper:
            break
        found.append([peer, parent])
        seen.add(parent)
    neighbors = [row for before in (True, False) for row in store.search_neighbors(peer,
        at=(anchor["date_utc"], identity), since=request["since"], until=upper,
        ceiling=ceiling, limit=cap + 1, before=before)]
    stamp = datetime.fromisoformat(anchor["date_utc"])
    neighbors.sort(key=lambda row: (abs((datetime.fromisoformat(row["date_utc"]) - stamp).total_seconds()),
                                    abs(row["message_id"] - identity), row["message_id"]))
    for row in neighbors:
        if row["message_id"] not in seen:
            found.append([peer, row["message_id"]])
            seen.add(row["message_id"])
    return found[:cap], len(found) > cap


def build_search_manifest(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    policy = settings["analysis"]
    manifest = job["checkpoint"].get("manifest")
    if manifest is None:
        history_client.require_fts_ready(store.conn)
        scope = _analysis_manifest_scope(store, job)
        prepared = {"original_terms": [], "expanded_terms": [], "expansion_status": "not_requested", "reasons": []}
        if scope["stored_in_window"]:
            prepared = prepare_search_query(store, job_id, generation=generation, **transport)
        refs, limited = _search_candidate_refs(store, job, scope, prepared)
        history_client.require_fts_ready(store.conn)
        manifest = {**scope, "selection_mode": "candidates", "candidate_refs": refs,
            "candidate_count": len(refs), "selected_candidates": 0, "context_messages": 0,
            "candidate_cursor": 0, "context_cursor": 0, "pending_context": [],
            "query_expansion_status": prepared["expansion_status"]}
        manifest["reasons"].extend(prepared["reasons"])
        if limited:
            manifest["reasons"].append("max_messages" if policy["max_messages"] < policy["search"]["max_candidates"] else "max_candidates")
        manifest["partial"] |= bool(manifest["reasons"])
        while True:
            try:
                current = store.require_running(job_id, generation=generation)
                store.save_checkpoint(job_id, {**current["checkpoint"], "manifest": manifest},
                    generation=generation, max_bytes=policy["max_intermediate_bytes"])
                break
            except BudgetExceededError as exc:
                if exc.reason != "max_intermediate_bytes" or not manifest["candidate_refs"]:
                    raise
                manifest["candidate_refs"] = manifest["candidate_refs"][:len(manifest["candidate_refs"]) // 2]
                manifest["candidate_count"] = len(manifest["candidate_refs"])
                manifest.update(partial=True, reasons=list(dict.fromkeys([*manifest["reasons"], "candidate_metadata_limited"])))
    if manifest.get("input_changed"):
        manifest = _repair_analysis_manifest(store, job_id, generation=generation)
        manifest["selected_candidates"] = sum(store.manifest_reference(job_id, *ref) is not None for ref in manifest["candidate_refs"])
        manifest["context_messages"] = manifest["selected"] - manifest["selected_candidates"]
        current = store.require_running(job_id, generation=generation)
        store.save_checkpoint(job_id, {**current["checkpoint"], "manifest": manifest}, generation=generation,
            max_bytes=policy["max_intermediate_bytes"])
    flush, upper = max(1, settings["sync"]["batch_size"]), min(request["until"], request["cutoff_at"])
    while manifest["state"] != "complete":
        store.require_running(job_id, generation=generation)
        next_state, inputs, pending = copy.deepcopy(manifest), [], set()
        try:
            while len(inputs) < flush and next_state["state"] != "complete":
                store.require_running(job_id, generation=generation)
                candidate = next_state["candidate_cursor"] < len(next_state["candidate_refs"])
                if candidate:
                    ref = next_state["candidate_refs"][next_state["candidate_cursor"]]
                    next_state["candidate_cursor"] += 1
                else:
                    if not next_state["pending_context"]:
                        if next_state["context_cursor"] == len(next_state["candidate_refs"]):
                            next_state["state"] = "complete"
                            break
                        context, limited = _search_context_refs(store, job, manifest,
                            next_state["candidate_refs"][next_state["context_cursor"]])
                        next_state["context_cursor"] += 1
                        next_state["pending_context"] = context
                        if limited:
                            next_state.update(partial=True, reasons=list(dict.fromkeys([*next_state["reasons"], "context_limited"])))
                        if not context:
                            continue
                    ref = next_state["pending_context"].pop(0)
                pair = tuple(ref)
                if pair in pending or store.manifest_reference(job_id, *pair) is not None:
                    continue
                if next_state["selected"] >= policy["max_messages"]:
                    next_state.update(state="complete", partial=True, reasons=list(dict.fromkeys([*next_state["reasons"], "max_messages"])))
                    break
                value, digest = _analysis_message_snapshot(store, *pair, policy["max_intermediate_bytes"])
                if value is None or not request["since"] <= value["date_utc"] <= upper:
                    next_state.update(partial=True, reasons=list(dict.fromkeys([*next_state["reasons"], "input_removed"])))
                    continue
                inputs.append({"channel_id": pair[0], "message_id": pair[1], "date_utc": value["date_utc"], "content_digest": digest})
                pending.add(pair)
                next_state["selected"] += 1
                next_state["selected_candidates" if candidate else "context_messages"] += 1
        except ValueError as exc:
            if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                raise
            return store.stop_manifest_at_byte_cap(job_id, generation=generation)
        next_state["page"] += 1
        next_state["known_skipped"] = max(0, next_state["stored_in_window"] - next_state["selected"])
        if next_state["state"] == "complete" and next_state["known_skipped"] == 0:
            next_state["reasons"] = [reason for reason in next_state["reasons"] if reason != "context_limited"]
            next_state["partial"] = bool(next_state["reasons"])
        try:
            store.commit_manifest_page(job_id, generation=generation, expected_page=manifest["page"], manifest=next_state, inputs=inputs)
        except ValueError as exc:
            if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                raise
            return store.stop_manifest_at_byte_cap(job_id, generation=generation)
        manifest = next_state
    return manifest


def _analysis_manifest_scope(store, job):
    history = job["checkpoint"].get("history")
    if history is None:
        raise ValueError("History must be prepared before selection")
    request = job["request"]["request"]
    channels, incomplete = [], bool(history.get("failed_refs"))
    for reference in request["channels"]:
        peer = history.get("refs", {}).get(reference)
        source = history.get("sources", {}).get(str(peer))
        if source is None or source.get("state") == "failed":
            incomplete = True
            continue
        if source.get("state") not in {"complete", "partial"}:
            raise ValueError("History selection cannot start during synchronization")
        incomplete = incomplete or source["state"] != "complete" or bool(source.get("pending_windows"))
        if peer not in channels:
            channels.append(peer)
    stats = {str(peer): store.conn.execute("""SELECT COUNT(*),COALESCE(MAX(message_id),0) FROM messages
        WHERE channel_id=? AND date_utc>=? AND date_utc<=?""",
        (peer, request["since"], min(request["until"], request["cutoff_at"]))).fetchone() for peer in channels}
    reasons = (["history_partial"] if incomplete else []) + (["ocr_partial"] if job["checkpoint"].get("ocr", {}).get("partial") else [])
    return {"state": "building", "channels": channels, "message_ceilings": {peer: row[1] for peer, row in stats.items()},
        "stored_in_window": sum(row[0] for row in stats.values()), "selected": 0, "known_skipped": 0,
        "partial": bool(reasons), "reasons": reasons, "page": 0}


def build_all_messages_manifest(store: AnalysisStore, job_id: str, *, generation: int):
    """Freeze paged IDs/digests before extraction; retain only one input page in RAM."""
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    policy = settings["analysis"]
    manifest = job["checkpoint"].get("manifest")
    if manifest is None:
        manifest = {**_analysis_manifest_scope(store, job), "selection_mode": "all_messages", "source_index": 0, "after": None}
        checkpoint = {**job["checkpoint"], "manifest": manifest}
        store.save_checkpoint(job_id, checkpoint, generation=generation, max_bytes=policy["max_intermediate_bytes"])
    if manifest.get("input_changed"):
        manifest = _repair_analysis_manifest(store, job_id, generation=generation)
    if manifest["state"] == "complete":
        return manifest
    upper = min(request["until"], request["cutoff_at"])
    flush = max(1, settings["sync"]["batch_size"])
    while manifest["state"] != "complete":
        store.require_running(job_id, generation=generation)
        next_state = copy.deepcopy(manifest)
        inputs = []
        if manifest["source_index"] >= len(manifest["channels"]):
            next_state["state"] = "complete"
        else:
            peer = manifest["channels"][manifest["source_index"]]
            remaining = policy["max_messages"] - manifest["selected"]
            rows = store.history_page(peer, since=request["since"], until=upper, after=manifest["after"],
                                      limit=min(flush, remaining + 1), max_message_id=manifest["message_ceilings"][str(peer)])
            for row in rows[:remaining]:
                try:
                    payload, digest = _analysis_message_snapshot(store, peer, row["message_id"], policy["max_intermediate_bytes"])
                except ValueError as exc:
                    if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                        raise
                    return store.stop_manifest_at_byte_cap(job_id, generation=generation)
                if payload is None or payload["date_utc"] != row["date_utc"]:
                    store.interrupt_changed_input(job_id, generation=generation,
                                                   source={"channel_id": peer, "message_id": row["message_id"]})
                    raise ValueError("input_changed")
                inputs.append({"channel_id": peer, "message_id": row["message_id"],
                               "date_utc": row["date_utc"], "content_digest": digest})
            next_state["selected"] += len(inputs)
            if inputs:
                last = inputs[-1]
                next_state["after"] = [last["date_utc"], last["message_id"]]
            if len(rows) > remaining:
                next_state.update(state="complete", partial=True,
                                  known_skipped=max(0, manifest["stored_in_window"] - next_state["selected"]))
                next_state["reasons"].append("max_messages")
            elif len(rows) < min(flush, remaining + 1):
                next_state.update(source_index=manifest["source_index"] + 1, after=None)
                if next_state["source_index"] == len(manifest["channels"]):
                    next_state["state"] = "complete"
        next_state["page"] += 1
        if next_state["state"] == "complete" and next_state["selected"] < next_state["stored_in_window"]:
            next_state["known_skipped"] = next_state["stored_in_window"] - next_state["selected"]
            next_state["partial"] = True
            if "max_messages" not in next_state["reasons"] and "input_removed" not in next_state["reasons"]:
                next_state["reasons"].append("input_removed")
        try:
            store.commit_manifest_page(job_id, generation=generation, expected_page=manifest["page"],
                                       manifest=next_state, inputs=inputs)
        except ValueError as exc:
            if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                raise
            return store.stop_manifest_at_byte_cap(job_id, generation=generation)
        manifest = next_state
    return manifest


def _repair_analysis_manifest(store, job_id, *, generation):
    job = store.require_running(job_id, generation=generation)
    request, settings = job["request"]["request"], job["request"]["settings"]
    manifest = job["checkpoint"]["manifest"]
    upper = min(request["until"], request["cutoff_at"])
    after = manifest.get("repair_after_step", "")
    while True:
        store.require_running(job_id, generation=generation)
        batch = store.next_manifest_batch(job_id, after=after)
        if batch is None:
            break
        inputs = []
        for source in store.manifest_batch_inputs(batch["batch_id"]):
            payload, digest = _analysis_message_snapshot(store, source["channel_id"], source["message_id"],
                                                         settings["analysis"]["max_intermediate_bytes"])
            if payload is not None and request["since"] <= payload["date_utc"] <= upper:
                inputs.append({**source, "date_utc": payload["date_utc"], "content_digest": digest})
        inputs.sort(key=lambda item: (item["date_utc"], item["message_id"]))
        store.repair_manifest_batch(job_id, generation=generation, batch=batch, inputs=inputs)
        after = batch["step_key"]
    job = store.require_running(job_id, generation=generation)
    manifest = job["checkpoint"]["manifest"]
    manifest.pop("input_changed", None)
    manifest.pop("repair_after_step", None)
    store.save_checkpoint(job_id, job["checkpoint"], generation=generation,
                          max_bytes=settings["analysis"]["max_intermediate_bytes"])
    return manifest


def iter_analysis_manifest_messages(store: AnalysisStore, job_id: str, *, generation: int):
    job = store.require_running(job_id, generation=generation)
    manifest = job["checkpoint"].get("manifest", {})
    if manifest.get("state") != "complete" or manifest.get("input_changed"):
        raise ValueError("Input manifest is not ready for extraction")
    settings = job["request"]["settings"]
    for peer in manifest["channels"]:
        after = None
        while True:
            store.require_running(job_id, generation=generation)
            refs = store.manifest_inputs_page(job_id, channel_id=peer, after=after,
                                              limit=max(1, settings["sync"]["batch_size"]))
            if not refs:
                break
            for row in refs:
                store.require_running(job_id, generation=generation)
                source = dict(row)
                try:
                    payload, digest = _analysis_message_snapshot(store, source["channel_id"], source["message_id"],
                                                                 settings["analysis"]["max_intermediate_bytes"])
                except ValueError:
                    payload, digest = None, None
                if digest != source["content_digest"]:
                    store.interrupt_changed_input(job_id, generation=generation, source=source)
                    raise ValueError("input_changed")
                yield {"message": payload, "input": source}
            last = refs[-1]
            after = (last["date_utc"], last["message_id"])


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
