from __future__ import annotations

from typing import Any


class ResumePositioningService:
    def build_positioning_brief(
        self,
        *,
        profile: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
        target_role: str,
        language: str,
    ) -> str:
        headings = self._headings_for(language)
        lines: list[str] = []
        core = profile.get("core_profile", {})
        lines.append(f"# {headings['title']}")
        lines.append("")
        lines.append(f"- {headings['candidate']}: {core.get('full_name') or profile.get('display_name') or 'Candidate'}")
        lines.append(f"- {headings['target_role']}: {target_role}")
        if core.get("current_title"):
            lines.append(f"- {headings['current_title']}: {core.get('current_title')}")
        if core.get("current_location"):
            lines.append(f"- {headings['location']}: {core.get('current_location')}")

        summary = core.get("summary_text")
        if summary:
            lines.append("")
            lines.append(f"## {headings['core_story']}")
            lines.append("")
            lines.append(str(summary))

        target_keywords = self._keywords_for_role(target_role)
        prioritized_achievements = self._rank_achievements(
            evidence.get("achievement_evidence", []),
            target_keywords,
        )
        if prioritized_achievements:
            lines.append("")
            lines.append(f"## {headings['key_evidence']}")
            lines.append("")
            for item in prioritized_achievements[:8]:
                lines.append(f"- {item.get('achievement_text')}")

        experience_entries = evidence.get("experience_entries", [])
        if experience_entries:
            lines.append("")
            lines.append(f"## {headings['relevant_experience']}")
            lines.append("")
            for entry in experience_entries[:5]:
                title = str(entry.get("role_title") or "")
                company = str(entry.get("company_name") or "")
                context = str(entry.get("company_context_text") or "").strip()
                lines.append(f"- {title} — {company}".strip(" —"))
                if context:
                    lines.append(f"  {context[:280]}")

        skills = self._rank_skills(evidence.get("skill_signals", []), target_keywords)
        if skills:
            lines.append("")
            lines.append(f"## {headings['skills_to_emphasize']}")
            lines.append("")
            lines.append(", ".join(skills[:20]))

        lines.append("")
        lines.append(f"## {headings['rewrite_guidance']}")
        lines.append("")
        for bullet in self._rewrite_guidance(target_role, language):
            lines.append(f"- {bullet}")

        return "\n".join(lines).strip() + "\n"

    def _rank_achievements(self, achievements: list[dict[str, Any]], keywords: set[str]) -> list[dict[str, Any]]:
        scored = []
        for item in achievements:
            text = str(item.get("achievement_text") or "")
            lowered = text.lower()
            score = sum(1 for keyword in keywords if keyword in lowered)
            if "%" in text or item.get("metric_value") is not None:
                score += 2
            if any(token in lowered for token in ("team", "команд", "engineer", "разработ", "director", "директор")):
                score += 1
            scored.append((score, text, item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [item for _, _, item in scored]

    def _rank_skills(self, skills: list[dict[str, Any]], keywords: set[str]) -> list[str]:
        scored: list[tuple[int, str]] = []
        seen: set[str] = set()
        for item in skills:
            skill = str(item.get("skill_name") or "").strip()
            if not skill or skill.lower() in seen:
                continue
            seen.add(skill.lower())
            lowered = skill.lower()
            score = sum(1 for keyword in keywords if keyword in lowered)
            scored.append((score, skill))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [skill for _, skill in scored]

    def _keywords_for_role(self, target_role: str) -> set[str]:
        lowered = target_role.lower()
        keywords = {"delivery", "platform", "architecture", "productivity"}
        if any(token in lowered for token in ("cto", "cio", "head", "director", "руковод")):
            keywords |= {
                "strategy", "scal", "team", "org", "budget", "cost", "platform",
                "команд", "масштаб", "бюдж", "эффектив", "директор", "инфра",
            }
        if "ai" in lowered:
            keywords |= {"ai", "llm", "rag", "automation", "assistant"}
        return keywords

    def _rewrite_guidance(self, target_role: str, language: str) -> list[str]:
        if language.lower().startswith("ru"):
            return [
                f"Сделать явным позиционирование под роль {target_role}.",
                "Вынести наверх измеримые результаты и масштаб ответственности.",
                "Сократить нейтральные описания обязанностей в пользу бизнес-эффекта и управленческого контекста.",
                "Подсветить профильные навыки и домены, которые усиливают целевую роль.",
            ]
        return [
            f"Make the resume explicitly positioned for the {target_role} role.",
            "Move measurable outcomes and scope to the top.",
            "Compress neutral responsibility descriptions in favor of business impact and leadership context.",
            "Highlight skills and domains that reinforce the target role.",
        ]

    def _headings_for(self, language: str) -> dict[str, str]:
        if language.lower().startswith("ru"):
            return {
                "title": "Позиционирование резюме",
                "candidate": "Кандидат",
                "target_role": "Целевая роль",
                "current_title": "Текущий заголовок",
                "location": "Локация",
                "core_story": "Базовый narrative",
                "key_evidence": "Ключевые доказательства",
                "relevant_experience": "Релевантный опыт",
                "skills_to_emphasize": "Навыки для усиления",
                "rewrite_guidance": "Подсказки для переписывания",
            }
        return {
            "title": "Resume Positioning Brief",
            "candidate": "Candidate",
            "target_role": "Target Role",
            "current_title": "Current Title",
            "location": "Location",
            "core_story": "Core Story",
            "key_evidence": "Key Evidence",
            "relevant_experience": "Relevant Experience",
            "skills_to_emphasize": "Skills to Emphasize",
            "rewrite_guidance": "Rewrite Guidance",
        }
