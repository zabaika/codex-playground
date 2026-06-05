from __future__ import annotations

import re
from typing import Any


class ResumeAssemblyService:
    def assemble_markdown(
        self,
        *,
        profile: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
        language: str,
        target_role: str | None,
    ) -> str:
        core = profile.get("core_profile", {})
        headings = self._headings_for(language)
        lines: list[str] = []
        lines.append(f"# {core.get('full_name') or profile.get('display_name') or 'Candidate'}")
        if target_role:
            lines.append("")
            lines.append(target_role)
        elif core.get("current_title"):
            lines.append("")
            lines.append(str(core["current_title"]))

        contact_parts = []
        if core.get("primary_email"):
            contact_parts.append(str(core["primary_email"]))
        if core.get("primary_phone"):
            contact_parts.append(str(core["primary_phone"]))
        if core.get("current_location"):
            contact_parts.append(str(core["current_location"]))
        if contact_parts:
            lines.append("")
            lines.append(" | ".join(contact_parts))

        external_profiles = profile.get("external_profiles", [])
        if external_profiles:
            links = [str(item.get("profile_url")) for item in external_profiles if item.get("profile_url")]
            if links:
                lines.append("")
                lines.append(f"{headings['profiles_label']}: " + " | ".join(links))

        if core.get("summary_text"):
            lines.append("")
            lines.append(f"## {headings['summary']}")
            lines.append("")
            lines.append(self._clean_text(str(core["summary_text"])))

        target_roles = profile.get("targets", {}).get("target_roles", [])
        if target_roles:
            lines.append("")
            lines.append(f"## {headings['target_roles']}")
            lines.append("")
            for role in target_roles[:8]:
                lines.append(f"- {role}")

        work_authorizations = profile.get("work_authorizations", [])
        if work_authorizations:
            lines.append("")
            lines.append(f"## {headings['work_authorizations']}")
            lines.append("")
            for item in work_authorizations:
                region = item.get("country_or_region")
                status = item.get("authorization_status")
                basis = item.get("authorization_basis")
                parts = [str(part) for part in (region, status, basis) if part]
                if parts:
                    lines.append(f"- {' — '.join(parts)}")

        languages = profile.get("languages", [])
        if languages:
            lines.append("")
            lines.append(f"## {headings['languages']}")
            lines.append("")
            for item in languages:
                name = item.get("language_name")
                level = item.get("proficiency_level")
                if level:
                    lines.append(f"- {name}: {level}")
                else:
                    lines.append(f"- {name}")

        experience_entries = evidence.get("experience_entries", [])
        achievements = evidence.get("achievement_evidence", [])
        if experience_entries:
            lines.append("")
            lines.append(f"## {headings['experience']}")
            for entry in experience_entries:
                lines.append("")
                title = str(entry.get("role_title") or "")
                company = str(entry.get("company_name") or "")
                lines.append(f"### {title} — {company}".strip(" —"))
                period = self._format_period(entry.get("start_date"), entry.get("end_date"), bool(entry.get("is_current")))
                details = [period] if period else []
                if entry.get("location"):
                    details.append(str(entry["location"]))
                if details:
                    lines.append("")
                    lines.append(" | ".join(details))
                if entry.get("company_context_text"):
                    lines.append("")
                    lines.append(self._clean_text(str(entry["company_context_text"])))
                entry_achievements = [a for a in achievements if a.get("experience_entry_id") == entry.get("experience_entry_id")]
                if entry_achievements:
                    lines.append("")
                    for achievement_text in self._unique_texts(
                        str(achievement.get("achievement_text") or "")
                        for achievement in entry_achievements
                    )[:8]:
                        lines.append(f"- {achievement_text}")

        education_entries = evidence.get("education_entries", [])
        if education_entries:
            lines.append("")
            lines.append(f"## {headings['education']}")
            for entry in education_entries:
                lines.append("")
                title = str(entry.get("institution_name") or "")
                degree = str(entry.get("degree") or "")
                year = entry.get("end_year")
                header = title if not degree else f"{title} — {degree}"
                if year:
                    header = f"{header} ({year})"
                lines.append(f"- {header}")

        skill_signals = evidence.get("skill_signals", [])
        if skill_signals:
            lines.append("")
            lines.append(f"## {headings['skills']}")
            lines.append("")
            unique_skills: list[str] = []
            seen: set[str] = set()
            for entry in skill_signals:
                skill = str(entry.get("skill_name") or "").strip()
                if skill and skill not in seen:
                    seen.add(skill)
                    unique_skills.append(skill)
            lines.append(", ".join(unique_skills[:30]))

        recommendations = evidence.get("recommendations", [])
        if recommendations:
            lines.append("")
            lines.append(f"## {headings['recommendations']}")
            lines.append("")
            for entry in recommendations:
                name = str(entry.get("recommender_name") or "")
                role = str(entry.get("recommender_role") or "")
                company = str(entry.get("recommender_company") or "")
                parts = [part for part in (name, role, company) if part]
                if parts:
                    lines.append(f"- {' — '.join(parts)}")

        certifications = evidence.get("certifications", [])
        if certifications:
            lines.append("")
            lines.append(f"## {headings['certifications']}")
            lines.append("")
            for entry in certifications:
                name = str(entry.get("certification_name") or "")
                issuer = str(entry.get("issuer") or "")
                parts = [part for part in (name, issuer) if part]
                if parts:
                    lines.append(f"- {' — '.join(parts)}")

        publications = evidence.get("publications", [])
        if publications:
            lines.append("")
            lines.append(f"## {headings['publications']}")
            lines.append("")
            for entry in publications:
                title = str(entry.get("title") or "")
                url = str(entry.get("publication_url") or "")
                if title and url:
                    lines.append(f"- {title}: {url}")
                elif title:
                    lines.append(f"- {title}")

        awards = evidence.get("awards", [])
        if awards:
            lines.append("")
            lines.append(f"## {headings['awards']}")
            lines.append("")
            for entry in awards:
                name = str(entry.get("award_name") or "")
                awarder = str(entry.get("awarder") or "")
                parts = [part for part in (name, awarder) if part]
                if parts:
                    lines.append(f"- {' — '.join(parts)}")

        return "\n".join(lines).strip() + "\n"

    def _clean_text(self, value: str) -> str:
        cleaned_lines = []
        for line in value.splitlines():
            if re.fullmatch(r"\s*Page\s+\d+\s+of\s+\d+\s*", line, flags=re.IGNORECASE):
                continue
            stripped = re.sub(r"\s+", " ", line).strip()
            if stripped:
                cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines).strip()

    def _unique_texts(self, values: object) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = self._clean_text(str(raw))
            if not value:
                continue
            key = re.sub(r"\s+", " ", value).casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _format_period(self, start_date: object, end_date: object, is_current: bool) -> str:
        if not start_date and not end_date and not is_current:
            return ""
        start = str(start_date) if start_date else ""
        end = "Present" if is_current else (str(end_date) if end_date else "")
        if start and end:
            return f"{start} - {end}"
        return start or end

    def _headings_for(self, language: str) -> dict[str, str]:
        if language.lower().startswith("ru"):
            return {
                "profiles_label": "Профили",
                "summary": "Профиль",
                "target_roles": "Целевые роли",
                "work_authorizations": "Разрешения на работу",
                "languages": "Языки",
                "experience": "Опыт",
                "education": "Образование",
                "skills": "Навыки",
                "recommendations": "Рекомендации",
                "certifications": "Сертификаты",
                "publications": "Публикации",
                "awards": "Награды",
            }
        return {
            "profiles_label": "Profiles",
            "summary": "Summary",
            "target_roles": "Target Roles",
            "work_authorizations": "Work Authorization",
            "languages": "Languages",
            "experience": "Experience",
            "education": "Education",
            "skills": "Skills",
            "recommendations": "Recommendations",
            "certifications": "Certifications",
            "publications": "Publications",
            "awards": "Awards",
        }
