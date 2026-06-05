from __future__ import annotations

import re
from typing import Any


DEFAULT_WEIGHTS = {
    "role_fit": 30,
    "skill_stack_fit": 30,
    "company_fit": 20,
    "location_work_model_fit": 20,
}
DEFAULT_THRESHOLDS = {
    "high": 70,
    "medium": 50,
}


class VacancyRankingService:
    def rank(
        self,
        *,
        candidate_profile: dict[str, Any],
        vacancies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        matching_rules = self._build_matching_rules(candidate_profile)
        ranked: list[dict[str, Any]] = []
        for vacancy in vacancies:
            scoring = self._score_vacancy(vacancy, matching_rules)
            company_name = str(vacancy.get("company_name") or "")

            ranked.append(
                {
                    **vacancy,
                    "ranking_score": scoring["score"],
                    "ranking_reasons": scoring["score_reasons"],
                    **scoring,
                    "company_name": company_name,
                }
            )
        ranked.sort(
            key=lambda item: (
                self._fit_label_rank(str(item["fit_label"])),
                int(item["score"]),
                str(item.get("updated_at") or ""),
                str(item.get("company_name") or ""),
            ),
            reverse=True,
        )
        return ranked

    def _build_matching_rules(self, candidate_profile: dict[str, Any]) -> dict[str, Any]:
        targets = candidate_profile.get("targets", {})
        compensation = candidate_profile.get("compensation", {})
        search_preferences = candidate_profile.get("search_preferences", {})
        target_roles = self._string_list(targets.get("target_roles"))
        current_title = self._normalize_text(str(candidate_profile.get("core_profile", {}).get("current_title") or ""))
        if current_title and current_title not in target_roles:
            target_roles.append(current_title)
        role_aliases = sorted(self._role_aliases(target_roles))
        skill_tokens = sorted(self._candidate_skill_tokens(candidate_profile))
        company_priorities = self._string_list(
            targets.get("company_priorities") or search_preferences.get("company_priorities")
        )
        company_avoid_list = self._string_list(
            targets.get("company_avoid_list") or search_preferences.get("company_avoid_list")
        )
        remote_preference = str(search_preferences.get("remote_preference") or "").strip()
        work_model_preferences = self._string_list(search_preferences.get("work_model_preferences"))
        target_geographies = self._string_list(search_preferences.get("target_geographies"))
        base_location = self._normalize_text(str(search_preferences.get("base_location") or "").strip())
        salary_floor = self._as_int(compensation.get("salary_floor"))
        compensation_by_currency = self._compensation_by_currency(compensation.get("compensation_by_currency"))

        return {
            "target_roles": target_roles,
            "dealbreakers": {
                "company_avoid_list": company_avoid_list,
                "salary_floor": salary_floor,
                "compensation_by_currency": compensation_by_currency,
                "remote_required": self._remote_required(remote_preference, work_model_preferences),
            },
            "must_haves": {
                "target_roles": target_roles,
                "role_aliases": role_aliases,
                "salary_floor": salary_floor,
                "leadership_target": self._has_leadership_target(target_roles),
            },
            "nice_to_haves": {
                "skill_tokens": skill_tokens,
                "company_priorities": company_priorities,
                "remote_preferred": self._remote_preferred(remote_preference, work_model_preferences),
            },
            "location_preferences": [*target_geographies, *([base_location] if base_location else [])],
            "work_model_preferences": work_model_preferences,
            "scoring_weights": DEFAULT_WEIGHTS,
            "scoring_thresholds": DEFAULT_THRESHOLDS,
        }

    def _score_vacancy(self, vacancy: dict[str, Any], matching_rules: dict[str, Any]) -> dict[str, Any]:
        role_title = str(vacancy.get("role_title") or "")
        company_name = str(vacancy.get("company_name") or "")
        location_text = str(vacancy.get("location_text") or "")
        raw_text = str(vacancy.get("latest_raw_text") or "")
        searchable = self._normalize_text(" ".join([role_title, company_name, location_text, raw_text]))
        normalized_company = self._normalize_text(company_name)

        dealbreakers_hit: list[str] = []
        matched_signals: list[str] = []
        missing_signals: list[str] = []
        score_reasons: list[str] = []

        for company in matching_rules["dealbreakers"]["company_avoid_list"]:
            if company and company in normalized_company:
                dealbreakers_hit.append(f"company_avoid:{company}")

        vacancy_salary, vacancy_currency = self._extract_salary(searchable)
        salary_floor = self._salary_floor_for_currency(
            vacancy_currency=vacancy_currency,
            default_salary_floor=matching_rules["dealbreakers"]["salary_floor"],
            compensation_by_currency=matching_rules["dealbreakers"]["compensation_by_currency"],
        )
        salary_currency_ambiguous = (
            vacancy_salary is not None
            and vacancy_currency is None
            and bool(matching_rules["dealbreakers"]["compensation_by_currency"])
        )
        if salary_floor and vacancy_salary is not None and not salary_currency_ambiguous and vacancy_salary < salary_floor:
            dealbreakers_hit.append("salary_below_floor")

        if matching_rules["dealbreakers"]["remote_required"] and self._onsite_only(searchable):
            dealbreakers_hit.append("remote_required_but_onsite_only")

        role_fit = self._dimension_role_fit(role_title, searchable, matching_rules, matched_signals, missing_signals)
        skill_fit = self._dimension_skill_fit(searchable, matching_rules, matched_signals, missing_signals)
        company_fit = self._dimension_company_fit(normalized_company, matching_rules, matched_signals)
        location_work_model_fit = self._dimension_location_work_model_fit(
            searchable,
            location_text,
            matching_rules,
            matched_signals,
            missing_signals,
        )

        weighted_score = round(
            role_fit * DEFAULT_WEIGHTS["role_fit"]
            + skill_fit * DEFAULT_WEIGHTS["skill_stack_fit"]
            + company_fit * DEFAULT_WEIGHTS["company_fit"]
            + location_work_model_fit * DEFAULT_WEIGHTS["location_work_model_fit"]
        )

        if vacancy_salary is not None and salary_floor and not salary_currency_ambiguous:
            if vacancy_salary >= salary_floor:
                matched_signals.append("salary_floor_met")
            else:
                missing_signals.append("salary_floor_not_met")
        elif vacancy_salary is not None and salary_floor and salary_currency_ambiguous:
            missing_signals.append("salary_currency_unknown")
        elif salary_floor:
            missing_signals.append("salary_unknown")

        if dealbreakers_hit:
            fit_label = "skip"
            score = 0
            score_reasons.extend(dealbreakers_hit)
        else:
            score = int(weighted_score)
            fit_label = self._fit_label(score)
            score_reasons.extend(self._top_reasons(matched_signals, missing_signals))

        needs_review = bool(missing_signals) and fit_label in {"medium", "high"}
        scoring_breakdown = self._scoring_breakdown(
            role_fit=role_fit,
            skill_fit=skill_fit,
            company_fit=company_fit,
            location_work_model_fit=location_work_model_fit,
            vacancy_salary=vacancy_salary,
            vacancy_currency=vacancy_currency,
            salary_floor=salary_floor,
            salary_currency_ambiguous=salary_currency_ambiguous,
            matched_signals=matched_signals,
            missing_signals=missing_signals,
            dealbreakers_hit=dealbreakers_hit,
        )
        return {
            "fit_label": fit_label,
            "score": score,
            "score_reasons": score_reasons,
            "matched_signals": sorted(set(matched_signals)),
            "missing_signals": sorted(set(missing_signals)),
            "dealbreakers_hit": sorted(set(dealbreakers_hit)),
            "needs_review": needs_review,
            "scoring_breakdown": scoring_breakdown,
            "scoring_weights": dict(DEFAULT_WEIGHTS),
            "scoring_thresholds": dict(DEFAULT_THRESHOLDS),
        }

    def _scoring_breakdown(
        self,
        *,
        role_fit: float,
        skill_fit: float,
        company_fit: float,
        location_work_model_fit: float,
        vacancy_salary: int | None,
        vacancy_currency: str | None,
        salary_floor: int | None,
        salary_currency_ambiguous: bool,
        matched_signals: list[str],
        missing_signals: list[str],
        dealbreakers_hit: list[str],
    ) -> dict[str, object]:
        matched = sorted(set(matched_signals))
        missing = sorted(set(missing_signals))
        return {
            "role": self._weighted_dimension(
                score=role_fit,
                weight=DEFAULT_WEIGHTS["role_fit"],
                matched_signals=[signal for signal in matched if signal.startswith("target_role_match:")],
                missing_signals=[signal for signal in missing if signal in {"target_role_missing", "target_role_not_configured"}],
            ),
            "seniority": {
                "status": "mismatch" if "seniority_mismatch_ic_role" in missing else "ok",
                "matched_signals": [],
                "missing_signals": [signal for signal in missing if signal == "seniority_mismatch_ic_role"],
            },
            "skill_stack": self._weighted_dimension(
                score=skill_fit,
                weight=DEFAULT_WEIGHTS["skill_stack_fit"],
                matched_signals=[signal for signal in matched if signal.startswith("skill_match:")],
                missing_signals=[signal for signal in missing if signal.startswith("skill_stack") or signal == "candidate_skills_not_configured"],
            ),
            "company": self._weighted_dimension(
                score=company_fit,
                weight=DEFAULT_WEIGHTS["company_fit"],
                matched_signals=[signal for signal in matched if signal.startswith("company_priority:")],
                missing_signals=[],
            ),
            "work_model_location": self._weighted_dimension(
                score=location_work_model_fit,
                weight=DEFAULT_WEIGHTS["location_work_model_fit"],
                matched_signals=[signal for signal in matched if signal in {"remote_signal", "location_match"}],
                missing_signals=[signal for signal in missing if signal in {"remote_signal_missing", "location_missing"}],
            ),
            "compensation": {
                "vacancy_salary": vacancy_salary,
                "vacancy_currency": vacancy_currency,
                "salary_floor": salary_floor,
                "status": self._compensation_status(
                    vacancy_salary=vacancy_salary,
                    salary_floor=salary_floor,
                    salary_currency_ambiguous=salary_currency_ambiguous,
                    dealbreakers_hit=dealbreakers_hit,
                ),
                "matched_signals": [signal for signal in matched if signal == "salary_floor_met"],
                "missing_signals": [signal for signal in missing if signal.startswith("salary_")],
            },
            "dealbreakers": sorted(set(dealbreakers_hit)),
        }

    def _weighted_dimension(
        self,
        *,
        score: float,
        weight: int,
        matched_signals: list[str],
        missing_signals: list[str],
    ) -> dict[str, object]:
        return {
            "score": round(score, 3),
            "weight": weight,
            "weighted_score": round(score * weight),
            "matched_signals": matched_signals,
            "missing_signals": missing_signals,
        }

    def _compensation_status(
        self,
        *,
        vacancy_salary: int | None,
        salary_floor: int | None,
        salary_currency_ambiguous: bool,
        dealbreakers_hit: list[str],
    ) -> str:
        if "salary_below_floor" in dealbreakers_hit:
            return "below_floor"
        if vacancy_salary is not None and salary_floor and salary_currency_ambiguous:
            return "currency_unknown"
        if vacancy_salary is not None and salary_floor and vacancy_salary >= salary_floor:
            return "floor_met"
        if salary_floor:
            return "unknown"
        return "not_configured"

    def _dimension_role_fit(
        self,
        role_title: str,
        searchable: str,
        matching_rules: dict[str, Any],
        matched_signals: list[str],
        missing_signals: list[str],
    ) -> float:
        target_roles = matching_rules["must_haves"]["target_roles"]
        aliases = matching_rules["must_haves"]["role_aliases"]
        if not target_roles and not aliases:
            missing_signals.append("target_role_not_configured")
            return 0.5
        if matching_rules["must_haves"]["leadership_target"] and self._is_individual_contributor_title(role_title):
            missing_signals.append("seniority_mismatch_ic_role")
            return 0.0
        for role in target_roles:
            if role and role in searchable:
                matched_signals.append(f"target_role_match:{role}")
                return 1.0
        for alias in aliases:
            if alias and alias in searchable:
                matched_signals.append(f"target_role_match:{alias}")
                return 1.0
        missing_signals.append("target_role_missing")
        return 0.0

    def _dimension_skill_fit(
        self,
        searchable: str,
        matching_rules: dict[str, Any],
        matched_signals: list[str],
        missing_signals: list[str],
    ) -> float:
        skill_tokens = matching_rules["nice_to_haves"]["skill_tokens"]
        if not skill_tokens:
            missing_signals.append("candidate_skills_not_configured")
            return 0.5
        hits = [token for token in skill_tokens if token in searchable]
        if hits:
            matched_signals.extend(f"skill_match:{token}" for token in hits[:8])
        missing = [token for token in skill_tokens if token not in searchable]
        if missing and not hits:
            missing_signals.append("skill_stack_missing")
        elif missing:
            missing_signals.append("skill_stack_partial")
        return min(1.0, len(hits) / min(6, max(1, len(skill_tokens))))

    def _dimension_company_fit(
        self,
        normalized_company: str,
        matching_rules: dict[str, Any],
        matched_signals: list[str],
    ) -> float:
        priorities = matching_rules["nice_to_haves"]["company_priorities"]
        if not priorities:
            return 0.7
        for company in priorities:
            if company and company in normalized_company:
                matched_signals.append(f"company_priority:{company}")
                return 1.0
        return 0.6

    def _dimension_location_work_model_fit(
        self,
        searchable: str,
        location_text: str,
        matching_rules: dict[str, Any],
        matched_signals: list[str],
        missing_signals: list[str],
    ) -> float:
        score = 0.0
        parts = 0
        if matching_rules["nice_to_haves"]["remote_preferred"] or matching_rules["dealbreakers"]["remote_required"]:
            parts += 1
            if self._remote_signal(searchable):
                score += 1.0
                matched_signals.append("remote_signal")
            else:
                missing_signals.append("remote_signal_missing")
        locations = matching_rules["location_preferences"]
        if locations:
            parts += 1
            normalized_location = self._normalize_text(location_text)
            if any(location in normalized_location or location in searchable for location in locations):
                score += 1.0
                matched_signals.append("location_match")
            else:
                missing_signals.append("location_missing")
        if parts == 0:
            return 0.5
        return score / parts

    def _candidate_skill_tokens(self, candidate_profile: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        for skill in candidate_profile.get("skill_signals", []):
            name = str(skill.get("skill_name") or "").lower().strip()
            normalized_name = self._normalize_text(name)
            if len(normalized_name) >= 3 and self._is_useful_skill_token(normalized_name):
                tokens.add(normalized_name)
        profile_text = self._normalize_text(
            " ".join(
                [
                    str(candidate_profile.get("core_profile", {}).get("current_title") or ""),
                    str(candidate_profile.get("core_profile", {}).get("summary_text") or ""),
                ]
            )
        )
        curated = {
            "ai",
            "cloud",
            "delivery",
            "devops",
            "devsecops",
            "engineering leadership",
            "finops",
            "kubernetes",
            "platform",
            "platform engineering",
            "product",
            "secure sdlc",
        }
        for token in curated:
            if token in profile_text:
                tokens.add(token)
        return tokens

    def _is_useful_skill_token(self, value: str) -> bool:
        if "—" in value or re.search(r"\b(native|bilingual|working|родной|английский|русский)\b", value):
            return False
        useful_markers = {
            "ai",
            "ansible",
            "cloud",
            "delivery",
            "devops",
            "devsecops",
            "docker",
            "engineering",
            "finops",
            "grafana",
            "jenkins",
            "k8s",
            "kafka",
            "kubernetes",
            "leadership",
            "linux",
            "management",
            "platform",
            "postgresql",
            "prometheus",
            "python",
            "redis",
            "secure",
        }
        return any(marker in value for marker in useful_markers)

    def _role_aliases(self, target_roles: list[str]) -> set[str]:
        aliases: set[str] = set()
        for role in target_roles:
            normalized = self._normalize_text(role)
            if "cto" in normalized or "technical director" in normalized or "технический директор" in normalized:
                aliases.update({"cto", "technical director", "технический директор"})
            if "cio" in normalized or "it director" in normalized or "ит директор" in normalized:
                aliases.update({"cio", "it director", "ит директор"})
            if "head of engineering" in normalized or "руководитель" in normalized:
                aliases.update({"head of engineering", "engineering head", "руководитель разработки", "руководитель отдела разработки"})
            if "vp engineering" in normalized or "vp of engineering" in normalized:
                aliases.update({"vp engineering", "vp of engineering", "vice president engineering"})
            if "director of engineering" in normalized:
                aliases.update({"director of engineering", "engineering director"})
            if "директор по разработке" in normalized or "руководитель технологического" in normalized:
                aliases.update({"director of engineering", "engineering director", "head of engineering", "cto"})
        return aliases

    def _has_leadership_target(self, target_roles: list[str]) -> bool:
        leadership_markers = {
            "cto",
            "cio",
            "head",
            "director",
            "vp",
            "руководитель",
            "директор",
        }
        return any(any(marker in role for marker in leadership_markers) for role in target_roles)

    def _is_individual_contributor_title(self, role_title: str) -> bool:
        title = self._normalize_text(role_title)
        if any(marker in title for marker in ("cto", "cio", "head", "director", "vp", "lead", "manager", "руководитель", "директор")):
            return False
        return any(marker in title for marker in ("developer", "engineer", "разработчик", "программист"))

    def _string_list(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            return []
        return [self._normalize_text(str(item)) for item in values if str(item).strip()]

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value.lower()).strip()

    def _as_int(self, value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(float(str(value).replace(" ", "")))
        except ValueError:
            return None

    def _compensation_by_currency(self, value: object) -> dict[str, dict[str, object]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, object]] = {}
        for raw_currency, raw_compensation in value.items():
            currency = self._normalize_currency(str(raw_currency))
            if currency and isinstance(raw_compensation, dict):
                result[currency] = raw_compensation
        return result

    def _salary_floor_for_currency(
        self,
        *,
        vacancy_currency: str | None,
        default_salary_floor: int | None,
        compensation_by_currency: dict[str, dict[str, object]],
    ) -> int | None:
        if vacancy_currency and vacancy_currency in compensation_by_currency:
            currency_floor = self._as_int(compensation_by_currency[vacancy_currency].get("salary_floor"))
            if currency_floor is not None:
                return currency_floor
        return default_salary_floor

    def _extract_salary(self, searchable: str) -> tuple[int | None, str | None]:
        searchable = re.sub(r"https?://\S+|www\.\S+", " ", searchable, flags=re.IGNORECASE)
        pattern = re.compile(
            r"(?P<prefix>usd|eur|rub|\$|€|₽|руб)?\s*(?P<amount>\d[\d\s]{4,})\s*(?P<suffix>usd|eur|rub|\$|€|₽|руб)?",
            re.IGNORECASE,
        )
        candidates: list[tuple[int, str | None]] = []
        for match in pattern.finditer(searchable):
            amount = self._as_int(match.group("amount"))
            if amount is None:
                continue
            currency = self._normalize_currency(match.group("suffix") or match.group("prefix") or "")
            candidates.append((amount, currency))
        if not candidates:
            return None, None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0]

    def _extract_salary_amount(self, searchable: str) -> int | None:
        amount, _ = self._extract_salary(searchable)
        return amount

    def _normalize_currency(self, value: str) -> str | None:
        normalized = value.strip().lower()
        if normalized in {"usd", "$"}:
            return "USD"
        if normalized in {"eur", "€"}:
            return "EUR"
        if normalized in {"rub", "₽", "руб"}:
            return "RUB"
        return None

    def _remote_required(self, remote_preference: str, work_model_preferences: list[str]) -> bool:
        values = [self._normalize_text(remote_preference), *work_model_preferences]
        return any(value in {"remote", "remote only", "только удаленно", "только удалённо"} for value in values)

    def _remote_preferred(self, remote_preference: str, work_model_preferences: list[str]) -> bool:
        values = [self._normalize_text(remote_preference), *work_model_preferences]
        return any("remote" in value or "удален" in value or "удалён" in value for value in values)

    def _remote_signal(self, searchable: str) -> bool:
        return "remote" in searchable or "удален" in searchable or "удалён" in searchable

    def _onsite_only(self, searchable: str) -> bool:
        return "onsite only" in searchable or "on-site only" in searchable or "только офис" in searchable

    def _fit_label(self, score: int) -> str:
        if score >= DEFAULT_THRESHOLDS["high"]:
            return "high"
        if score >= DEFAULT_THRESHOLDS["medium"]:
            return "medium"
        return "low"

    def _fit_label_rank(self, label: str) -> int:
        return {"skip": 0, "low": 1, "medium": 2, "high": 3}.get(label, 0)

    def _top_reasons(self, matched_signals: list[str], missing_signals: list[str]) -> list[str]:
        if matched_signals:
            return sorted(set(matched_signals))[:8]
        return sorted(set(missing_signals))[:5]
