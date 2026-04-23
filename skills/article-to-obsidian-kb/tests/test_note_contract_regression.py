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
                "## Ключевые тезисы",
                "## Практика",
                "## Подводные камни и антипаттерны",
            ],
            forbidden_headings=["## Суть"],
            enforce_leading_bold_under=[
                "## Ключевые тезисы",
                "## Практика",
                "## Подводные камни и антипаттерны",
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
        self.assertIn(
            "structure.missing-heading:## Подводные камни и антипаттерны", codes
        )
        self.assertIn("examples.missing:салона, химчистки или шиномонтажа", codes)
        self.assertIn(
            "emphasis.missing-leading-bold:## Ключевые тезисы",
            codes,
        )
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
                "## Ключевые тезисы",
                "## Практика",
                "## Подводные камни и антипаттерны",
            ],
            enforce_leading_bold_under=[
                "## Ключевые тезисы",
                "## Практика",
                "## Подводные камни и антипаттерны",
            ],
        )
        codes = {violation.code for violation in violations}
        self.assertIn("closing.empty-related-section", codes)

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
                "## Ключевые тезисы",
                "## Практика",
                "## Подводные камни и антипаттерны",
            ],
            forbidden_headings=["## Суть"],
            enforce_leading_bold_under=[
                "## Ключевые тезисы",
                "## Практика",
                "## Подводные камни и антипаттерны",
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
            chronology_headings=["## Additional insights"],
            allow_latin_terms=["Additional", "insights", "AI", "DX", "DevEx"],
        )
        codes = {violation.code for violation in violations}
        self.assertIn("chronology.out-of-order:## Additional insights", codes)

    def test_clean_dated_log_order_passes(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "dated-log-clean.md"
        violations = collect_violations(
            fixture,
            expect="concept",
            chronology_headings=["## Additional insights"],
            allow_latin_terms=["Additional", "insights", "AI", "DX", "DevEx"],
        )
        self.assertEqual([], violations)

    def test_management_tag_is_rejected(self) -> None:
        content = """---
title: Test concept
type: concept
tags:
  - management
---
Тестовая заметка.
## Additional insights
- 2026-04-23: Наблюдение.
# Связанные заметки
[[Test link]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test concept.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="concept",
                chronology_headings=["## Additional insights"],
                allow_latin_terms=["Additional", "insights"],
            )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.forbidden-tag:management", codes)

    def test_ai_tag_is_rejected(self) -> None:
        content = """---
title: Test ai concept
type: concept
tags:
  - ai
---
Тестовая заметка.
## Additional insights
- 2026-04-23: Наблюдение.
# Связанные заметки
[[Test link]]
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = Path(tmpdir) / "Test ai concept.md"
            fixture.write_text(content, encoding="utf-8")
            violations = collect_violations(
                fixture,
                expect="concept",
                chronology_headings=["## Additional insights"],
                allow_latin_terms=["Additional", "insights"],
            )
        codes = {violation.code for violation in violations}
        self.assertIn("frontmatter.forbidden-tag:ai", codes)


if __name__ == "__main__":
    unittest.main()
