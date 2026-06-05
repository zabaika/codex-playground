from __future__ import annotations

from typing import Any


class JobSearchPlaybookService:
    def build(
        self,
        *,
        profile: dict[str, Any],
        career_analysis: dict[str, object] | None = None,
    ) -> dict[str, object]:
        target_roles = profile.get("targets", {}).get("target_roles", []) or []
        primary_role = (
            str(target_roles[0])
            if target_roles
            else (
                str(career_analysis.get("primary_target_role"))
                if career_analysis and career_analysis.get("primary_target_role")
                else str(profile.get("core_profile", {}).get("current_title") or "target role")
            )
        )
        markets = profile.get("targets", {}).get("target_markets", []) or []
        search_preferences = profile.get("search_preferences", {})
        compensation = profile.get("compensation", {})
        saved_searches = self._saved_search_design_pack(primary_role, target_roles, markets, search_preferences)
        return {
            "primary_role": primary_role,
            "search_strategy": {
                "focus": f"Prioritize {primary_role} roles with strong evidence alignment.",
                "channels": ["direct company/ATS", "LinkedIn manual search", "referrals", "target company careers pages"],
                "batch_rule": "Process vacancies in small reviewed batches of 5-10.",
            },
            "saved_search_design_pack": saved_searches,
            "reusable_message_artifact": self._message_template(primary_role),
            "compensation_framework": self._compensation_framework(compensation),
            "interview_artifacts": self._interview_artifacts(primary_role),
        }

    def render_markdown(self, playbook: dict[str, object]) -> str:
        lines = [
            "# Job Search Playbook",
            "",
            f"Primary role: {playbook['primary_role']}",
            "",
            "## Search Strategy",
        ]
        strategy = playbook["search_strategy"]
        lines.append(f"- Focus: {strategy['focus']}")
        lines.append(f"- Batch rule: {strategy['batch_rule']}")
        lines.append(f"- Channels: {', '.join(strategy['channels'])}")
        lines.extend(["", "## Saved Search Design Pack"])
        for item in playbook["saved_search_design_pack"]:
            lines.append(f"- {item['name']}: {item['query']}")
            lines.append(f"  - Filters: {', '.join(item['filters']) or 'none'}")
        lines.extend(["", "## Reusable Message Artifact", playbook["reusable_message_artifact"]])
        comp = playbook["compensation_framework"]
        lines.extend(
            [
                "",
                "## Compensation Framework",
                f"- Floor: {comp['salary_floor'] or 'unknown'} {comp['currency'] or ''}".strip(),
                f"- Target: {comp['salary_target'] or 'unknown'} {comp['currency'] or ''}".strip(),
                f"- Negotiation note: {comp['negotiation_note']}",
            ]
        )
        lines.extend(["", "## Interview Artifacts"])
        for item in playbook["interview_artifacts"]:
            lines.append(f"- {item['name']}: {item['purpose']}")
            lines.append(f"  - Output: {item['output']}")
        return "\n".join(lines) + "\n"

    def _saved_search_design_pack(
        self,
        primary_role: str,
        target_roles: list[object],
        markets: list[object],
        search_preferences: dict[str, object],
    ) -> list[dict[str, object]]:
        roles = [str(role) for role in target_roles if str(role).strip()] or [primary_role]
        geographies = [str(item) for item in search_preferences.get("target_geographies", []) if str(item).strip()]
        work_models = [str(item) for item in search_preferences.get("work_model_preferences", []) if str(item).strip()]
        if search_preferences.get("remote_preference"):
            work_models.append(str(search_preferences["remote_preference"]))
        market_text = " OR ".join(str(market) for market in markets if str(market).strip())
        searches = []
        for role in roles[:5]:
            query_parts = [f'"{role}"']
            if market_text:
                query_parts.append(f"({market_text})")
            searches.append(
                {
                    "name": role,
                    "query": " ".join(query_parts),
                    "filters": [*geographies, *sorted(set(work_models))],
                }
            )
        return searches

    def _message_template(self, primary_role: str) -> str:
        return (
            f"Hi {{name}}, I am exploring {primary_role} opportunities where my background can create measurable "
            "engineering and delivery impact. If this role is still active, I would be glad to share a tailored CV "
            "and a short fit summary."
        )

    def _compensation_framework(self, compensation: dict[str, object]) -> dict[str, object]:
        floor = compensation.get("salary_floor")
        target = compensation.get("salary_target")
        currency = compensation.get("currency")
        if floor and target:
            note = "Use floor as reject threshold and target as default negotiation anchor."
        elif floor:
            note = "Use floor as explicit minimum; collect market data before anchoring."
        else:
            note = "Define salary floor before external negotiations."
        return {
            "salary_floor": floor,
            "salary_target": target,
            "salary_aspiration": compensation.get("salary_aspiration"),
            "currency": currency,
            "negotiation_note": note,
        }

    def _interview_artifacts(self, primary_role: str) -> list[dict[str, str]]:
        return [
            {
                "name": "role-fit brief",
                "purpose": f"Prepare a concise narrative for why the candidate fits {primary_role}.",
                "output": "One-page markdown with target role, proof points, risks, and clarifying questions.",
            },
            {
                "name": "achievement story bank",
                "purpose": "Map confirmed career evidence to STAR-style examples without inventing metrics.",
                "output": "Reusable bullets grouped by leadership, delivery, architecture, hiring, and stakeholder impact.",
            },
            {
                "name": "company interview prep",
                "purpose": "Turn a shortlisted company and vacancy into interview-ready research prompts.",
                "output": "Questions to ask, risks to validate, and value hypotheses to test in conversation.",
            },
        ]
