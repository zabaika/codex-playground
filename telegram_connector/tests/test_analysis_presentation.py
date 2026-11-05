import copy
import re
import tomllib
from pathlib import Path

import pytest

from telegram_connector import analysis_rendering as rendering


SEPARATOR = "───────────────────"


def presentation(command="topics", *, items=2, size=4000):
    config = tomllib.loads((Path(__file__).resolve().parents[1] /
                           "config/runtime.example.toml").read_text())
    job = {"command": command, "request": {"settings": {
        "analysis": config["analysis"], "bridge": {"text_chunk_size": size}},
        "request": {"channels": ["@fixture"], "since": "2026-10-01",
                    "until": "2026-11-01", "cutoff_at": "2026-11-01",
                    "detail": "normal", "query": None}}}
    item = {"title": "Факт", "text": "Подробность.", "conditions": ["Условие"],
            "exceptions": [], "contradictions": [], "sources": [
                {"channel_id": 1, "message_id": i, "url": f"https://t.me/fixture/{i}",
                 "date_utc": "2026-10-01"} for i in range(1, 4)]}
    result = {"items": [copy.deepcopy(item) for _ in range(items)],
              "summary": "Резюме", "partial": False,
              "insufficient_information": False, "limitations": ["Оговорка"]}
    return job, result


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
@pytest.mark.parametrize("items", [0, 1, 2])
def test_each_complete_item_ends_with_one_separator(command, items):
    job, result = presentation(command, items=items)
    chunks = rendering._analysis_html_chunks(job, result)
    html = "\n\n".join(chunks)
    assert html.count(SEPARATOR) == items + bool(items)
    for chunk in chunks:
        assert not chunk.startswith(SEPARATOR)
        assert chunk.strip() != SEPARATOR
    for number in range(1, 4):
        anchor = f'<a href="https://t.me/fixture/{number}">{number} источник</a>'
        assert html.count(anchor) == items
    if items:
        assert html.rindex(SEPARATOR) < html.index("Оговорка")
        assert f'3 источник</a>\n\n{SEPARATOR}' in html
    markdown = rendering._full_analysis_markdown(job, result)
    assert markdown.count(SEPARATOR) == items


def test_long_item_keeps_separator_with_last_fragment_and_escapes_text():
    job, result = presentation(items=1, size=180)
    result["items"][0]["text"] = "Текст <опасный> & подробности. " * 100
    chunks = rendering._analysis_html_chunks(job, result)
    assert len(chunks) > 1
    assert all(rendering._telegram_units(chunk) <= 180 for chunk in chunks)
    assert all(not chunk.startswith(SEPARATOR) and chunk.strip() != SEPARATOR
               for chunk in chunks)
    assert all("<опасный>" not in chunk for chunk in chunks)
    joined = "\n\n".join(chunks)
    assert joined.count(SEPARATOR) == 2
    assert re.search(r'3 источник</a>\n\n' + SEPARATOR, joined)


@pytest.mark.parametrize("command,heading", [
    ("topics", "Главные темы"), ("search", "Результат поиска"),
    ("qa", "Практические вопросы и ответы"),
])
def test_channel_period_count_and_partial_status_precede_content(command, heading):
    job, result = presentation(command)
    result.update(channel_titles=["Налоги 🇪🇸 <Spain>", "Digital Nomad Visa Spain"],
                  coverage={"selected_messages": 95, "processed_messages": 90},
                  partial=True, reasons=["merge_no_progress", "relations_incomplete"])
    first = rendering._analysis_html_chunks(job, result)[0]
    expected = ("<b>Налоги 🇪🇸 &lt;Spain&gt;, Digital Nomad Visa Spain</b>\n\n"
                "Период UTC: 2026-10-01 .. 2026-11-01\nСообщений в анализе: 95\n\n"
                "<b>Частичный результат</b>\nПричины: merge_no_progress, relations_incomplete\n\n"
                f"<b>{heading}</b>")
    assert first.startswith(expected)
    assert "&lt;Spain&gt;" in first and "<Spain>" not in first
    assert sum(chunk.count("Период UTC:") for chunk in rendering._analysis_html_chunks(job, result)) == 1


def test_complete_header_uses_requested_identifiers_when_titles_unavailable():
    job, result = presentation(items=0)
    first = rendering._analysis_html_chunks(job, result)[0]
    assert first.startswith("<b>@fixture</b>\n\nПериод UTC:")
    assert "Сообщений в анализе: 0\n\n<b>Главные темы</b>" in first
    assert "Частичный результат" not in first


def test_long_partial_status_still_obeys_telegram_part_limit():
    job, result = presentation(items=0, size=180)
    result.update(partial=True, reasons=["очень_длинная_причина" * 30])
    chunks = rendering._analysis_html_chunks(job, result)
    assert all(rendering._telegram_units(chunk) <= 180 for chunk in chunks)
    assert "Главные темы" in "\n".join(chunks)


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
def test_item_and_section_headings_use_one_newline_before_their_content(command):
    job, result = presentation(command, items=1)
    result["items"][0]["exceptions"] = ["Исключение"]
    result["items"][0]["contradictions"] = ["Противоречие"]
    text = "\n\n".join(rendering._analysis_html_chunks(job, result))
    title, answer_label = rendering._analysis_item_labels(job, result["items"][0])
    if answer_label:
        assert f"<b>{title}</b>\n<b>{answer_label}</b>\nПодробность." in text
    else:
        assert f"<b>{title}</b>\nПодробность." in text
    for heading, content in (("Условия", "Условие"), ("Исключения", "Исключение"),
                             ("Противоречия", "Противоречие"), ("Источники", '<a href="')):
        assert f"<b>{heading}</b>\n{content}" in text
        assert f"<b>{heading}</b>\n\n" not in text
    assert f"3 источник</a>\n\n{SEPARATOR}" in text


def test_conditions_and_sources_have_single_newlines_inside_each_block():
    job, result = presentation(items=1)
    result["items"][0]["conditions"] = ["Первое условие", "Второе условие", "Третье условие"]
    text = "\n\n".join(rendering._analysis_html_chunks(job, result))
    assert "<b>Условия</b>\nПервое условие\nВторое условие\nТретье условие\n\n<b>Источники</b>" in text
    anchors = [f'<a href="https://t.me/fixture/{n}">{n} источник</a>' for n in range(1, 4)]
    assert "<b>Источники</b>\n" + "\n".join(anchors) + "\n\n" + SEPARATOR in text


@pytest.mark.parametrize("summary,items", [("Саммари", 1), ("", 1), ("Саммари", 0)])
def test_summary_separator_appears_only_before_existing_items(summary, items):
    job, result = presentation(items=items)
    result["summary"] = summary
    text = "\n\n".join(rendering._analysis_html_chunks(job, result))
    assert text.count(SEPARATOR) == items + bool(summary and items)
    if summary and items:
        assert f"{summary}\n\n{SEPARATOR}\n\n<b>Факт</b>\nПодробность." in text
