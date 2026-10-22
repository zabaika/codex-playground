"""Pure HTML and Markdown views of prepared analytics results."""

import html
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_shared.bot_api import split_text_chunks


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
