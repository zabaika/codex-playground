import importlib.util
from pathlib import Path
import sys
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
            min_related_links=5,
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
        self.assertIn("closing.too-few-related-links", codes)
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

    def test_clean_note_passes_full_contract(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "clean-general-note.md"
        violations = collect_violations(
            fixture,
            expect="source",
            min_related_links=5,
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
                "Грейд определяется зоной ответственности",
            ],
        )
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
