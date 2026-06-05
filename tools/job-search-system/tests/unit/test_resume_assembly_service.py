from __future__ import annotations

import unittest

from job_search.application.services.resume_assembly_service import ResumeAssemblyService


class ResumeAssemblyServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ResumeAssemblyService()

    def test_removes_pdf_page_markers_and_duplicate_achievement_bullets(self) -> None:
        markdown = self.service.assemble_markdown(
            profile={
                "display_name": "Candidate",
                "core_profile": {
                    "full_name": "Candidate Name",
                    "primary_email": "candidate@example.com",
                    "summary_text": "Platform leader\nPage 1 of 3\nDelivery focus",
                },
            },
            evidence={
                "experience_entries": [
                    {
                        "experience_entry_id": "exp-1",
                        "role_title": "CTO",
                        "company_name": "Example",
                        "company_context_text": "SaaS company\nPage 2 of 3",
                    }
                ],
                "achievement_evidence": [
                    {
                        "experience_entry_id": "exp-1",
                        "achievement_text": "Improved delivery speed by 40%.",
                    },
                    {
                        "experience_entry_id": "exp-1",
                        "achievement_text": "Improved delivery speed by 40%.",
                    },
                ],
            },
            language="en",
            target_role="CTO",
        )

        self.assertNotIn("Page 1 of 3", markdown)
        self.assertNotIn("Page 2 of 3", markdown)
        self.assertEqual(markdown.count("- Improved delivery speed by 40%."), 1)


if __name__ == "__main__":
    unittest.main()
