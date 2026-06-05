from __future__ import annotations


class JobBoardOperationsService:
    def build_manual_checklist(
        self,
        *,
        platform: str,
        candidate_profile: dict[str, object],
        vacancy: dict[str, object] | None,
    ) -> dict[str, object]:
        normalized_platform = platform.strip().lower()
        target_role = self._target_role(candidate_profile, vacancy)
        search_preferences = self._dict(candidate_profile.get("search_preferences"))
        compensation = self._dict(candidate_profile.get("compensation"))
        geographies = self._string_list(search_preferences.get("target_geographies"))
        work_models = self._string_list(search_preferences.get("work_model_preferences"))

        return {
            "platform": normalized_platform,
            "target_role": target_role,
            "saved_search_settings": {
                "keywords": self._keywords(target_role),
                "locations": geographies or ["Remote"],
                "work_models": work_models or ["remote", "hybrid"],
                "salary_floor": compensation.get("salary_floor"),
                "salary_currency": compensation.get("currency"),
            },
            "checklist": [
                "Open the platform manually; do not let the system control the browser.",
                "Apply saved-search settings and verify that filters match the candidate constraints.",
                "Open each promising vacancy and copy/export the vacancy text or URL into the vacancy pipeline.",
                "Before submit/send/publish, verify the artifact quality gate and explicit user approval.",
                "After the manual action, record it with record-board-action so traceability stays in storage.",
            ],
            "guardrails": [
                "No credentials are handled by job-search-system.",
                "No unattended browser automation is implied by this checklist.",
                "Manual external actions are logs of what the operator did, not background promises.",
            ],
        }

    def _target_role(self, candidate_profile: dict[str, object], vacancy: dict[str, object] | None) -> str:
        if vacancy and vacancy.get("role_title"):
            return str(vacancy["role_title"])
        targets = self._dict(candidate_profile.get("targets"))
        if targets.get("primary_target_role"):
            return str(targets["primary_target_role"])
        core = self._dict(candidate_profile.get("core_profile"))
        if core.get("current_title"):
            return str(core["current_title"])
        return "target role"

    def _keywords(self, target_role: str) -> list[str]:
        keywords = [target_role]
        lowered = target_role.lower()
        if "engineering" in lowered or "cto" in lowered:
            keywords.extend(["platform engineering", "cloud", "delivery leadership"])
        return keywords

    def _dict(self, value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    def _string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]
