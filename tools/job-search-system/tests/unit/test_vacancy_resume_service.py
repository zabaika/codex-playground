from __future__ import annotations

import unittest

from job_search.application.services.vacancy_resume_service import VacancyResumeService


class VacancyResumeServiceTest(unittest.TestCase):
    def test_build_resume_replaces_existing_vacancy_tailoring_block(self) -> None:
        markdown = "\n".join(
            [
                "# Candidate",
                "",
                "<!-- JSS:VACANCY-TAILORING:START -->",
                "## Vacancy Tailoring Notes",
                "",
                "- Old vacancy.",
                "<!-- JSS:VACANCY-TAILORING:END -->",
            ]
        )

        result = VacancyResumeService().build_resume(
            source_markdown=markdown,
            source_artifact={"artifact_id": "resume-1", "artifact_type": "resume_markdown_final"},
            vacancy={"role_title": "CTO", "company_name": "Acme", "location_text": "Remote"},
            language="en",
        )

        self.assertIn("# Candidate", result)
        self.assertIn("Target vacancy: CTO — Acme.", result)
        self.assertIn("resume roast/report as guidance", result)
        self.assertNotIn("Old vacancy", result)
        self.assertEqual(result.count("JSS:VACANCY-TAILORING:START"), 1)

    def test_build_resume_renders_russian_block_with_default_location(self) -> None:
        result = VacancyResumeService().build_resume(
            source_markdown="# Кандидат\n\ncandidate@example.com\n",
            source_artifact={"artifact_id": "resume-ru-1", "artifact_type": "resume_markdown_final"},
            vacancy={"role_title": "Технический директор", "company_name": "Acme", "location_text": ""},
            language="ru",
        )

        self.assertIn("## Адаптация под вакансию", result)
        self.assertIn("Целевая вакансия: Технический директор — Acme.", result)
        self.assertIn("Локация/формат: не указано.", result)
        self.assertIn("Источник адаптации: `resume-ru-1` (resume_markdown_final).", result)
        self.assertEqual(result.count("JSS:VACANCY-TAILORING:START"), 1)


if __name__ == "__main__":
    unittest.main()
