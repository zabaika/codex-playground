from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from job_search.application.services.vacancy_ranking_service import VacancyRankingService  # noqa: E402


class VacancyRankingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = VacancyRankingService()
        self.profile = {
            "core_profile": {
                "current_title": "CTO",
                "summary_text": "Platform engineering leader with FinOps and cloud infrastructure experience.",
            },
            "targets": {
                "target_roles": ["CTO", "Head of Engineering", "VP Engineering"],
                "company_avoid_list": ["Bad Corp"],
            },
            "compensation": {
                "salary_floor": 100000,
                "currency": "USD",
            },
            "search_preferences": {
                "base_location": "Europe",
                "remote_preference": "remote",
                "work_model_preferences": ["remote"],
            },
            "skill_signals": [
                {"skill_name": "Platform Engineering"},
                {"skill_name": "FinOps"},
                {"skill_name": "Cloud"},
            ],
        }

    def test_dealbreaker_returns_skip_even_with_strong_role_and_skill_fit(self) -> None:
        ranked = self.service.rank(
            candidate_profile=self.profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "v1",
                    "role_title": "CTO",
                    "company_name": "Bad Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Platform Engineering FinOps Cloud. Salary 200000 USD.",
                }
            ],
        )

        self.assertEqual(ranked[0]["fit_label"], "skip")
        self.assertEqual(ranked[0]["score"], 0)
        self.assertIn("company_avoid:bad corp", ranked[0]["dealbreakers_hit"])
        self.assertIn("company_avoid:bad corp", ranked[0]["score_reasons"])

    def test_salary_below_floor_is_hard_dealbreaker_when_salary_is_explicit(self) -> None:
        ranked = self.service.rank(
            candidate_profile=self.profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "v1",
                    "role_title": "VP Engineering",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Platform Engineering FinOps Cloud. Salary 80000 USD.",
                }
            ],
        )

        self.assertEqual(ranked[0]["fit_label"], "skip")
        self.assertIn("salary_below_floor", ranked[0]["dealbreakers_hit"])

    def test_salary_floor_uses_vacancy_currency_when_multiple_compensation_blocks_exist(self) -> None:
        profile = {
            **self.profile,
            "compensation": {
                "salary_floor": 60000,
                "salary_target": 100000,
                "salary_aspiration": 150000,
                "currency": "EUR",
                "compensation_by_currency": {
                    "EUR": {"salary_floor": 60000, "salary_target": 100000, "salary_aspiration": 150000, "currency": "EUR"},
                    "USD": {"salary_floor": 100000, "salary_target": 150000, "salary_aspiration": 180000, "currency": "USD"},
                    "RUB": {"salary_floor": 500000, "salary_target": 650000, "salary_aspiration": 800000, "currency": "RUB"},
                },
            },
        }

        ranked = self.service.rank(
            candidate_profile=profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "usd",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote platform role. Salary 80000 USD.",
                },
                {
                    "canonical_vacancy_id": "rub",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote platform role. hh_salary_text=от 400 000 ₽ за месяц, до вычета налогов.",
                },
                {
                    "canonical_vacancy_id": "eur",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote platform role. Salary 70000 EUR.",
                },
            ],
        )

        by_id = {item["canonical_vacancy_id"]: item for item in ranked}
        self.assertEqual(by_id["usd"]["fit_label"], "skip")
        self.assertEqual(by_id["rub"]["fit_label"], "skip")
        self.assertIn("salary_below_floor", by_id["usd"]["dealbreakers_hit"])
        self.assertIn("salary_below_floor", by_id["rub"]["dealbreakers_hit"])
        self.assertNotEqual(by_id["eur"]["fit_label"], "skip")
        self.assertIn("salary_floor_met", by_id["eur"]["matched_signals"])

    def test_hh_salary_with_ruble_symbol_ignores_vacancy_url_ids(self) -> None:
        profile = {
            **self.profile,
            "compensation": {
                "salary_floor": 60000,
                "currency": "EUR",
                "compensation_by_currency": {
                    "RUB": {"salary_floor": 500000, "currency": "RUB"},
                },
            },
        }

        ranked = self.service.rank(
            candidate_profile=profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "hh",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Remote",
                    "latest_raw_text": (
                        "hh_salary_text=от 150 000 ₽ за месяц, до вычета налогов "
                        "https://hh.ru/vacancy/133640749?hhtmFrom=vacancy_search_list"
                    ),
                }
            ],
        )

        self.assertEqual(ranked[0]["fit_label"], "skip")
        self.assertIn("salary_below_floor", ranked[0]["dealbreakers_hit"])
        self.assertNotIn("salary_currency_unknown", ranked[0]["missing_signals"])

    def test_unknown_salary_currency_does_not_trigger_false_dealbreaker_with_multi_currency_profile(self) -> None:
        profile = {
            **self.profile,
            "compensation": {
                "salary_floor": 60000,
                "currency": "EUR",
                "compensation_by_currency": {
                    "EUR": {"salary_floor": 60000, "currency": "EUR"},
                    "USD": {"salary_floor": 100000, "currency": "USD"},
                },
            },
        }

        ranked = self.service.rank(
            candidate_profile=profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "unknown-currency",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote platform leadership role. Salary 50000.",
                }
            ],
        )

        self.assertNotEqual(ranked[0]["fit_label"], "skip")
        self.assertNotIn("salary_below_floor", ranked[0]["dealbreakers_hit"])
        self.assertIn("salary_currency_unknown", ranked[0]["missing_signals"])

    def test_explainable_high_score_contains_expected_scoring_fields(self) -> None:
        ranked = self.service.rank(
            candidate_profile=self.profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "v1",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote role for Platform Engineering, FinOps and Cloud leadership. Salary 180000 USD.",
                }
            ],
        )

        item = ranked[0]
        self.assertEqual(item["fit_label"], "high")
        self.assertGreaterEqual(item["score"], 70)
        self.assertGreaterEqual(item["ranking_score"], 70)
        self.assertIn("target_role_match:cto", item["matched_signals"])
        self.assertIn("remote_signal", item["matched_signals"])
        self.assertIn("salary_floor_met", item["matched_signals"])
        self.assertTrue(item["score_reasons"])
        self.assertEqual(item["dealbreakers_hit"], [])
        breakdown = item["scoring_breakdown"]
        self.assertEqual(breakdown["role"]["weight"], 30)
        self.assertEqual(breakdown["skill_stack"]["weight"], 30)
        self.assertEqual(breakdown["company"]["weight"], 20)
        self.assertEqual(breakdown["work_model_location"]["weight"], 20)
        self.assertEqual(breakdown["compensation"]["status"], "floor_met")
        self.assertIn("target_role_match:cto", breakdown["role"]["matched_signals"])
        self.assertIn("remote_signal", breakdown["work_model_location"]["matched_signals"])
        self.assertEqual(item["scoring_weights"]["role_fit"], 30)
        self.assertEqual(item["scoring_thresholds"]["high"], 70)

    def test_missing_required_signals_lower_fit_and_flag_review_when_near_shortlist(self) -> None:
        ranked = self.service.rank(
            candidate_profile=self.profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "v1",
                    "role_title": "CTO",
                    "company_name": "Good Corp",
                    "location_text": "Europe",
                    "latest_raw_text": "Engineering leadership role. Compensation not disclosed.",
                }
            ],
        )

        item = ranked[0]
        self.assertEqual(item["fit_label"], "medium")
        self.assertTrue(item["needs_review"])
        self.assertIn("salary_unknown", item["missing_signals"])

    def test_leadership_targets_rank_executive_roles_above_individual_contributor_roles(self) -> None:
        ranked = self.service.rank(
            candidate_profile=self.profile,
            vacancies=[
                {
                    "canonical_vacancy_id": "ic",
                    "role_title": "Backend Developer",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote backend developer role. Platform Engineering Cloud. Salary 150000 USD.",
                },
                {
                    "canonical_vacancy_id": "exec",
                    "role_title": "Head of Engineering",
                    "company_name": "Good Corp",
                    "location_text": "Europe Remote",
                    "latest_raw_text": "Remote engineering leadership for platform, cloud, FinOps and delivery. Salary 150000 USD.",
                },
            ],
        )

        self.assertEqual(ranked[0]["canonical_vacancy_id"], "exec")
        self.assertIn("seniority_mismatch_ic_role", ranked[1]["missing_signals"])
        self.assertLess(ranked[1]["ranking_score"], ranked[0]["ranking_score"])


if __name__ == "__main__":
    unittest.main()
