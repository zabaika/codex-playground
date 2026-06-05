from __future__ import annotations

from typing import Any


class VacancyResumeService:
    def build_resume(
        self,
        *,
        source_markdown: str,
        source_artifact: dict[str, Any],
        vacancy: dict[str, Any],
        language: str,
    ) -> str:
        cleaned = self._remove_existing_vacancy_block(source_markdown)
        block = self._vacancy_block(source_artifact=source_artifact, vacancy=vacancy, language=language)
        return f"{cleaned.rstrip()}\n\n{block}\n"

    def _remove_existing_vacancy_block(self, markdown: str) -> str:
        start = "<!-- JSS:VACANCY-TAILORING:START -->"
        end = "<!-- JSS:VACANCY-TAILORING:END -->"
        if start not in markdown or end not in markdown:
            return markdown
        before, _, tail = markdown.partition(start)
        _, _, after = tail.partition(end)
        return f"{before.rstrip()}\n\n{after.lstrip()}".strip() + "\n"

    def _vacancy_block(self, *, source_artifact: dict[str, Any], vacancy: dict[str, Any], language: str) -> str:
        role = str(vacancy.get("role_title") or "")
        company = str(vacancy.get("company_name") or "")
        location = str(vacancy.get("location_text") or "")
        source_type = str(source_artifact.get("artifact_type") or "")
        if language.lower().startswith("ru"):
            title = "## Адаптация под вакансию"
            bullets = [
                f"Целевая вакансия: {role} — {company}.",
                f"Локация/формат: {location or 'не указано'}.",
                f"Источник адаптации: `{source_artifact.get('artifact_id')}` ({source_type}).",
                "Детерминированная версия: используйте quality gate и resume roast/report как guidance; полноценный AI rewrite остаётся будущим AI layer.",
            ]
        else:
            title = "## Vacancy Tailoring Notes"
            bullets = [
                f"Target vacancy: {role} — {company}.",
                f"Location/work model: {location or 'not specified'}.",
                f"Source resume: `{source_artifact.get('artifact_id')}` ({source_type}).",
                "Deterministic draft: use quality gate and resume roast/report as guidance; full AI rewrite stays in the future AI layer.",
            ]
        lines = ["<!-- JSS:VACANCY-TAILORING:START -->", title, ""]
        lines.extend(f"- {bullet}" for bullet in bullets)
        lines.append("<!-- JSS:VACANCY-TAILORING:END -->")
        return "\n".join(lines)
