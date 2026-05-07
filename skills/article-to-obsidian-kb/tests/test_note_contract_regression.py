import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "check_note_contract.py"
)
SPEC = importlib.util.spec_from_file_location("check_note_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
collect_violations = MODULE.collect_violations
RELATED_NOTES_HEADING = MODULE.RELATED_NOTES_HEADING
ADDITIONAL_INSIGHTS_HEADING = MODULE.ADDITIONAL_INSIGHTS_HEADING
EVIDENCE_HEADING = MODULE.EVIDENCE_HEADING
KEY_THESES_HEADING = MODULE.heading("key_theses")
PRACTICE_HEADING = MODULE.heading("practice")
PITFALLS_HEADING = MODULE.heading("pitfalls")


class CheckNoteContractTests(unittest.TestCase):
    def test_second_pass_catches_leftovers_from_first_pass(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "after-first-pass-regression.md"
        )
        violations = collect_violations(
            fixture,
            expect="source",
            forbidden_terms=["good enough", "builder-режим"],
            allow_latin_terms=["AI", "CPO"],
            required_linked_phrases=["Грейды всё сильнее определяются ответственностью"],
            required_example_phrases=["салона, химчистки или шиномонтажа"],
            required_headings=[
                KEY_THESES_HEADING,
                PRACTICE_HEADING,
                PITFALLS_HEADING,
            ],
            forbidden_headings=["## Суть"],
            enforce_leading_bold_under=[
                KEY_THESES_HEADING,
                PRACTICE_HEADING,
                PITFALLS_HEADING,
            ],
            required_related_links=["Найм с AI-усилением"],
        )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.missing-date", codes)
        self.assertIn(
            "links.unlinked-phrase:Грейды всё сильнее определяются ответственностью",
            codes,
        )
        self.assertIn("language.forbidden-term:good enough", codes)
        self.assertIn("language.forbidden-term:builder-режим", codes)
        self.assertIn("spacing.blank-line-after-heading", codes)
        self.assertIn("spacing.blank-line-before-list", codes)
        self.assertIn(f"structure.missing-heading:{PITFALLS_HEADING}", codes)
        self.assertIn("examples.missing:салона, химчистки или шиномонтажа", codes)
        self.assertIn(f"emphasis.missing-leading-bold:{KEY_THESES_HEADING}", codes)
        self.assertIn("language.unexpected-latin:product", codes)
        self.assertIn("language.unexpected-latin:lead", codes)
        self.assertIn("closing.duplicate-inline-link:Найм с AI-усилением", codes)

    def test_empty_related_section_is_rejected(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "empty-related-section.md"
        )
        violations = collect_violations(
            fixture,
            expect="source",
            allow_latin_terms=["AI", "OKR"],
            required_headings=[
                KEY_THESES_HEADING,
                PRACTICE_HEADING,
                PITFALLS_HEADING,
            ],
            enforce_leading_bold_under=[
                KEY_THESES_HEADING,
                PRACTICE_HEADING,
                PITFALLS_HEADING,
            ],
        )
        codes = {violation.code for violation in violations}
        self.assertIn("closing.empty-related-section", codes)

    def test_related_section_must_not_repeat_inline_wikilinks(self) -> None:
        content = f"""---
title: Test related dedup
source:
  - https://example.com
type: general
tags:
  - metrics
date: 2026
---
Короткая заметка со ссылкой на [[DX Core 4]] прямо в теле.
{KEY_THESES_HEADING}
- **Тезис.** Связь с [[Human-equivalent hours]] уже дана inline.
{PRACTICE_HEADING}
- **Практика.** Дедуплицируйте closing section после вставки inline wikilinks.
## Подводные камки и антипаттерны
- **Ошибка.** Механически дублировать те же ссылки в closing block.
{RELATED_NOTES_HEADING}
[[DX Core 4]]
[[Human-equivalent hours]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test related dedup.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
                required_headings=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    "## Подводные камки и антипаттерны",
                ],
                enforce_leading_bold_under=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    "## Подводные камки и антипаттерны",
                ],
                allow_latin_terms=["DX"],
            )
        codes = {violation.code for violation in violations}
        self.assertIn("closing.duplicate-inline-link:DX Core 4", codes)
        self.assertIn("closing.duplicate-inline-link:Human-equivalent hours", codes)

    def test_wikilinks_with_english_titles_do_not_trigger_latin_residue(self) -> None:
        content = f"""---
title: Test english wikilinks
source:
  - https://example.com
type: general
tags:
  - ai-tools
date: 2026
---
Короткая заметка со ссылками на [[Prompt Hardening]] и [[AI Rollout Operating Model - Engineering Organizations]].
{KEY_THESES_HEADING}
- **Тезис.** Встроенные `wikilinks` с английскими названиями допустимы.
{PRACTICE_HEADING}
- **Практика.** Не русифицируйте названия связанных заметок ради языковой чистки.
{PITFALLS_HEADING}
- **Ошибка.** Чистить англицизмы внутри канонических `wikilinks`.
{RELATED_NOTES_HEADING}
[[Личный AI operating system - Telegram, Obsidian и агент на VPS]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test english wikilinks.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
                required_headings=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    PITFALLS_HEADING,
                ],
                enforce_leading_bold_under=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    PITFALLS_HEADING,
                ],
            )
        codes = {violation.code for violation in violations}
        self.assertFalse(
            any(code.startswith("language.unexpected-latin:") for code in codes)
        )

    def test_clean_note_passes_full_contract(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "clean-general-note.md"
        violations = collect_violations(
            fixture,
            expect="source",
            forbidden_terms=["good enough", "builder-режим"],
            allow_latin_terms=[
                "AI",
                "CPO",
                "JTBD",
                "PRD",
                "CSV",
                "Airtable",
            ],
            required_linked_phrases=["Грейд всё сильнее определяется зоной ответственности"],
            required_example_phrases=["салона, химчистки или шиномонтажа"],
            required_headings=[
                KEY_THESES_HEADING,
                PRACTICE_HEADING,
                PITFALLS_HEADING,
            ],
            forbidden_headings=["## Суть"],
            enforce_leading_bold_under=[
                KEY_THESES_HEADING,
                PRACTICE_HEADING,
                PITFALLS_HEADING,
            ],
            required_related_links=[
                "Найм с AI-усилением",
                "Оркестрация мультиагентных систем",
            ],
        )
        self.assertEqual([], violations)

    def test_prepended_dated_log_entry_is_rejected(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "dated-log-prepend-regression.md"
        )
        violations = collect_violations(
            fixture,
            expect="concept",
            chronology_headings=[ADDITIONAL_INSIGHTS_HEADING],
            allow_latin_terms=ADDITIONAL_INSIGHTS_HEADING.removeprefix("## ").split() + ["AI", "DX", "DevEx"],
        )
        codes = {violation.code for violation in violations}
        self.assertIn(f"chronology.out-of-order:{ADDITIONAL_INSIGHTS_HEADING}", codes)

    def test_clean_dated_log_order_passes(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "dated-log-clean.md"
        violations = collect_violations(
            fixture,
            expect="concept",
            chronology_headings=[ADDITIONAL_INSIGHTS_HEADING],
            allow_latin_terms=ADDITIONAL_INSIGHTS_HEADING.removeprefix("## ").split() + ["AI", "DX", "DevEx"],
        )
        self.assertEqual([], violations)

    def test_multisource_evidence_requires_dated_bullets(self) -> None:
        content = f"""---
title: Multi-source evidence regression
source:
  - https://example.com/a
  - https://example.com/b
type: operating-model
tags:
  - infrastructure
date: 2026
---
Короткое вступление.
{KEY_THESES_HEADING}
- **Тезис.** Полезный вывод.
{PRACTICE_HEADING}
- **Практика.** Полезное действие.
{EVIDENCE_HEADING}
- Первый источник без даты.
- 2026-05: Второй источник с датой.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Multi-source evidence regression.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
                required_headings=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    EVIDENCE_HEADING,
                ],
                allow_latin_terms=EVIDENCE_HEADING.removeprefix("## ").split(),
            )
        codes = {violation.code for violation in violations}
        self.assertIn("chronology.multisource-evidence-missing-date", codes)

    def test_multisource_evidence_with_dated_bullets_passes(self) -> None:
        content = f"""---
title: Multi-source evidence clean
source:
  - https://example.com/a
  - https://example.com/b
type: operating-model
tags:
  - infrastructure
date: 2026
---
Короткое вступление.
{KEY_THESES_HEADING}
- **Тезис.** Полезный вывод.
{PRACTICE_HEADING}
- **Практика.** Полезное действие.
{EVIDENCE_HEADING}
- 2024-10-03: Первый источник.
- 2026-05: Второй источник.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Multi-source evidence clean.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
                required_headings=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    EVIDENCE_HEADING,
                ],
                allow_latin_terms=EVIDENCE_HEADING.removeprefix("## ").split(),
            )
        self.assertEqual([], violations)

    def test_multisource_frontmatter_date_must_match_newest_evidence_year(self) -> None:
        content = f"""---
title: Multi-source date mismatch
source:
  - https://example.com/a
  - https://example.com/b
type: operating-model
tags:
  - infrastructure
date: 2024
---
Короткое вступление.
{KEY_THESES_HEADING}
- **Тезис.** Полезный вывод.
{PRACTICE_HEADING}
- **Практика.** Полезное действие.
{EVIDENCE_HEADING}
- 2024-10-03: Первый источник.
- 2025-03-26: Второй источник.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Multi-source date mismatch.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
                required_headings=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    EVIDENCE_HEADING,
                ],
                allow_latin_terms=EVIDENCE_HEADING.removeprefix("## ").split(),
            )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.multisource-date-mismatch", codes)

    def test_multisource_frontmatter_date_matches_newest_evidence_year(self) -> None:
        content = f"""---
