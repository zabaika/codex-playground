from __future__ import annotations

import unittest

from job_search.infrastructure.board_adapters.generic_vacancy_text_adapter import GenericVacancyTextAdapter


class GenericVacancyTextAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = GenericVacancyTextAdapter()

    def test_extracts_labeled_generic_vacancy_blocks(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Title: Head of Platform",
                    "Company: Example Cloud",
                    "Location: Remote Europe",
                    "URL: https://jobs.example.com/platform?utm_source=mail",
                    "Build internal developer platforms.",
                ]
            ),
            source_origin="manual_text",
        )

        self.assertEqual(len(extraction.items), 1)
        item = extraction.items[0]
        self.assertEqual(item.title, "Head of Platform")
        self.assertEqual(item.company_name, "Example Cloud")
        self.assertEqual(item.location_text, "Remote Europe")
        self.assertEqual(item.source_url, "https://jobs.example.com/platform?utm_source=mail")
        self.assertIn("source_origin=manual_text", item.raw_text)

    def test_extracts_simple_copied_vacancy_block_without_platform_specific_rules(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Engineering Director",
                    "Acme AI · Hybrid Berlin",
                    "https://acme.example/jobs/123",
                    "Lead engineering managers and platform teams.",
                ]
            ),
            source_origin="copied_page",
        )

        self.assertEqual(len(extraction.items), 1)
        self.assertEqual(extraction.items[0].title, "Engineering Director")
        self.assertEqual(extraction.items[0].company_name, "Acme AI")
        self.assertEqual(extraction.items[0].location_text, "Hybrid Berlin")

    def test_url_only_input_returns_warning_without_silent_import(self) -> None:
        extraction = self.adapter.extract_from_text("https://jobs.example.com/123", source_origin="manual_url")

        self.assertEqual(extraction.items, [])
        self.assertIn("block_1: vacancy text requires title and company before import", extraction.warnings)


if __name__ == "__main__":
    unittest.main()
