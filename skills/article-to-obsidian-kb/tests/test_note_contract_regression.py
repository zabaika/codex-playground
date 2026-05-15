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
        self.assertIn("language.translate-phrase:product lead", codes)
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

    def test_canonical_schema_heading_does_not_trigger_latin_residue(self) -> None:
        content = f"""---
title: Тест канонического заголовка
type: concept
tags:
  - prompts
---
Короткое определение на русском языке.
{ADDITIONAL_INSIGHTS_HEADING}
- 2026-05-12: русскоязычная запись без англоязычного prose.
{RELATED_NOTES_HEADING}
[[Русская заметка]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест канонического заголовка.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="concept",
                chronology_headings=[ADDITIONAL_INSIGHTS_HEADING],
            )
        codes = {violation.code for violation in violations}
        self.assertFalse(
            any(code.startswith("language.unexpected-latin:Additional") for code in codes)
        )
        self.assertFalse(
            any(code.startswith("language.unexpected-latin:insights") for code in codes)
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

    def test_canonical_engineering_terms_and_named_entities_pass(self) -> None:
        content = f"""---
title: Тест канонических английских терминов
source:
  - https://example.com
type: operating-model
tags:
  - ai-adoption
date: 2026
---
Команда использует `code review`, держит путь в `production`, хранит `playbooks` и строит отдельный [[LLMOps]]-контур. На уровне ролей рядом работают `Product Manager` и `product engineer`, а практики описаны на панели `Microsoft`, `Atlassian` и `1Password`.
{KEY_THESES_HEADING}
- **Тезис.** Канонические инженерные термины, устойчивые названия ролей и названия компаний могут оставаться на английском.
{PRACTICE_HEADING}
- **Практика.** Не переводите устойчивые названия процессов и ролей только потому, что они написаны латиницей.
{PITFALLS_HEADING}
- **Ошибка.** Чистить `Prompt Hardening` или `LLMOps` как будто это случайный англоязычный хвост.
{RELATED_NOTES_HEADING}
[[Prompt Hardening]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест канонических английских терминов.md"
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
        self.assertFalse(
            any(code.startswith("language.translate-term:") for code in codes)
        )

    def test_discouraged_english_prose_terms_fail_with_translate_term_code(self) -> None:
        content = f"""---
title: Тест переводимых английских ярлыков
source:
  - https://example.com
type: general
tags:
  - organization
date: 2026
---
Заметка намеренно оставляет фразы AI literacy, top-down, shadow AI, subjective satisfaction и delivery.
{KEY_THESES_HEADING}
- **Тезис.** Эти ярлыки должны быть переведены, даже если встречаются в источнике.
{PRACTICE_HEADING}
- **Практика.** Держите канонические термины и переводимые ярлыки как разные классы.
{PITFALLS_HEADING}
- **Ошибка.** Смешивать названия компаний и одноразовый организационный жаргон в одну категорию исключений.
{RELATED_NOTES_HEADING}
[[Социотехническая продуктивность]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест переводимых английских ярлыков.md"
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
        self.assertIn("language.translate-term:AI literacy", codes)
        self.assertIn("language.translate-term:top-down", codes)
        self.assertIn("language.translate-term:shadow AI", codes)
        self.assertIn("language.translate-term:subjective satisfaction", codes)
        self.assertIn("language.translate-term:delivery", codes)

    def test_named_entities_do_not_trigger_latin_residue_in_rollout_like_note(self) -> None:
        content = f"""---
title: Тест rollout note
source:
  - https://example.com
type: operating-model
tags:
  - ai-adoption
date: 2026
---
Аналитики Accenture и RedMonk описывают общий сдвиг в инженерных организациях.
## Команда и зона ответственности
- **Новые роли.** В найме растет доля ролей уровня `AI Engineer`, где основной акцент не на обучении foundation models, а на построении связующего слоя между `LLM` и боевым кодом.
## Платформы и системы
- **Prompt как часть системы.** Разовые личные prompts не масштабируются.
## Метрики
- **Подход.** Важны не только затраты, но и то, как меняется качество решений.
## Приоритизация
- **Выбор.** Команда определяет, какие сценарии стоит стандартизировать.
## Внедрение AI
- **Этапы.** Внедрение идет по частям.
## Покупать или строить
- **Решение.** Нужен отдельный слой интеграции.
## Key lessons
1. **Вывод.** Формулировка section title пока не переведена.
{RELATED_NOTES_HEADING}
[[Prompt Hardening]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест rollout note.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="source",
            )
        codes = {violation.code for violation in violations}
        self.assertNotIn("language.unexpected-latin:Accenture", codes)
        self.assertNotIn("language.unexpected-latin:RedMonk", codes)
        self.assertIn("language.translate-term:foundation models", codes)
        self.assertIn("language.translate-phrase:Key lessons", codes)

    def test_phrase_candidates_collapse_legacy_token_soup(self) -> None:
        content = f"""---
title: Тест phrase candidates
source:
  - https://example.com
type: general
tags:
  - organization
date: 2026
---
Короткое вступление про внедрение.
{KEY_THESES_HEADING}
- **Тезис.** Команда быстро получает root-cause analysis и живет в messy middle.
- **Тезис.** Community of practice и feedback loop не должны оставаться английским prose.
{PRACTICE_HEADING}
- **Практика.** Не оставляйте acceptable use и learning system в исходной форме.
{PITFALLS_HEADING}
- **Ошибка.** usage dashboards и monthly demos не должны разъезжаться в россыпь одиночных токенов.
{RELATED_NOTES_HEADING}
[[Социотехническая продуктивность]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест phrase candidates.md"
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
        self.assertIn("language.translate-term:root-cause analysis", codes)
        self.assertIn("language.translate-term:messy middle", codes)
        self.assertIn("language.translate-term:feedback loop", codes)
        self.assertIn("language.translate-term:acceptable use", codes)
        self.assertIn("language.translate-term:learning system", codes)
        self.assertIn("language.translate-term:usage dashboards", codes)
        self.assertIn("language.translate-phrase:Community of practice", codes)
        self.assertIn("language.translate-phrase:monthly demos", codes)
        self.assertNotIn("language.unexpected-latin:Community", codes)
        self.assertNotIn("language.unexpected-latin:of", codes)
        self.assertNotIn("language.unexpected-latin:practice", codes)

    def test_discouraged_terms_inside_inline_code_are_still_checked(self) -> None:
        content = f"""---
title: Тест inline code prose
source:
  - https://example.com
type: general
tags:
  - ai-adoption
date: 2026
---
Короткое вступление.
{KEY_THESES_HEADING}
- **Тезис.** Команда делегирует `root-cause analysis` агенту и потом оценивает `usage dashboards`.
{PRACTICE_HEADING}
- **Практика.** Не прячьте `prompt` и `evaluation` в inline code, если это обычный prose.
{PITFALLS_HEADING}
- **Ошибка.** Inline code не должен обходить language-pass для переводимых ярлыков.
{RELATED_NOTES_HEADING}
[[Социотехническая продуктивность]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест inline code prose.md"
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
        self.assertIn("language.translate-term:root-cause analysis", codes)
        self.assertIn("language.translate-term:usage dashboards", codes)
        self.assertIn("language.translate-term:prompt", codes)
        self.assertIn("language.translate-term:evaluation", codes)

    def test_uncatalogued_descriptive_inline_phrases_are_reported(self) -> None:
        content = f"""---
title: Тест inline code phrase heuristics
source:
  - https://example.com
type: general
tags:
  - ai-adoption
date: 2026
---
Короткое вступление.
{KEY_THESES_HEADING}
- **Тезис.** Команда обсуждает `model routing`, `token budgets`, `usage-contingent pricing` и `inference costs`.
{PRACTICE_HEADING}
- **Практика.** Такие описательные операционные фразы не должны сохраняться на английском только потому, что они стоят в inline code.
{PITFALLS_HEADING}
- **Ошибка.** Не прячьте descriptive labels в backticks.
{RELATED_NOTES_HEADING}
[[Социотехническая продуктивность]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест inline code phrase heuristics.md"
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
        self.assertIn("language.translate-phrase:model routing", codes)
        self.assertIn("language.translate-phrase:token budgets", codes)
        self.assertIn("language.translate-phrase:usage-contingent pricing", codes)
        self.assertIn("language.translate-phrase:inference costs", codes)

    def test_mixed_case_and_hyphenated_residue_are_reported_more_helpfully(self) -> None:
        content = f"""---
title: Тест mixed residue
source:
  - https://example.com
type: operating-model
tags:
  - ai-adoption
date: 2026
---
Короткое вступление с DevProd, PoC и hub-and-spoke.
{KEY_THESES_HEADING}
- **Тезис.** Такие остатки должны давать более точные diagnostics, чем общий unexpected-latin.
{PRACTICE_HEADING}
- **Практика.** Сначала проверяйте, является ли токен shorthand, acronym или phrase-like остатком.
{PITFALLS_HEADING}
- **Ошибка.** Смешивать внутренние labels и обычный prose residue в один bucket.
{RELATED_NOTES_HEADING}
[[AI Rollout Operating Model - Engineering Organizations]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Тест mixed residue.md"
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
        self.assertIn("language.review-term:DevProd", codes)
        self.assertIn("language.review-term:PoC", codes)
        self.assertIn("language.translate-phrase:hub-and-spoke", codes)
        self.assertNotIn("language.unexpected-latin:DevProd", codes)
        self.assertNotIn("language.unexpected-latin:PoC", codes)
        self.assertNotIn("language.unexpected-latin:hub-and-spoke", codes)

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
