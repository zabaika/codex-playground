from __future__ import annotations

from collections import Counter
import re
from typing import Any


MARKET_SIGNAL_GROUPS = {
    "executive_scope": ("cto", "cio", "vp", "strategy", "board", "p&l", "budget", "revenue"),
    "people_leadership": ("team", "people", "hiring", "org", "organization", "руковод", "команд"),
    "delivery": ("delivery", "roadmap", "execution", "agile", "program", "portfolio"),
    "platform": ("platform", "cloud", "devops", "sre", "finops", "infrastructure"),
    "architecture": ("architecture", "system", "scalability", "microservices", "integration"),
    "security": ("security", "compliance", "risk", "iso", "soc"),
    "product": ("product", "customer", "market", "growth", "go-to-market"),
    "data_ai": ("data", "analytics", "ai", "ml", "machine learning", "llm"),
}


class CareerPathingFullService:
    def analyze(
        self,
        *,
        profile: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
        vacancies: list[dict[str, Any]],
        lite_analysis: dict[str, Any],
        kb_context: dict[str, Any] | None,
        target_roles: list[str] | None = None,
    ) -> dict[str, Any]:
        candidate_text = self._candidate_text(profile, evidence)
        vacancy_text_by_role = self._vacancy_text_by_role(vacancies)
        role_universe = self._role_universe(
            profile=profile,
            target_roles=target_roles,
            lite_roles=[str(role["role"]) for role in lite_analysis.get("roles", [])],
            vacancy_text_by_role=vacancy_text_by_role,
        )
        vacancy_counts = Counter(str(item.get("role_title") or "").strip() for item in vacancies)
        role_analyses = [
            self._analyze_role(
                role=role,
                candidate_text=candidate_text,
                market_text=vacancy_text_by_role.get(role.casefold(), ""),
                vacancy_count=vacancy_counts.get(role, 0),
                lite_role=self._lite_role(lite_analysis, role),
            )
            for role in role_universe
        ]
        role_analyses.sort(key=lambda item: (item["trajectory_score"], item["market_vacancy_count"], item["role"]), reverse=True)
        primary = role_analyses[0] if role_analyses else {}
        return {
            "mode": "full",
            "state_mutation": "none",
            "primary_trajectory": primary.get("role"),
            "role_universe": role_universe,
            "role_analyses": role_analyses,
            "capability_gap_summary": self._capability_gap_summary(role_analyses),
            "t_shape_branches": self._t_shape_branches(role_analyses, candidate_text),
            "professional_brand_plan": self._brand_plan(primary, role_analyses),
            "trajectory_ranking": self._trajectory_ranking(role_analyses),
            "kb_context": kb_context or {"status": "not_requested"},
            "safety_boundaries": [
                "Do not add capability gaps as candidate facts without candidate-intake confirmation.",
                "Do not change primary target role automatically.",
                "Treat market signals as advisory until reviewed by the operator.",
            ],
        }

    def render_markdown(self, analysis: dict[str, Any]) -> str:
        lines = [
            "# Career Pathing Full",
            "",
            f"Mode: {analysis['mode']}",
            f"State mutation: {analysis['state_mutation']}",
            f"Primary trajectory: {analysis.get('primary_trajectory') or 'not enough data'}",
            "",
            "## Trajectory Ranking",
        ]
        for item in analysis["trajectory_ranking"]:
            lines.extend(
                [
                    f"- {item['role']}: {item['trajectory_score']} ({item['classification']})",
                    f"  - Market vacancies: {item['market_vacancy_count']}",
                    f"  - Matched signals: {', '.join(item['matched_signals']) or 'limited'}",
                    f"  - Capability gaps: {', '.join(item['capability_gaps']) or 'none detected'}",
                ]
            )
        lines.extend(["", "## Capability Gap Summary"])
        lines.extend(f"- {item}" for item in analysis["capability_gap_summary"])
        lines.extend(["", "## T-Shape Development Branches"])
        for branch in analysis["t_shape_branches"]:
            lines.extend(
                [
                    f"- {branch['branch']}",
                    f"  - Why: {branch['why']}",
                    f"  - Next proof artifact: {branch['next_proof_artifact']}",
                ]
            )
        lines.extend(["", "## Professional Brand Plan"])
        brand = analysis["professional_brand_plan"]
        lines.extend(f"- Headline theme: {item}" for item in brand["headline_themes"])
        lines.extend(f"- Proof asset: {item}" for item in brand["proof_assets"])
        lines.extend(f"- Boundary: {item}" for item in brand["boundaries"])
        lines.extend(["", "## KB Context"])
        kb = analysis["kb_context"]
        lines.append(f"- Status: {kb.get('status')}")
        if kb.get("reason"):
            lines.append(f"- Reason: {kb['reason']}")
        for result in kb.get("results", [])[:3]:
            title = result.get("title") or result.get("path") or "KB result"
            lines.append(f"- {title}")
        lines.extend(["", "## Safety Boundaries"])
        lines.extend(f"- {item}" for item in analysis["safety_boundaries"])
        return "\n".join(lines) + "\n"

    def _role_universe(
        self,
        *,
        profile: dict[str, Any],
        target_roles: list[str] | None,
        lite_roles: list[str],
        vacancy_text_by_role: dict[str, str],
    ) -> list[str]:
        roles: list[str] = []
        roles.extend(target_roles or [])
        roles.extend(profile.get("targets", {}).get("target_roles", []) or [])
        current_title = str(profile.get("core_profile", {}).get("current_title") or "").strip()
        if current_title:
            roles.append(current_title)
        roles.extend(lite_roles)
        roles.extend(vacancy_text_by_role.keys())
        return self._dedupe_roles(roles)[:10]

    def _analyze_role(
        self,
        *,
        role: str,
        candidate_text: str,
        market_text: str,
        vacancy_count: int,
        lite_role: dict[str, Any] | None,
    ) -> dict[str, Any]:
        market_signals = self._signals(market_text or role)
        candidate_signals = self._signals(candidate_text)
        matched = sorted(signal for signal in market_signals if signal in candidate_signals)
        gaps = sorted(signal for signal in market_signals if signal not in candidate_signals)
        lite_score = int((lite_role or {}).get("fit_score") or 40)
        score = lite_score + min(vacancy_count * 4, 20) + len(matched) * 5 - len(gaps) * 4
        return {
            "role": role,
            "classification": (lite_role or {}).get("classification") or ("realistic" if score >= 60 else "stretch"),
            "trajectory_score": max(0, min(score, 100)),
            "market_vacancy_count": vacancy_count,
            "matched_signals": matched,
            "capability_gaps": gaps,
            "market_signals": sorted(market_signals),
            "lite_fit_score": lite_score,
        }

    def _capability_gap_summary(self, roles: list[dict[str, Any]]) -> list[str]:
        gaps = Counter(gap for role in roles for gap in role["capability_gaps"])
        if not gaps:
            return ["No repeated deterministic capability gap detected."]
        return [f"{name}: appears in {count} target trajectory signal set(s)" for name, count in gaps.most_common(6)]

    def _t_shape_branches(self, roles: list[dict[str, Any]], candidate_text: str) -> list[dict[str, str]]:
        gap_counts = Counter(gap for role in roles for gap in role["capability_gaps"])
        branches = []
        for gap, _ in gap_counts.most_common(4):
            branches.append(
                {
                    "branch": gap,
                    "why": "Repeated across target role market signals and not visible in confirmed candidate evidence.",
                    "next_proof_artifact": self._proof_artifact_for(gap, candidate_text),
                }
            )
        return branches or [
            {
                "branch": "market validation",
                "why": "Local vacancy sample is too small to expose a repeated gap.",
                "next_proof_artifact": "Import more relevant vacancies and rerun career-pathing-full.",
            }
        ]

    def _brand_plan(self, primary: dict[str, Any], roles: list[dict[str, Any]]) -> dict[str, list[str]]:
        role = str(primary.get("role") or "target role")
        matched = list(primary.get("matched_signals") or [])
        gaps = list(primary.get("capability_gaps") or [])
        headline_signals = matched[:3] or ["confirmed leadership outcomes"]
        return {
            "headline_themes": [f"{role} anchored in {signal.replace('_', ' ')}" for signal in headline_signals],
            "proof_assets": [
                "Resume final version aligned to the selected trajectory.",
                "Short achievement bank mapped to the top 3 trajectory signals.",
                "Reusable outreach message showing evidence, not claims.",
            ],
            "boundaries": [
                f"Do not position as {role} where required evidence is missing.",
                f"Ask candidate-intake to confirm evidence before using gaps such as {', '.join(gaps[:3]) or 'none'}.",
                "Keep stretch positioning separate from realistic target positioning.",
            ],
        }

    def _trajectory_ranking(self, roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "role": role["role"],
                "trajectory_score": role["trajectory_score"],
                "classification": role["classification"],
                "market_vacancy_count": role["market_vacancy_count"],
                "matched_signals": role["matched_signals"],
                "capability_gaps": role["capability_gaps"],
            }
            for role in roles
        ]

    def _candidate_text(self, profile: dict[str, Any], evidence: dict[str, list[dict[str, Any]]]) -> str:
        parts = [
            str(profile.get("core_profile", {}).get("current_title") or ""),
            str(profile.get("core_profile", {}).get("summary_text") or ""),
        ]
        for items in evidence.values():
            for item in items:
                parts.extend(str(value) for value in item.values() if value is not None)
        return self._clean_text(" ".join(parts))

    def _vacancy_text_by_role(self, vacancies: list[dict[str, Any]]) -> dict[str, str]:
        grouped: dict[str, list[str]] = {}
        for vacancy in vacancies:
            role = str(vacancy.get("role_title") or "").strip()
            if not role:
                continue
            grouped.setdefault(role.casefold(), []).append(
                " ".join(
                    [
                        role,
                        str(vacancy.get("company_name") or ""),
                        str(vacancy.get("location_text") or ""),
                        str(vacancy.get("latest_raw_text") or ""),
                    ]
                )
            )
        return {role: self._clean_text(" ".join(parts)) for role, parts in grouped.items()}

    def _signals(self, text: str) -> set[str]:
        found: set[str] = set()
        normalized = self._clean_text(text)
        for group, tokens in MARKET_SIGNAL_GROUPS.items():
            if any(token in normalized for token in tokens):
                found.add(group)
        return found

    def _lite_role(self, lite_analysis: dict[str, Any], role: str) -> dict[str, Any] | None:
        role_key = role.casefold()
        for item in lite_analysis.get("roles", []):
            if str(item.get("role") or "").casefold() == role_key:
                return item
        return None

    def _dedupe_roles(self, roles: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for role in roles:
            clean = str(role).strip()
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                result.append(clean)
        return result

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.casefold())

    def _proof_artifact_for(self, gap: str, candidate_text: str) -> str:
        if gap in candidate_text:
            return "Candidate-intake review: confirm this existing evidence explicitly."
        return f"Collect a concrete achievement, project, or credential proving {gap.replace('_', ' ')}."