title: Multi-source date clean
source:
  - https://example.com/a
  - https://example.com/b
type: operating-model
tags:
  - infrastructure
date: 2025
---
Короткое вступление.
{KEY_THESES_HEADING}
- **Тезис.** Полезный вывод.
{PRACTICE_HEADING}
- **Практика.** Полезное действие.
{EVIDENCE_HEADING}
- 2024-10-03: Первый источник.
- 2025-03-26: Второй источник.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Multi-source date clean.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
                required_headings=[
                    KEY_THESES_HEADING,
                    PRACTICE_HEADING,
                    EVIDENCE_HEADING,
                ],
                allow_latin_terms=EVIDENCE_HEADING.removeprefix("## ").split(),
            )
        self.assertEqual([], violations)

    def test_management_tag_is_rejected(self) -> None:
        content = f"""---
title: Test concept
type: concept
tags:
  - management
---
Тестовая заметка.
{ADDITIONAL_INSIGHTS_HEADING}
- 2026-04-23: Наблюдение.
{RELATED_NOTES_HEADING}
[[Test link]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test concept.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="concept",
                chronology_headings=[ADDITIONAL_INSIGHTS_HEADING],
                allow_latin_terms=ADDITIONAL_INSIGHTS_HEADING.removeprefix("## ").split(),
            )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.forbidden-tag:management", codes)

    def test_ai_tag_is_rejected(self) -> None:
        content = f"""---
title: Test ai concept
type: concept
tags:
  - ai
---
Тестовая заметка.
{ADDITIONAL_INSIGHTS_HEADING}
- 2026-04-23: Наблюдение.
{RELATED_NOTES_HEADING}
[[Test link]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test ai concept.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="concept",
                chronology_headings=[ADDITIONAL_INSIGHTS_HEADING],
                allow_latin_terms=ADDITIONAL_INSIGHTS_HEADING.removeprefix("## ").split(),
            )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.forbidden-tag:ai", codes)

    def test_more_than_three_tags_is_rejected(self) -> None:
        content = f"""---
title: Test tag count concept
type: concept
tags:
  - metrics
  - developer-productivity
  - developer-experience
  - business-impact
---
Тестовая заметка.
{ADDITIONAL_INSIGHTS_HEADING}
- 2026-04-23: Наблюдение.
{RELATED_NOTES_HEADING}
[[Test link]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test tag count concept.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="concept",
                chronology_headings=[ADDITIONAL_INSIGHTS_HEADING],
                allow_latin_terms=ADDITIONAL_INSIGHTS_HEADING.removeprefix("## ").split(),
            )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.invalid-tag-count", codes)


if __name__ == "__main__":
    unittest.main()
