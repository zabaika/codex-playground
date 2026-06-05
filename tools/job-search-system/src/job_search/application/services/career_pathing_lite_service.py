from __future__ import annotations

import re
from typing import Any


DEFAULT_ROLE_UNIVERSE = [
    "CTO",
    "CIO",
    "VP Engineering",
    "Head of Engineering",
    "Engineering Director",
]


class CareerPathingLiteService:
    def analyze(
        self,
        *,
        profile: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
        target_roles: list[str] | None = None,
    ) -> dict[str, object]:
        roles = self._role_candidates(profile, target_roles)
        skill_text = self._candidate_text(profile, evidence)
        leadership_score = self._leadership_score(skill_text)
        analyzed_roles = [self._analyze_role(role, skill_text, leadership_score) for role in roles[:5]]
        analyzed_roles.sort(key=lambda item: (item["fit_score"], item["role"]), reverse=True)
        primary = next((role for role in analyzed_roles if role["classification"] == "realistic"), analyzed_roles[0])
        return {
            "primary_target_role": primary["role"],
            "roles": analyzed_roles,
            "title_inflation_risks": self._title_inflation_risks(analyzed_roles),
            "safe_positioning_boundaries": self._safe_positioning_boundaries(primary),
        }

    def render_markdown(self, analysis: dict[str, object]) -> str:
        lines = [
            "# Career Pathing Lite",
            "",
            f"Primary target role: {analysis['primary_target_role']}",
            "",
            "## Roles",
        ]
        for role in analysis["roles"]:
            lines.extend(
                [
                    f"- {role['role']}: {role['classification']} ({role['fit_score']})",
                    f"  - Evidence: {', '.join(role['matched_signals']) or 'limited'}",
                    f"  - Gaps: {', '.join(role['gaps']) or 'none detected'}",
                ]
            )
        lines.extend(["", "## Title Inflation Risks"])
        risks = analysis["title_inflation_risks"]
        lines.extend([f"- {risk}" for risk in risks] if risks else ["- none detected"])
        lines.extend(["", "## Safe Positioning Boundaries"])
        lines.extend(f"- {item}" for item in analysis["safe_positioning_boundaries"])
        return "\n".join(lines) + "\n"

    def _role_candidates(self, profile: dict[str, Any], target_roles: list[str] | None) -> list[str]:
        roles: list[str] = []
        roles.extend(target_roles or [])
        roles.extend(profile.get("targets", {}).get("target_roles", []) or [])
        current_title = str(profile.get("core_profile", {}).get("current_title") or "").strip()
        if current_title:
            roles.append(current_title)
        roles.extend(DEFAULT_ROLE_UNIVERSE)
        deduped: list[str] = []
        seen: set[str] = set()
        for role in roles:
            role_text = str(role).strip()
            key = role_text.lower()
            if role_text and key not in seen:
                seen.add(key)
                deduped.append(role_text)
        return deduped[:5] if len(deduped) >= 2 else DEFAULT_ROLE_UNIVERSE[:3]

    def _candidate_text(self, profile: dict[str, Any], evidence: dict[str, list[dict[str, Any]]]) -> str:
        parts = [
            str(profile.get("core_profile", {}).get("current_title") or ""),
            str(profile.get("core_profile", {}).get("summary_text") or ""),
        ]
        for group in ("experience_entries", "achievement_evidence", "skill_signals"):
            for item in evidence.get(group, []):
                parts.extend(str(value) for value in item.values() if value is not None)
        return re.sub(r"\s+", " ", " ".join(parts).lower())

    def _leadership_score(self, text: str) -> int:
        signals = ("lead", "head", "director", "cto", "cio", "руковод", "директор", "команд", "team", "people")
        return sum(1 for signal in signals if signal in text)

    def _analyze_role(self, role: str, text: str, leadership_score: int) -> dict[str, object]:
        role_key = role.lower()
        matched: list[str] = []
        gaps: list[str] = []
        score = 30
        for token in self._role_tokens(role_key):
            if token in text:
                score += 15
                matched.append(token)
        if leadership_score >= 3:
            score += 25
            matched.append("leadership_scope")
        else:
            gaps.append("leadership_scope_evidence")
        if any(token in role_key for token in ("cto", "cio", "vp")) and leadership_score < 4:
            gaps.append("executive_scope_evidence")
        if any(token in text for token in ("platform", "architecture", "cloud", "delivery", "finops")):
            score += 10
            matched.append("technical_leadership")
        classification = "realistic" if score >= 60 and len(gaps) <= 1 else "stretch"
        return {
            "role": role,
            "classification": classification,
            "fit_score": min(score, 100),
            "matched_signals": sorted(set(matched)),
            "gaps": sorted(set(gaps)),
        }

    def _role_tokens(self, role: str) -> list[str]:
        return [token for token in re.split(r"[^a-zа-я0-9]+", role) if len(token) >= 3]

    def _title_inflation_risks(self, roles: list[dict[str, object]]) -> list[str]:
        risks = []
        for role in roles:
            if role["classification"] == "stretch" and any(
                token in str(role["role"]).lower() for token in ("cto", "cio", "vp")
            ):
                risks.append(f"{role['role']}: may require stronger executive-scope evidence")
        return risks

    def _safe_positioning_boundaries(self, primary: dict[str, object]) -> list[str]:
        return [
            f"Position as {primary['role']} only where evidence supports scope.",
            "Do not invent budget, headcount, revenue, or board-level ownership.",
            "Separate realistic roles from stretch roles in external-facing artifacts.",
        ]
