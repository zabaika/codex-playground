from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from job_search.infrastructure.board_adapters.linkedin_vacancy_adapter import LinkedInVacancyAdapter  # noqa: E402


class LinkedInVacancyAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = LinkedInVacancyAdapter()

    def test_extracts_job_from_manual_page_text(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Head of Engineering",
                    "ScaleOps · Remote Europe",
                    "https://www.linkedin.com/jobs/view/123456789/?trk=public_jobs_topcard-title&utm_source=x",
                    "About the job",
                    "Lead platform engineering and cloud delivery.",
                ]
            ),
            source_origin="manual_page",
        )

        self.assertEqual(extraction.warnings, [])
        self.assertEqual(len(extraction.items), 1)
        item = extraction.items[0]
        self.assertEqual(item.title, "Head of Engineering")
        self.assertEqual(item.company_name, "ScaleOps")
        self.assertEqual(item.location_text, "Remote Europe")
        self.assertEqual(item.external_vacancy_id, "123456789")
        self.assertEqual(item.source_url, "https://www.linkedin.com/jobs/view/123456789")

    def test_saved_url_without_title_and_company_is_not_importable(self) -> None:
        extraction = self.adapter.extract_from_text(
            "https://www.linkedin.com/jobs/view/123456789/",
            source_origin="saved_url",
        )

        self.assertEqual(extraction.items, [])
        self.assertEqual(extraction.warnings, ["block_1: LinkedIn job URL requires title and company before import"])

    def test_extracts_linkedin_alert_email_style_text(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Principal Engineering Manager",
                    "Northstar Systems",
                    "Berlin, Germany · Hybrid",
                    "View job: https://www.linkedin.com/jobs/view/222333444/?trackingId=abc",
                    "This job matches your alert.",
                ]
            ),
            source_origin="alerts_email",
        )

        self.assertEqual(extraction.warnings, [])
        item = extraction.items[0]
        self.assertEqual(item.title, "Principal Engineering Manager")
        self.assertEqual(item.company_name, "Northstar Systems")
        self.assertEqual(item.location_text, "Berlin, Germany · Hybrid")
        self.assertEqual(item.source_url, "https://www.linkedin.com/jobs/view/222333444")

    def test_extracts_multiple_markdown_cards_from_linkedin_job_alert_email(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n\n".join(
                [
                    "[Your job alert for Engineering Leadership](https://www.linkedin.com/comm/jobs/search-results/?keywords=Engineering)",
                    "[Example Search](https://www.linkedin.com/comm/jobs/view/111222333/?trackingId=company-logo) "
                    "[Chief Technology Officer\nExample Search · Greater Madrid Metropolitan Area (Remote) Easy Apply\nEasy Apply Premium\nFast growing]"
                    "(https://www.linkedin.com/comm/jobs/view/111222333/?trackingId=job-posting&utm_source=email)",
                    "[Security Systems](https://www.linkedin.com/comm/jobs/view/444555666/?trackingId=company-logo) "
                    "[Head of Technology\nSecurity Systems · Sant Joan Despi (Hybrid)\nActively recruiting Easy Apply\nEasy Apply]"
                    "(https://www.linkedin.com/comm/jobs/view/444555666/?trackingId=job-posting)",
                ]
            ),
            source_origin="alerts_email",
        )

        self.assertEqual(extraction.warnings, [])
        self.assertEqual(len(extraction.items), 2)
        first, second = extraction.items
        self.assertEqual(first.title, "Chief Technology Officer")
        self.assertEqual(first.company_name, "Example Search")
        self.assertEqual(first.location_text, "Greater Madrid Metropolitan Area · Remote")
        self.assertEqual(first.source_url, "https://www.linkedin.com/jobs/view/111222333")
        self.assertIn("linkedin_workplace_type=Remote", first.raw_text or "")
        self.assertEqual(second.title, "Head of Technology")
        self.assertEqual(second.company_name, "Security Systems")
        self.assertEqual(second.location_text, "Sant Joan Despi · Hybrid")
        self.assertEqual(second.source_url, "https://www.linkedin.com/jobs/view/444555666")

    def test_extracts_multiple_markdown_cards_from_linkedin_recommended_jobs_email(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n\n".join(
                [
                    "[Top job picks for you](https://www.linkedin.com/comm/jobs/collections/recommended)",
                    "[AI Recruiters](https://www.linkedin.com/comm/jobs/view/777888999/?trackingId=company-logo) "
                    "[AI Engineering Manager | Remote | Europe\nAI Recruiters · Spain (Remote)\nActively recruiting Premium\nFast growing]"
                    "(https://www.linkedin.com/comm/jobs/view/777888999/?trackingId=job-posting)",
                    "[Transformation Partners](https://www.linkedin.com/comm/jobs/view/222333555/?trackingId=company-logo) "
                    "[Chief Transformation Technology Officer\nTransformation Partners · Barcelona (On-site)\nActively recruiting Easy Apply]"
                    "(https://www.linkedin.com/comm/jobs/view/222333555/?trackingId=job-posting)",
                ]
            ),
            source_origin="alerts_email",
        )

        self.assertEqual(extraction.warnings, [])
        self.assertEqual(len(extraction.items), 2)
        first, second = extraction.items
        self.assertEqual(first.title, "AI Engineering Manager | Remote | Europe")
        self.assertEqual(first.company_name, "AI Recruiters")
        self.assertEqual(first.location_text, "Spain · Remote")
        self.assertEqual(first.external_vacancy_id, "777888999")
        self.assertEqual(second.title, "Chief Transformation Technology Officer")
        self.assertEqual(second.company_name, "Transformation Partners")
        self.assertEqual(second.location_text, "Barcelona · On-site")
        self.assertEqual(second.external_vacancy_id, "222333555")

    def test_extracts_markdown_cards_from_linkedin_search_results(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n\n".join(
                [
                    "- ![Example Fintech logo](https://media.licdn.com/company-logo.png)",
                    "    [**Head of Engineering (Credit)**](https://www.linkedin.com/jobs/view/4415101513/?eBP=abc&refId=def&trackingId=ghi&trk=flagship3_search_srp_jobs)",
                    "    Example Fintech",
                    "    - Spain (Remote)",
                    "    - Viewed",
                    "    - Promoted",
                    "- ![Architecture Lab logo](https://media.licdn.com/company-logo.png)",
                    "    [**Subdirector de Desarrollo Tecnológico y Arquitectura**](https://www.linkedin.com/jobs/view/4413162359/?eBP=abc&refId=def&trackingId=ghi&trk=flagship3_search_srp_jobs)",
                    "    Architecture Lab",
                    "    - Spain (Hybrid)",
                    "    You'd be a top applicant",
                    "    - Easy Apply",
                ]
            ),
            source_origin="search_results",
        )

        self.assertEqual(extraction.warnings, [])
        self.assertEqual(len(extraction.items), 2)
        first, second = extraction.items
        self.assertEqual(first.title, "Head of Engineering (Credit)")
        self.assertEqual(first.company_name, "Example Fintech")
        self.assertEqual(first.location_text, "Spain · Remote")
        self.assertEqual(first.source_url, "https://www.linkedin.com/jobs/view/4415101513")
        self.assertEqual(first.external_vacancy_id, "4415101513")
        self.assertNotIn("logo", first.title)
        self.assertEqual(second.title, "Subdirector de Desarrollo Tecnológico y Arquitectura")
        self.assertEqual(second.company_name, "Architecture Lab")
        self.assertEqual(second.location_text, "Spain · Hybrid")

    def test_extracts_manual_page_copy_without_url_and_skips_site_noise(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Jobgether",
                    "",
                    "Chief Technology Officer (CTO)",
                    "",
                    "Greater Madrid Metropolitan Area · 3 days ago · 95 applicants",
                    "",
                    "No response insights available yet",
                    "Remote",
                    "Full-time",
                    "Easy Apply",
                    "Save",
                    "Use AI to assess how you fit",
                    "Tailor my resume",
                    "Create cover letter",
                    "About the job",
                    "This is a rare opportunity to step into a high-impact CTO role.",
                ]
            ),
            source_origin="manual_page",
        )

        self.assertEqual(extraction.warnings, ["block_1: LinkedIn job URL missing; imported without external_vacancy_id"])
        self.assertEqual(len(extraction.items), 1)
        item = extraction.items[0]
        self.assertEqual(item.title, "Chief Technology Officer (CTO)")
        self.assertEqual(item.company_name, "Jobgether")
        self.assertEqual(item.location_text, "Greater Madrid Metropolitan Area · Remote")
        self.assertIsNone(item.source_url)
        self.assertIsNone(item.external_vacancy_id)
        self.assertIn("linkedin_workplace_type=Remote", item.raw_text or "")
        self.assertIn("linkedin_employment_type=Full-time", item.raw_text or "")

    def test_extracts_manual_page_copy_with_url_before_site_header(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "https://www.linkedin.com/jobs/view/4416169048/?trk=jobs_biz_prem_srch&utm_source=x",
                    "Company logo for, Remote.",
                    "Remote",
                    "Engineering Team Leader",
                    "Russia · 1 week ago · 40 people clicked apply",
                    "Remote",
                    "Full-time",
                    "Apply",
                    "About the job",
                    "Remote is solving modern organizations' biggest challenge.",
                ]
            ),
            source_origin="manual_page",
        )

        self.assertEqual(extraction.warnings, [])
        item = extraction.items[0]
        self.assertEqual(item.title, "Engineering Team Leader")
        self.assertEqual(item.company_name, "Remote")
        self.assertEqual(item.location_text, "Russia · Remote")
        self.assertEqual(item.source_url, "https://www.linkedin.com/jobs/view/4416169048")
        self.assertEqual(item.external_vacancy_id, "4416169048")
        self.assertIn("linkedin_workplace_type=Remote", item.raw_text or "")
        self.assertIn("linkedin_employment_type=Full-time", item.raw_text or "")

    def test_extracts_manual_page_copy_from_compact_card_when_header_has_dynamic_blocks(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Jobgether logo",
                    "Jobgether",
                    "Share",
                    "Show more options",
                    "Head of Engineering",
                    "Greater Madrid Metropolitan Area · 5 days ago · 65 applicants",
                    "Remote",
                    "Full-time",
                    "Easy Apply",
                    "Save",
                    "Head of Engineering",
                    "Jobgether · Greater Madrid Metropolitan Area (Remote)",
                    "Easy Apply",
                    "Save",
                    "About the job",
                    "This position is posted by Jobgether on behalf of a partner company.",
                ]
            ),
            source_origin="manual_page",
        )

        self.assertEqual(extraction.warnings, ["block_1: LinkedIn job URL missing; imported without external_vacancy_id"])
        item = extraction.items[0]
        self.assertEqual(item.title, "Head of Engineering")
        self.assertEqual(item.company_name, "Jobgether")
        self.assertEqual(item.location_text, "Greater Madrid Metropolitan Area · Remote")
        self.assertIn("linkedin_workplace_type=Remote", item.raw_text or "")
        self.assertIn("linkedin_employment_type=Full-time", item.raw_text or "")

    def test_extracts_requirements_added_by_job_poster_metadata(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "ChVmpion Mind Technology",
                    "Director técnico CTO (IA Agentica)",
                    "Madrid, Community of Madrid, Spain · 2 days ago · 6 applicants",
                    "Hybrid",
                    "Full-time",
                    "About the job",
                    "Startup CTO role.",
                    "Requirements added by the job poster",
                    "• No need for visa sponsorship",
                    "• 5+ years of experience in Estrategia/planificación",
                    "• Authorized to work in España",
                ]
            ),
            source_origin="manual_page",
        )

        item = extraction.items[0]
        self.assertEqual(item.title, "Director técnico CTO (IA Agentica)")
        self.assertEqual(item.company_name, "ChVmpion Mind Technology")
        self.assertEqual(item.location_text, "Madrid, Community of Madrid, Spain · Hybrid")
        self.assertIn("linkedin_poster_requirements_json=", item.raw_text or "")
        self.assertIn("No need for visa sponsorship", item.raw_text or "")
        self.assertIn("Authorized to work in España", item.raw_text or "")

    def test_extracts_unicode_manual_page_copy(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Company logo for, Kryptonite.",
                    "Kryptonite",
                    "Руководитель группы разработки (Scala)",
                    "Moscow City, Russia · 1 week ago · 11 applicants",
                    "Hybrid",
                    "Full-time",
                    "Easy Apply",
                    "About the job",
                    "Основные задачи:",
                    "Разработка отказоустойчивых распределённых систем",
                    "Наши ожидания:",
                    "Продвинутое владение Scala",
                ]
            ),
            source_origin="manual_page",
        )

        item = extraction.items[0]
        self.assertEqual(item.title, "Руководитель группы разработки (Scala)")
        self.assertEqual(item.company_name, "Kryptonite")
        self.assertEqual(item.location_text, "Moscow City, Russia · Hybrid")
        self.assertIn("linkedin_workplace_type=Hybrid", item.raw_text or "")
        self.assertIn("Разработка отказоустойчивых распределённых систем", item.raw_text or "")

    def test_extracts_csv_like_linkedin_rows(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Job Title,Company,Location,Job URL",
                    "VP Engineering,ScaleOps,Remote Europe,https://www.linkedin.com/jobs/view/987654321/?trk=x",
                ]
            ),
            source_origin="manual_csv_like_rows",
        )

        self.assertEqual(extraction.warnings, [])
        item = extraction.items[0]
        self.assertEqual(item.title, "VP Engineering")
        self.assertEqual(item.company_name, "ScaleOps")
        self.assertEqual(item.location_text, "Remote Europe")
        self.assertEqual(item.external_vacancy_id, "987654321")


if __name__ == "__main__":
    unittest.main()
