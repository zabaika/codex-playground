from __future__ import annotations

import unittest

from job_search.application.services.vacancy_normalization_service import VacancyNormalizationService


class VacancyNormalizationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = VacancyNormalizationService()

    def test_source_url_normalization_removes_tracking_params_and_trailing_punctuation(self) -> None:
        normalized = self.service.normalize_item(
            {
                "title": "CTO",
                "company_name": "Example Corp",
                "source_url": "HTTPS://Example.COM/jobs/1/?utm_source=mail&foo=bar&gclid=123).",
            }
        )

        self.assertEqual(normalized["source_url"], "https://example.com/jobs/1?foo=bar")
        self.assertEqual(normalized["raw_payload"]["source_url"], "https://example.com/jobs/1?foo=bar")

    def test_preserves_source_publication_dates_in_raw_payload(self) -> None:
        normalized = self.service.normalize_item(
            {
                "title": "CIO",
                "company_name": "Example Corp",
                "source_published_at": "2026-06-01",
                "source_updated_at": "2026-06-02",
            }
        )

        self.assertEqual(normalized["raw_payload"]["source_published_at"], "2026-06-01")
        self.assertEqual(normalized["raw_payload"]["source_updated_at"], "2026-06-02")


if __name__ == "__main__":
    unittest.main()
