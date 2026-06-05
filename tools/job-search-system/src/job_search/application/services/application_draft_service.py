from __future__ import annotations

from typing import Any


class ApplicationDraftService:
    def build_message_artifact(
        self,
        *,
        candidate_profile: dict[str, Any],
        vacancy: dict[str, Any],
        language: str,
        target_role: str | None,
    ) -> str:
        headings = self._headings_for(language)
        full_name = str(candidate_profile.get("core_profile", {}).get("full_name") or candidate_profile.get("display_name") or "Candidate")
        role = target_role or str(vacancy.get("role_title") or "")
        company = str(vacancy.get("company_name") or "your company")
        summary = str(candidate_profile.get("core_profile", {}).get("summary_text") or "").strip()
        achievements = candidate_profile.get("achievement_evidence", [])
        top_achievement = str(achievements[0].get("achievement_text") or "") if achievements else ""

        lines: list[str] = []
        lines.append(f"# {headings['title']}")
        lines.append("")
        lines.append(f"{headings['greeting']} {company},")
        lines.append("")
        lines.append(
            self._paragraph(
                language,
                full_name=full_name,
                role=role,
                company=company,
                summary=summary,
            )
        )
        if top_achievement:
            lines.append("")
            lines.append(f"{headings['evidence_label']}: {top_achievement}")
        lines.append("")
        lines.append(headings["close"])
        lines.append(full_name)
        return "\n".join(lines).strip() + "\n"

    def _paragraph(self, language: str, *, full_name: str, role: str, company: str, summary: str) -> str:
        if language.lower().startswith("ru"):
            base = f"Меня зовут {full_name}. Хочу откликнуться на роль {role} в {company}."
            if summary:
                return f"{base} {summary}"
            return base
        base = f"My name is {full_name}. I would like to apply for the {role} role at {company}."
        if summary:
            return f"{base} {summary}"
        return base

    def _headings_for(self, language: str) -> dict[str, str]:
        if language.lower().startswith("ru"):
            return {
                "title": "Application Draft",
                "greeting": "Здравствуйте, команда",
                "evidence_label": "Ключевой релевантный результат",
                "close": "С уважением,",
            }
        return {
            "title": "Application Draft",
            "greeting": "Hello",
            "evidence_label": "Relevant achievement",
            "close": "Best regards,",
        }
