from __future__ import annotations

import unittest

from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService


class ResumeQualityGateServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ResumeQualityGateService()

    def test_warns_on_pdf_page_markers_and_duplicate_bullets(self) -> None:
        result = self.service.check_markdown(
            markdown=(
                "# Example Candidate\n\n"
                "andrei@example.com\n\n"
                "## Experience\n\n"
                "### Page 2 of 5\n"
                "Page 1 of 5\n"
                "- Improved delivery speed by 40% across engineering teams.\n"
                "- Improved delivery speed by 40% across engineering teams.\n"
            )
        )
        codes = {issue["code"] for issue in result["issues"]}

        self.assertEqual(result["status"], "warn")
        self.assertIn("pdf_page_marker_detected", codes)
        self.assertIn("duplicate_bullets_detected", codes)
        self.assertIn("misparsed_experience_entries", codes)

    def test_application_message_rejects_generic_company_placeholder(self) -> None:
        result = self.service.check_application_message(
            markdown=(
                "# Application Draft\n\n"
                "Hello your company,\n\n"
                "My name is Andrei. I would like to apply for the CTO role at your company.\n\n"
                "Best regards,\n"
                "Andrei\n"
            ),
            target_role="CTO",
            target_company="Good Corp",
        )
        codes = {issue["code"] for issue in result["issues"]}

        self.assertEqual(result["status"], "fail")
        self.assertIn("generic_company_placeholder", codes)
        self.assertIn("target_company_not_visible", codes)


if __name__ == "__main__":
    unittest.main()
