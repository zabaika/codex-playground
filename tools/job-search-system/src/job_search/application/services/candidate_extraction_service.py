from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import re
from urllib.parse import urlsplit, urlunsplit

from job_search.application.dto.candidate_profile_draft import CandidateProfileDraftDTO
from job_search.application.services.evidence_normalization import dedupe_evidence_map
from job_search.domain.enums import FieldStatus


EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
URL_RE = re.compile(r"https?://[^\s),;]+|www\.[^\s),;]+")
SALARY_RE = re.compile(r"(?P<amount>\d[\d\s]{2,})(?:\s*(?P<currency>руб|RUB|EUR|USD|€|\$))?", re.IGNORECASE)
ROLE_LINE_RE = re.compile(
    r"(?:Технический директор|IT директор|CTO|CIO|Deputy Head[^\n]*|Руководитель[^\n]*|Разработчик[^\n]*|Head of [^\n]*|Technical Director[^\n]*)"
)
RU_MONTHS = {
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2, "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4, "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8, "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10, "ноябрь": 11, "ноября": 11, "декабрь": 12, "декабря": 12,
}
EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


class CandidateExtractionService:
    def build_draft(
        self,
        *,
        candidate_id: str,
        sources: list[dict[str, str]],
    ) -> CandidateProfileDraftDTO:
        values_by_field: dict[str, list[dict[str, str]]] = defaultdict(list)
        language_values: dict[str, list[dict[str, object]]] = defaultdict(list)
        external_profiles: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        experience_entries: list[dict[str, object]] = []
        achievement_evidence: list[dict[str, object]] = []
        education_entries: list[dict[str, object]] = []
        skill_signals: list[dict[str, object]] = []
        recommendations: list[dict[str, object]] = []
        certifications: list[dict[str, object]] = []
        publications: list[dict[str, object]] = []
        awards: list[dict[str, object]] = []
        missing_fields = set()

        for source in sources:
            artifact_id = source["artifact_id"]
            source_kind = str(source.get("source_kind") or "")
            content = source["content_text"]
            extracted = self._extract_from_text(content, source_kind=source_kind)
            for field, value in extracted["scalar_fields"].items():
                if value:
                    values_by_field[field].append({"value": value, "artifact_id": artifact_id, "source_kind": source_kind})
            for language in extracted["languages"]:
                normalized_language_name = str(language.get("language_name", "")).strip()
                if not normalized_language_name:
                    continue
                language_values[normalized_language_name].append(
                    {
                        "artifact_id": artifact_id,
                        "proficiency_level": language.get("proficiency_level"),
                        "is_primary": bool(language.get("is_primary", False)),
                    }
                )
            for profile in extracted["external_profiles"]:
                normalized_profile_url = self._normalize_profile_url(str(profile["profile_url"]))
                key = (profile["platform"], normalized_profile_url)
                external_profiles[key].append({"artifact_id": artifact_id})
            for work_authorization in extracted["work_authorizations"]:
                values_by_field[f"work_authorization:{work_authorization['country_or_region']}"].append(
                    {"value": work_authorization, "artifact_id": artifact_id, "source_kind": source_kind}
                )
            if extracted["search_preferences"]:
                values_by_field["search_preferences"].append(
                    {"value": extracted["search_preferences"], "artifact_id": artifact_id, "source_kind": source_kind}
                )
            if extracted["targets"]:
                values_by_field["targets"].append({"value": extracted["targets"], "artifact_id": artifact_id, "source_kind": source_kind})
            if extracted["compensation"]:
                values_by_field["compensation"].append({"value": extracted["compensation"], "artifact_id": artifact_id, "source_kind": source_kind})
            experience_entries.extend(self._attach_source_id(extracted["experience_entries"], artifact_id))
            achievement_evidence.extend(self._attach_source_id(extracted["achievement_evidence"], artifact_id))
            education_entries.extend(self._attach_source_id(extracted["education_entries"], artifact_id))
            skill_signals.extend(self._attach_source_id(extracted["skill_signals"], artifact_id))
            recommendations.extend(self._attach_source_id(extracted["recommendations"], artifact_id))
            certifications.extend(self._attach_source_id(extracted["certifications"], artifact_id))
            publications.extend(self._attach_source_id(extracted["publications"], artifact_id))
            awards.extend(self._attach_source_id(extracted["awards"], artifact_id))
            missing_fields.update(extracted["missing_fields"])

        payload: dict[str, object] = {
            "core_profile": {},
            "languages": [],
            "external_profiles": [],
            "work_authorizations": [],
            "experience_entries": experience_entries,
            "achievement_evidence": achievement_evidence,
            "education_entries": education_entries,
            "skill_signals": skill_signals,
            "recommendations": recommendations,
            "certifications": certifications,
            "publications": publications,
            "awards": awards,
            "targets": {},
            "compensation": {},
            "platform_preferences": {},
            "search_preferences": {},
            "field_statuses": {},
        }
        field_conflicts: dict[str, list[dict[str, str]]] = {}
        field_evidence: dict[str, list[dict[str, str]]] = {}

        for field, entries in values_by_field.items():
            if field.startswith("work_authorization:"):
                unique_values = {self._stable_value_key(entry["value"]): entry["value"] for entry in entries}
                payload["work_authorizations"].extend(unique_values.values())
                field_evidence[field] = [{"artifact_id": entry["artifact_id"]} for entry in entries]
                continue
            if field == "search_preferences":
                merged: dict[str, object] = {}
                for entry in entries:
                    for key, value in entry["value"].items():
                        if isinstance(value, list):
                            merged.setdefault(key, [])
                            merged[key] = sorted({*merged[key], *value})
                        elif isinstance(value, dict):
                            existing = merged.setdefault(key, {})
                            if isinstance(existing, dict):
                                merged[key] = {**existing, **value}
                        else:
                            distinct_values = {
                                candidate["value"].get(key)
                                for candidate in entries
                                if isinstance(candidate["value"], dict) and candidate["value"].get(key) not in (None, "", [])
                            }
                            if len(distinct_values) > 1:
                                preferred = self._preferred_profile_value(entries, key)
                                if preferred not in (None, "", []):
                                    merged[key] = preferred
                                    continue
                                field_conflicts[f"{field}.{key}"] = [
                                    {"value": candidate["value"].get(key), "artifact_id": candidate["artifact_id"]}
                                    for candidate in entries
                                    if isinstance(candidate["value"], dict) and candidate["value"].get(key) not in (None, "", [])
                                ]
                                continue
                            merged[key] = value
                payload["search_preferences"] = merged
                field_evidence[field] = [{"artifact_id": entry["artifact_id"]} for entry in entries]
                continue
            if field in {"targets", "compensation"}:
                merged: dict[str, object] = {}
                for entry in entries:
                    for key, value in entry["value"].items():
                        if isinstance(value, list):
                            merged.setdefault(key, [])
                            merged[key] = sorted({*merged[key], *value})
                        elif isinstance(value, dict):
                            existing = merged.setdefault(key, {})
                            if isinstance(existing, dict):
                                merged[key] = {**existing, **value}
                        else:
                            distinct_values = {
                                candidate["value"].get(key)
                                for candidate in entries
                                if isinstance(candidate["value"], dict) and candidate["value"].get(key) not in (None, "", [])
                            }
                            if len(distinct_values) > 1:
                                preferred = self._preferred_profile_value(entries, key)
                                if preferred not in (None, "", []):
                                    merged[key] = preferred
                                    continue
                                field_conflicts[f"{field}.{key}"] = [
                                    {"value": candidate["value"].get(key), "artifact_id": candidate["artifact_id"]}
                                    for candidate in entries
                                    if isinstance(candidate["value"], dict) and candidate["value"].get(key) not in (None, "", [])
                                ]
                                continue
                            if key not in merged or merged[key] in (None, "", []):
                                merged[key] = value
                payload[field] = merged
                field_evidence[field] = [{"artifact_id": entry["artifact_id"]} for entry in entries]
                continue
            distinct = {entry["value"] for entry in entries}
            field_evidence[field] = [{"artifact_id": entry["artifact_id"]} for entry in entries]
            if len(distinct) == 1:
                value = next(iter(distinct))
                payload["core_profile"][field] = value
                payload["field_statuses"][field] = FieldStatus.CONFIRMED.value
                missing_fields.discard(field)
            else:
                payload["field_statuses"][field] = FieldStatus.CONFLICTING.value
                field_conflicts[field] = entries

        for language_name, evidence in language_values.items():
            proficiency_level = self._best_language_proficiency(
                [str(entry.get("proficiency_level") or "").strip() for entry in evidence]
            )
            payload["languages"].append(
                {
                    "language_name": language_name,
                    "proficiency_level": proficiency_level or None,
                    "is_primary": any(bool(entry.get("is_primary", False)) for entry in evidence),
                }
            )
            field_evidence[f"language:{language_name}"] = [
                {"artifact_id": str(entry["artifact_id"])}
                for entry in evidence
            ]

        for idx, ((platform, profile_url), evidence) in enumerate(external_profiles.items()):
            payload["external_profiles"].append(
                {
                    "platform": platform,
                    "profile_url": profile_url,
                    "handle_or_slug": self._extract_handle(profile_url),
                    "is_primary": idx == 0,
                    "visibility_status": None,
                }
            )
            field_evidence[f"external_profile:{platform}:{profile_url}"] = evidence

        source_set_id = sha256("|".join(sorted(s["artifact_id"] for s in sources)).encode("utf-8")).hexdigest()
        return CandidateProfileDraftDTO(
            candidate_id=candidate_id,
            source_set_id=source_set_id,
            draft_payload=payload,
            field_conflicts=field_conflicts,
            field_evidence=dedupe_evidence_map(field_evidence),
            missing_fields=sorted(missing_fields),
        )

    def _extract_from_text(self, content: str, *, source_kind: str = "") -> dict[str, object]:
        if source_kind == "profile":
            return self._extract_profile_context(content)

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        scalar_fields: dict[str, str] = {}
        languages: list[dict[str, object]] = []
        external_profiles: list[dict[str, object]] = []
        experience_entries: list[dict[str, object]] = []
        achievement_evidence: list[dict[str, object]] = []
        education_entries: list[dict[str, object]] = []
        skill_signals: list[dict[str, object]] = []
        recommendations: list[dict[str, object]] = []
        certifications: list[dict[str, object]] = []
        publications: list[dict[str, object]] = []
        awards: list[dict[str, object]] = []
        targets: dict[str, object] = {}
        compensation: dict[str, object] = {}
        missing_fields = {"full_name", "primary_email"}

        linkedin_name = self._extract_linkedin_name(lines) if source_kind == "linkedin" else None
        if linkedin_name:
            scalar_fields["full_name"] = linkedin_name
        else:
            for line in lines[:12]:
                if self._looks_like_person_name(line):
                    scalar_fields["full_name"] = line[:200]
                    break
        emails = EMAIL_RE.findall(content)
        if emails:
            scalar_fields["primary_email"] = emails[0]
        phone_match = re.search(r"(?:Phone|Телефон|Mobile|Phone number)\s*[:\-]?\s*(\+[\d\s().-]{7,}\d)", content, re.IGNORECASE)
        phones = [phone_match.group(1)] if phone_match else [phone for phone in PHONE_RE.findall(content) if phone.strip().startswith("+")]
        if phones:
            scalar_fields["primary_phone"] = phones[0].strip()
        for raw_url in URL_RE.findall(content):
            normalized = self._normalize_profile_url(raw_url if raw_url.startswith("http") else f"https://{raw_url}")
            platform = self._profile_platform(normalized)
            if platform:
                external_profiles.append({"platform": platform, "profile_url": normalized})
        location_match = re.search(r"(?:Проживает:|Location:)\s*(.+)", content)
        if location_match:
            scalar_fields["current_location"] = location_match.group(1).strip()
        title_match = re.search(r"(?:^|\n)(?:Технический директор|CTO|CIO|Deputy Head.+|Руководитель отдела разработки)(?:\n|$)", content, re.MULTILINE)
        if title_match:
            scalar_fields["current_title"] = title_match.group(0).strip()
        desired_role_match = re.search(
            r"(?:Желаемая должность и зарплата|Желаемая позиция|Desired position(?: and salary)?)\s*(.+?)(?:\n(?:Специализации|Specializations|Тип занятости|Employment type|Опыт работы|Experience)|$)",
            content,
            re.DOTALL,
        )
        if desired_role_match:
            role_lines = [line.strip(" -—") for line in desired_role_match.group(1).splitlines() if line.strip()]
            if role_lines:
                targets["target_roles"] = [role_lines[0][:200]]
        specializations = re.findall(r"(?m)^—\s*(.+)$", content)
        if specializations:
            targets.setdefault("target_roles", [])
            targets["target_roles"] = sorted({*targets["target_roles"], *[spec[:200] for spec in specializations]})
        salary_source = desired_role_match.group(1) if desired_role_match else content
        salary_match = SALARY_RE.search(salary_source)
        if salary_match and any(marker in salary_source.lower() for marker in ("руб", "rub", "eur", "usd", "€", "$")):
            amount = int(re.sub(r"\s+", "", salary_match.group("amount")))
            compensation["salary_target"] = amount
            raw_currency = (salary_match.group("currency") or "").lower()
            if raw_currency in {"руб", "rub"}:
                compensation["currency"] = "RUB"
            elif raw_currency in {"€", "eur"}:
                compensation["currency"] = "EUR"
            elif raw_currency in {"$", "usd"}:
                compensation["currency"] = "USD"
        summary_match = re.search(r"(?:Summary|Обо мне)\s*(.+?)(?:Experience|Опыт работы|$)", content, re.DOTALL)
        if summary_match:
            scalar_fields["summary_text"] = " ".join(summary_match.group(1).split())[:3000]
        else:
            about_match = re.search(r"Обо мне\s*(.+)", content, re.DOTALL)
            if about_match:
                scalar_fields["summary_text"] = " ".join(about_match.group(1).split())[:3000]

        for match in re.finditer(r"(Русский|Russian|English|Английский)\s*[—(-]?\s*([^)\n]+)?", content):
            name = match.group(1)
            proficiency = (match.group(2) or "").strip(" )")
            normalized_name = "Russian" if name in {"Русский", "Russian"} else "English"
            languages.append(
                {
                    "language_name": normalized_name,
                    "proficiency_level": proficiency or None,
                    "is_primary": normalized_name == "Russian",
                }
            )

        auth_match = re.search(r"Гражданство:\s*([^,\n]+)(?:,\s*есть разрешение на работу:\s*([^\n]+))?", content)
        work_authorizations = []
        if auth_match:
            citizenship = auth_match.group(1).strip()
            permit = (auth_match.group(2) or "").strip()
            work_authorizations.append(
                {
                    "country_or_region": permit or citizenship,
                    "authorization_status": "authorized",
                    "authorization_basis": citizenship if permit else "citizenship",
                    "valid_until": None,
                    "is_primary": True,
                }
            )

        search_preferences = {}
        if "Не готов к переезду" in content:
            search_preferences["relocation_preference"] = "not_ready"
        if "готов к редким командировкам" in content:
            search_preferences["travel_preference"] = "rare_travel_ok"
        if "удалённо" in content:
            search_preferences.setdefault("work_model_preferences", []).append("remote")
        if "на месте работодателя" in content:
            search_preferences.setdefault("work_model_preferences", []).append("on_site")
        if "полная занятость" in content.lower():
            search_preferences.setdefault("employment_type_preferences", []).append("full_time")
        if "удаленно" in content.lower() or "удалённо" in content.lower():
            search_preferences.setdefault("work_model_preferences", []).append("remote")
        if "не более полутора часов" in content.lower():
            search_preferences["commute_preference"] = "up_to_90_minutes"
        if "гибрид" in content.lower():
            search_preferences.setdefault("work_model_preferences", []).append("hybrid")

        experience_entries, achievement_evidence = self._extract_experience_and_achievements(content)
        education_entries = self._extract_education(content)
        skill_signals = self._extract_skills(content)
        recommendations = self._extract_recommendations(content)
        certifications = self._extract_certifications(content)
        publications = self._extract_publications(content)
        awards = self._extract_awards(content)

        if "full_name" in scalar_fields:
            missing_fields.discard("full_name")
        if "primary_email" in scalar_fields:
            missing_fields.discard("primary_email")

        return {
            "scalar_fields": scalar_fields,
            "languages": languages,
            "external_profiles": external_profiles,
            "work_authorizations": work_authorizations,
            "experience_entries": experience_entries,
            "achievement_evidence": achievement_evidence,
            "education_entries": education_entries,
            "skill_signals": skill_signals,
            "recommendations": recommendations,
            "certifications": certifications,
            "publications": publications,
            "awards": awards,
            "targets": targets,
            "compensation": compensation,
            "search_preferences": search_preferences,
            "missing_fields": missing_fields,
        }

    def _empty_extraction_result(self) -> dict[str, object]:
        return {
            "scalar_fields": {},
            "languages": [],
            "external_profiles": [],
            "work_authorizations": [],
            "experience_entries": [],
            "achievement_evidence": [],
            "education_entries": [],
            "skill_signals": [],
            "recommendations": [],
            "certifications": [],
            "publications": [],
            "awards": [],
            "targets": {},
            "compensation": {},
            "search_preferences": {},
            "missing_fields": set(),
        }

    def _extract_profile_context(self, content: str) -> dict[str, object]:
        result = self._empty_extraction_result()
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        scalar_fields: dict[str, str] = {}
        external_profiles: list[dict[str, object]] = []
        targets: dict[str, object] = {}
        compensation: dict[str, object] = {}
        search_preferences: dict[str, object] = {}

        for line in lines[:12]:
            if self._looks_like_person_name(line):
                scalar_fields["full_name"] = line[:200]
                break
        emails = EMAIL_RE.findall(content)
        if emails:
            scalar_fields["primary_email"] = emails[0]
        for raw_url in URL_RE.findall(content):
            normalized = self._normalize_profile_url(raw_url if raw_url.startswith("http") else f"https://{raw_url}")
            platform = self._profile_platform(normalized)
            if platform:
                external_profiles.append({"platform": platform, "profile_url": normalized})

        target_roles = self._extract_markdown_list_section(content, "Target roles")
        if target_roles:
            targets["target_roles"] = target_roles
        target_markets = self._extract_markdown_list_section(content, "Target markets")
        if target_markets:
            targets["target_markets"] = target_markets
            search_preferences["target_geographies"] = target_markets

        company_avoid = self._extract_markdown_list_section(content, "Company avoid list")
        if company_avoid:
            search_preferences["company_avoid_list"] = company_avoid
        company_priorities = self._extract_markdown_list_section(content, "Company priority list")
        if company_priorities:
            search_preferences["company_priorities"] = company_priorities

        compensation.update(self._extract_compensation_context(content, preferred_currency="EUR"))

        preferences = [item.lower() for item in self._extract_markdown_list_section(content, "Search preferences")]
        for preference in preferences:
            if "remote only" in preference:
                search_preferences["remote_preference"] = "remote only"
                search_preferences.setdefault("work_model_preferences", []).append("remote")
            elif "hybrid" in preference:
                search_preferences.setdefault("work_model_preferences", []).append("hybrid")
                search_preferences["hybrid_policy"] = preference
            elif "no relocation" in preference:
                search_preferences["relocation_preference"] = "no_relocation"
            elif "rare travel" in preference:
                search_preferences["travel_preference"] = "rare_travel_ok"

        result["scalar_fields"] = scalar_fields
        result["external_profiles"] = external_profiles
        result["certifications"] = self._extract_certifications(content)
        result["publications"] = self._extract_publications(content)
        result["awards"] = self._extract_awards(content)
        result["targets"] = targets
        result["compensation"] = compensation
        result["search_preferences"] = search_preferences
        return result

    def _extract_markdown_list_section(self, content: str, heading: str) -> list[str]:
        match = re.search(rf"(?im)^\s*{re.escape(heading)}\s*:\s*$", content)
        if not match:
            return []
        tail = content[match.end():]
        next_heading = re.search(r"(?m)^\s*[A-Za-zА-Яа-я][^:\n]{0,80}:\s*$", tail)
        block = tail[:next_heading.start()] if next_heading else tail
        values: list[str] = []
        for line in block.splitlines():
            item = line.strip()
            if not item.startswith("-"):
                continue
            value = item.lstrip("-").strip()
            if value:
                values.append(value)
        return values

    def _extract_compensation_context(self, content: str, *, preferred_currency: str) -> dict[str, object]:
        sections: dict[str, dict[str, object]] = {}
        for match in re.finditer(r"(?im)^\s*Compensation\s+(?P<currency>[A-Z]{3})\s*:\s*$", content):
            currency = match.group("currency").upper()
            tail = content[match.end():]
            next_heading = re.search(r"(?m)^\s*[A-Za-zА-Яа-я][^:\n]{0,80}:\s*$", tail)
            block = tail[:next_heading.start()] if next_heading else tail
            parsed: dict[str, object] = {"currency": currency}
            for line in block.splitlines():
                item = line.strip().lower()
                value_match = re.search(r"(\d[\d\s]*)", item)
                if not value_match:
                    continue
                value = int(re.sub(r"\s+", "", value_match.group(1)))
                if "floor" in item:
                    parsed["salary_floor"] = value
                elif "target" in item:
                    parsed["salary_target"] = value
                elif "aspiration" in item:
                    parsed["salary_aspiration"] = value
            sections[currency] = parsed
        primary = dict(sections.get(preferred_currency) or (next(iter(sections.values())) if sections else {}))
        if sections:
            primary["compensation_by_currency"] = sections
        return primary

    def _looks_like_person_name(self, line: str) -> bool:
        value = line.strip().strip("#").strip()
        if not value or len(value) > 120:
            return False
        lowered = value.lower()
        blocked = {
            "contact",
            "contacts",
            "top skills",
            "languages",
            "experience",
            "education",
            "summary",
            "candidate a search context",
        }
        if lowered in blocked or ":" in value or any(char.isdigit() for char in value):
            return False
        words = value.split()
        if len(words) < 2 or len(words) > 5:
            return False
        return all(re.search(r"[A-Za-zА-Яа-я]", word) for word in words)

    def _extract_linkedin_name(self, lines: list[str]) -> str | None:
        section_markers = {"languages", "языки"}
        skip_markers = {"english", "russian", "английский", "русский"}
        after_languages = False
        for line in lines:
            lowered = line.strip().lower()
            if lowered in section_markers:
                after_languages = True
                continue
            if not after_languages:
                continue
            if lowered in skip_markers or "(" in lowered or lowered in {"experience", "summary"}:
                continue
            if self._looks_like_person_name(line):
                return line[:200]
        return None

    def _preferred_profile_value(self, entries: list[dict[str, object]], key: str) -> object | None:
        for entry in entries:
            value = entry.get("value")
            if entry.get("source_kind") == "profile" and isinstance(value, dict):
                candidate = value.get(key)
                if candidate not in (None, "", []):
                    return candidate
        return None

    def _extract_handle(self, profile_url: str) -> str | None:
        if "/in/" in profile_url:
            return profile_url.rstrip("/").split("/in/")[-1]
        return None

    def _normalize_profile_url(self, profile_url: str) -> str:
        raw = profile_url.strip().rstrip(".,;)")
        parts = urlsplit(raw)
        scheme = parts.scheme.lower() or "https"
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/")
        if not path:
            path = "/"
        return urlunsplit((scheme, netloc, path, "", ""))

    def _profile_platform(self, profile_url: str) -> str | None:
        parts = urlsplit(profile_url)
        host = parts.netloc.lower()
        path = parts.path.strip("/")
        if host.endswith("linkedin.com"):
            return "linkedin" if path.startswith("in/") else None
        if host == "github.com" and path and "/" not in path:
            return "github"
        if host == "gitlab.com" and path and "/" not in path:
            return "gitlab"
        if host in {"t.me", "telegram.me"} and path and "/" not in path:
            return "telegram"
        if host.endswith("habr.com") and path.startswith(("ru/users/", "en/users/")):
            return "habr"
        if host.endswith("medium.com") and path:
            return "medium"
        if host.endswith("about.me") and path:
            return "aboutme"
        if path and path != "/":
            return "other"
        return None

    def _best_language_proficiency(self, values: list[str]) -> str:
        ranked_values: list[tuple[int, str]] = []
        for raw in values:
            normalized = raw.strip()
            if not normalized:
                continue
            ranked_values.append((self._language_proficiency_rank(normalized), normalized))
        if not ranked_values:
            return ""
        ranked_values.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        return ranked_values[0][1]

    def _language_proficiency_rank(self, value: str) -> int:
        normalized = value.strip().casefold()
        if normalized in {"native", "родной", "носитель"}:
            return 100
        known = {
            "c2": 92,
            "c1": 91,
            "b2": 82,
            "b1": 81,
            "a2": 72,
            "a1": 71,
            "advanced": 65,
            "upper-intermediate": 64,
            "upper intermediate": 64,
            "intermediate": 54,
            "pre-intermediate": 44,
            "pre intermediate": 44,
            "elementary": 34,
            "beginner": 24,
        }
        return known.get(normalized, 10)

    def _stable_value_key(self, value: object) -> str:
        if isinstance(value, dict):
            return repr(sorted(value.items()))
        return repr(value)

    def _extract_experience_and_achievements(self, content: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        experience_entries: list[dict[str, object]] = []
        achievement_evidence: list[dict[str, object]] = []
        raw_lines = [line.strip() for line in content.splitlines()]
        lines = [line for line in raw_lines if line]
        role_indexes = [idx for idx, line in enumerate(lines) if ROLE_LINE_RE.fullmatch(line)]
        for pos, role_idx in enumerate(role_indexes):
            role_title = " ".join(lines[role_idx].split())[:200]
            company_idx = role_idx - 1 if role_idx > 0 else role_idx
            company_name = " ".join(lines[company_idx].split())[:300] if company_idx >= 0 else "Unknown"
            period_line = lines[role_idx - 2] if role_idx >= 2 and self._looks_like_period_line(lines[role_idx - 2]) else None
            body_start = role_idx + 1
            body_end = role_indexes[pos + 1] - 1 if pos + 1 < len(role_indexes) else len(lines)
            body_lines = lines[body_start:body_end]
            body = "\n".join(body_lines).strip()
            temp_key = self._stable_value_key((company_name, role_title, body[:60]))
            start_date, end_date, is_current = self._parse_period_line(period_line)
            location = None
            if body_lines and self._looks_like_location_line(body_lines[0]):
                location = body_lines[0]
            experience_entries.append(
                {
                    "company_name": company_name,
                    "role_title": role_title,
                    "start_date": start_date,
                    "end_date": end_date,
                    "is_current": is_current,
                    "location": location,
                    "company_context_text": body[:1000] if body else None,
                    "company_industry": None,
                    "org_scale_hint": None,
                    "domain_context_json": [],
                    "_temp_experience_key": temp_key,
                }
            )
            bullets = re.findall(r"(?m)^[\-•]\s*(.+)$", body)
            if not bullets and body:
                bullets = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", body) if sentence.strip()]
            for bullet in bullets[:20]:
                metric_name = None
                metric_value = None
                metric_unit = None
                percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", bullet)
                if percent_match:
                    metric_name = "percentage_change"
                    metric_value = float(percent_match.group(1).replace(",", "."))
                    metric_unit = "percent"
                achievement_evidence.append(
                    {
                        "experience_ref": temp_key,
                        "achievement_text": bullet[:2000],
                        "metric_name": metric_name,
                        "metric_value": metric_value,
                        "metric_unit": metric_unit,
                        "metric_period": None,
                        "confidence_status": "source_stated",
                    }
                )
        return experience_entries, achievement_evidence

    def _extract_education(self, content: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        match = re.search(r"(?:Education|Образование)\s*(.+?)(?:Навыки|Skills|Дополнительная информация|$)", content, re.DOTALL)
        if not match:
            return results
        block = match.group(1)
        chunks = [chunk.strip() for chunk in re.split(r"\n(?=\d{4}\n|\d{4}\s)", block) if chunk.strip()]
        for chunk in chunks:
            lines = [line.strip() for line in chunk.splitlines() if line.strip()]
            if not lines:
                continue
            institution_name = lines[-2] if len(lines) >= 2 else lines[-1]
            degree = lines[-1]
            years = re.findall(r"(19|20)\d{2}", chunk)
            end_year = None
            if years:
                year_full = re.search(r"(19|20)\d{2}", chunk)
                end_year = int(year_full.group(0)) if year_full else None
            results.append(
                {
                    "institution_name": institution_name[:300],
                    "degree": degree[:300],
                    "faculty": None,
                    "specialization": None,
                    "start_year": None,
                    "end_year": end_year,
                }
            )
        return results

    def _extract_skills(self, content: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        block = self._extract_section_block(content, headings=("Навыки", "Skills", "Top Skills"))
        if not block:
            return results
        raw_skills = re.split(r"[\n,•]+", block)
        seen: set[str] = set()
        for raw in raw_skills:
            skill = " ".join(raw.split()).strip(" -")
            if not skill or len(skill) < 2:
                continue
            if skill.lower() in {"навыки", "знание языков", "languages", "page 1 of 1", "page 1 of 5"}:
                continue
            if self._looks_like_non_skill_line(skill):
                continue
            if skill in seen:
                continue
            seen.add(skill)
            results.append({"skill_name": skill[:200], "skill_group": None, "context": "resume_skill_section"})
        return results[:60]

    def _looks_like_non_skill_line(self, value: str) -> bool:
        lowered = value.lower()
        if len(value) > 80:
            return True
        if URL_RE.search(value):
            return True
        if re.search(r"\b(19|20)\d{2}\b", value):
            return True
        if re.search(r"\b(year|years|month|months|present|experience|education)\b", lowered):
            return True
        if re.match(r"^(русский|английский|russian|english)\b", lowered):
            return True
        return False

    def _extract_recommendations(self, content: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        match = re.search(r"(?:Рекомендации|Recommendations)\s*(.+?)(?:Обо мне|Summary|$)", content, re.DOTALL)
        if not match:
            return results
        lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
        if len(lines) >= 2:
            results.append(
                {
                    "recommender_name": lines[1][:200],
                    "recommender_role": lines[2][:300] if len(lines) > 2 else None,
                    "recommender_company": lines[0][:300],
                    "contact_hint": None,
                    "recommendation_text": None,
                }
            )
        return results

    def _extract_certifications(self, content: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        block = self._extract_section_block(
            content,
            headings=("Сертификаты", "Certifications", "Licenses & Certifications"),
        )
        if not block:
            return results
        for line in self._section_lines(block):
            year_match = re.search(r"(19|20)\d{2}", line)
            certification_name = re.sub(r"\b(19|20)\d{2}\b", "", line).strip(" -—,")
            if certification_name:
                results.append(
                    {
                        "certification_name": certification_name[:300],
                        "issuer": None,
                        "issued_at": f"{year_match.group(0)}-01-01" if year_match else None,
                        "expires_at": None,
                    }
                )
        return results[:20]

    def _extract_publications(self, content: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        block = self._extract_section_block(
            content,
            headings=("Публикации", "Publications"),
        )
        if not block:
            return results
        previous_title: str | None = None
        for line in self._section_lines(block):
            url_match = URL_RE.search(line)
            if url_match and previous_title:
                results.append(
                    {
                        "title": previous_title[:500],
                        "publication_type": None,
                        "publication_url": url_match.group(0),
                        "published_at": None,
                    }
                )
                previous_title = None
                continue
            clean = line.strip(" -—")
            if clean:
                if previous_title is not None:
                    results.append(
                        {
                            "title": previous_title[:500],
                            "publication_type": None,
                            "publication_url": None,
                            "published_at": None,
                        }
                    )
                previous_title = clean
        if previous_title is not None:
            results.append(
                {
                    "title": previous_title[:500],
                    "publication_type": None,
                    "publication_url": None,
                    "published_at": None,
                }
            )
        return results[:20]

    def _extract_awards(self, content: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        block = self._extract_section_block(
            content,
            headings=("Награды", "Awards", "Honors & Awards"),
        )
        if not block:
            return results
        for line in self._section_lines(block):
            year_match = re.search(r"(19|20)\d{2}", line)
            award_name = re.sub(r"\b(19|20)\d{2}\b", "", line).strip(" -—,")
            if award_name:
                results.append(
                    {
                        "award_name": award_name[:300],
                        "awarder": None,
                        "awarded_at": f"{year_match.group(0)}-01-01" if year_match else None,
                    }
                )
        return results[:20]

    def _attach_source_id(self, entries: list[dict[str, object]], artifact_id: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for entry in entries:
            enriched = dict(entry)
            enriched.setdefault("source_artifact_id", artifact_id)
            result.append(enriched)
        return result

    def _parse_month_year(self, raw: str | None) -> str | None:
        if not raw:
            return None
        value = raw.strip()
        if not value:
            return None
        lower = value.lower()
        if lower in {"present", "настоящее время", "по настоящее время"}:
            return None
        month_name = None
        year = None
        parts = value.split()
        if len(parts) == 2 and parts[1].isdigit():
            month_name = parts[0].lower()
            year = int(parts[1])
            if month_name in RU_MONTHS:
                return f"{year:04d}-{RU_MONTHS[month_name]:02d}-01"
            if month_name in EN_MONTHS:
                return f"{year:04d}-{EN_MONTHS[month_name]:02d}-01"
        if value.isdigit() and len(value) == 4:
            return f"{int(value):04d}-01-01"
        return None

    def _parse_period_line(self, raw: str | None) -> tuple[str | None, str | None, bool]:
        if not raw:
            return None, None, False
        parts = re.split(r"\s*[—-]\s*", raw, maxsplit=1)
        start = self._parse_month_year(parts[0]) if parts else None
        end_raw = parts[1] if len(parts) > 1 else None
        is_current = bool(end_raw and end_raw.strip().lower() in {"present", "настоящее время", "по настоящее время"})
        end = None if is_current else self._parse_month_year(end_raw)
        return start, end, is_current

    def _looks_like_period_line(self, line: str) -> bool:
        lowered = line.lower()
        if any(month in lowered for month in RU_MONTHS | EN_MONTHS):
            return True
        return bool(re.match(r"^(19|20)\d{2}\s*[—-]\s*((19|20)\d{2}|present|настоящее время|по настоящее время)$", lowered))

    def _looks_like_location_line(self, line: str) -> bool:
        lowered = line.lower()
        markers = ("москва", "moscow", "russia", "russian federation", "spain", "remote")
        return any(marker in lowered for marker in markers)

    def _extract_section_block(self, content: str, *, headings: tuple[str, ...]) -> str | None:
        joined = "|".join(re.escape(heading) for heading in headings)
        next_headings = (
            "Опыт работы|Experience|Образование|Education|Навыки|Skills|"
            "Top\\s+Skills|Languages|Contact|Рекомендации|Recommendations|Обо мне|Summary|Публикации|Publications|"
            "Сертификаты|Certifications|Licenses\\s*&\\s*Certifications|"
            "Награды|Awards|Honors\\s*&\\s*Awards"
        )
        match = re.search(rf"(?:{joined})\s*(.+?)(?=\n(?:{next_headings})|\Z)", content, re.DOTALL)
        if not match:
            return None
        return match.group(1)

    def _section_lines(self, block: str) -> list[str]:
        return [line.strip() for line in block.splitlines() if line.strip()]
