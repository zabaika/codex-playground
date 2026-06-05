from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from job_search.application.services.candidate_extraction_service import CandidateExtractionService  # noqa: E402


class CandidateExtractionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CandidateExtractionService()

    def test_profile_source_sets_search_context_without_polluting_core_fields(self) -> None:
        draft = self.service.build_draft(
            candidate_id="candidate-1",
            sources=[
                {
                    "artifact_id": "resume-1",
                    "source_kind": "resume",
                    "content_text": "Example Candidate\nandrei@example.com\nЖелаемая должность и зарплата\nCTO\n500000 руб.\n",
                },
                {
                    "artifact_id": "profile-1",
                    "source_kind": "profile",
                    "content_text": (
                        "# Candidate A Search Context\n\n"
                        "Target roles:\n- CTO\n- Head of Engineering\n\n"
                        "Target markets:\n- Europe\n- UK\n\n"
                        "Compensation EUR:\n- salary floor: 60000\n- salary target: 100000\n- salary aspiration: 150000\n- currency: EUR\n\n"
                        "Compensation USD:\n- salary floor: 100000\n- salary target: 150000\n- salary aspiration: 180000\n- currency: USD\n\n"
                        "Compensation RUB:\n- salary floor: 500000\n- salary target: 650000\n- salary aspiration: 800000\n- currency: RUB\n\n"
                        "Search preferences:\n- remote only\n- no relocation\n- rare travel acceptable\n\n"
                        "Company avoid list:\n- Sberbank\n\n"
                        "Company priority list:\n- Yandex\n"
                    ),
                },
            ],
        )

        payload = draft.draft_payload
        self.assertEqual(payload["core_profile"]["full_name"], "Example Candidate")
        self.assertEqual(payload["targets"]["target_roles"], ["CTO", "Head of Engineering"])
        self.assertEqual(payload["compensation"]["salary_floor"], 60000)
        self.assertEqual(payload["compensation"]["salary_target"], 100000)
        self.assertEqual(payload["compensation"]["currency"], "EUR")
        self.assertEqual(payload["compensation"]["compensation_by_currency"]["USD"]["salary_floor"], 100000)
        self.assertEqual(payload["compensation"]["compensation_by_currency"]["RUB"]["salary_target"], 650000)
        self.assertEqual(payload["search_preferences"]["remote_preference"], "remote only")
        self.assertIn("Sberbank", payload["search_preferences"]["company_avoid_list"])
        self.assertNotIn("full_name", draft.field_conflicts)
        self.assertNotIn("compensation.currency", draft.field_conflicts)

    def test_linkedin_headers_and_dates_are_not_extracted_as_name_or_phone(self) -> None:
        draft = self.service.build_draft(
            candidate_id="candidate-1",
            sources=[
                {
                    "artifact_id": "linkedin-1",
                    "source_kind": "linkedin",
                    "content_text": "Contact\nTop Skills\nLanguages\n2015 - 2015\nandrei@example.com\nwww.linkedin.com/in/example-candidate\n",
                }
            ],
        )

        core = draft.draft_payload["core_profile"]
        self.assertEqual(core["primary_email"], "andrei@example.com")
        self.assertNotIn("full_name", core)
        self.assertNotIn("primary_phone", core)

    def test_external_profile_extraction_skips_company_root_urls_and_tracking_noise(self) -> None:
        draft = self.service.build_draft(
            candidate_id="candidate-1",
            sources=[
                {
                    "artifact_id": "linkedin-1",
                    "source_kind": "linkedin",
                    "content_text": (
                        "Example Candidate\n"
                        "www.linkedin.com/in/example-candidate?utm_source=x\n"
                        "https://github.com/example-candidate,\n"
                        "https://www.1001tur.ru,www.turizm.ru/\n"
                        "https://www.linkedin.com/company/example\n"
                    ),
                }
            ],
        )

        profiles = draft.draft_payload["external_profiles"]
        urls = {item["profile_url"] for item in profiles}
        platforms = {item["platform"] for item in profiles}
        self.assertIn("https://www.linkedin.com/in/example-candidate", urls)
        self.assertIn("https://github.com/example-candidate", urls)
        self.assertIn("github", platforms)
        self.assertNotIn("https://www.1001tur.ru/", urls)
        self.assertNotIn("https://www.linkedin.com/company/example", urls)

    def test_field_evidence_is_deduplicated_per_field_without_losing_sources(self) -> None:
        draft = self.service.build_draft(
            candidate_id="candidate-1",
            sources=[
                {
                    "artifact_id": "resume-1",
                    "source_kind": "resume",
                    "content_text": "Example Candidate\nRussian\nRussian\nEnglish\n",
                },
                {
                    "artifact_id": "linkedin-1",
                    "source_kind": "linkedin",
                    "content_text": "Languages\nExample Candidate\nRussian\n",
                },
            ],
        )

        self.assertEqual(
            draft.field_evidence["language:Russian"],
            [{"artifact_id": "resume-1"}, {"artifact_id": "linkedin-1"}],
        )
        self.assertEqual(draft.field_evidence["language:English"], [{"artifact_id": "resume-1"}])


if __name__ == "__main__":
    unittest.main()
