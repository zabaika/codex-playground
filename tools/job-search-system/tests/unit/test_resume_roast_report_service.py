from __future__ import annotations

import unittest

from job_search.application.services.resume_roast_report_service import ResumeRoastReportService


class ResumeRoastReportServiceTest(unittest.TestCase):
    def test_report_contains_source_link_sections_and_future_derivation_note(self) -> None:
        report = ResumeRoastReportService().build_report(
            resume_artifact_id="resume-1",
            resume_storage_path="/tmp/resume.md",
            markdown="# Candidate\n\ncandidate@example.com\n\n## Summary\n\nResponsible for platform work.\n",
            target_role="CTO",
            quality_gate={
                "status": "warn",
                "issues": [{"severity": "warn", "code": "example", "message": "Needs work."}],
            },
        )

        self.assertIn("Source resume artifact: `resume-1`", report)
        self.assertIn("## Positioning Risks", report)
        self.assertIn("## Evidence Gaps", report)
        self.assertIn("## Weak / Generic Claims", report)
        self.assertIn("## Future Rewrite Linkage", report)


if __name__ == "__main__":
    unittest.main()
