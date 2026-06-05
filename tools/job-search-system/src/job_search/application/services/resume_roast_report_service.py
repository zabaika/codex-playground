from __future__ import annotations

import re
from typing import Any


class ResumeRoastReportService:
    def build_report(
        self,
        *,
        resume_artifact_id: str,
        resume_storage_path: str,
        markdown: str,
        target_role: str | None,
        quality_gate: dict[str, Any],
    ) -> str:
        role = target_role or "unspecified target role"
        lines = [
            f"# Resume Roast Report - {role}",
            "",
            f"- Source resume artifact: `{resume_artifact_id}`",
            f"- Source resume path: `{resume_storage_path}`",
            f"- Target role: {role}",
            f"- Quality gate status: {quality_gate.get('status')}",
            "",
            "## Positioning Risks",
            "",
            *self._bullets(self._positioning_risks(markdown, target_role)),
            "",
            "## Evidence Gaps",
            "",
            *self._bullets(self._evidence_gaps(markdown)),
            "",
            "## Weak / Generic Claims",
            "",
            *self._bullets(self._generic_claims(markdown)),
            "",
            "## Rewrite Actions",
            "",
            *self._bullets(self._rewrite_actions(markdown, target_role)),
            "",
            "## Quality Gate Issues",
            "",
            *self._bullets(self._quality_gate_issues(quality_gate)),
            "",
            "## Future Rewrite Linkage",
            "",
            "- If a new resume is created from this report, store it as a new resume artifact derived from this roast report artifact.",
        ]
        return "\n".join(lines).strip() + "\n"

    def _positioning_risks(self, markdown: str, target_role: str | None) -> list[str]:
        risks = []
        if target_role and target_role.casefold() not in markdown.casefold():
            risks.append(f"Target role `{target_role}` is not visible enough in the resume body.")
        if len(re.findall(r"(?m)^##\s+", markdown)) < 3:
            risks.append("Resume has too few sections to make positioning explicit.")
        if not re.search(r"\b(team|budget|revenue|cost|scale|platform|delivery|strategy)\b", markdown, flags=re.IGNORECASE):
            risks.append("Leadership/business-impact vocabulary is weak or absent.")
        return risks or ["No deterministic positioning risk detected."]

    def _evidence_gaps(self, markdown: str) -> list[str]:
        gaps = []
        if not re.search(r"\d+[%x]|\b\d{2,}\b", markdown):
            gaps.append("Few measurable outcomes are visible; add metrics where facts support them.")
        if "## Experience" not in markdown and "## Опыт" not in markdown:
            gaps.append("Experience section is missing or misparsed.")
        if "## Skills" not in markdown and "## Навыки" not in markdown:
            gaps.append("Skills section is missing or not extracted into the generated resume.")
        return gaps or ["No deterministic evidence gap detected."]

    def _generic_claims(self, markdown: str) -> list[str]:
        claims = []
        patterns = [
            r"\bresponsible for\b",
            r"\bparticipated in\b",
            r"\bworked on\b",
            r"\bstrong communication\b",
            r"\bteam player\b",
            r"\bответственн\w+\b",
            r"\bучаствовал\w*\b",
        ]
        for pattern in patterns:
            if re.search(pattern, markdown, flags=re.IGNORECASE):
                claims.append(f"Generic wording detected: `{pattern}`.")
        return claims or ["No obvious generic claim pattern detected."]

    def _rewrite_actions(self, markdown: str, target_role: str | None) -> list[str]:
        actions = [
            "Move the strongest quantified outcomes into the top third of the resume.",
            "Rewrite responsibility-like bullets into outcome, scope, and business-impact statements.",
            "Remove extraction noise before external use.",
        ]
        if target_role:
            actions.insert(0, f"Make the first screen explicitly support `{target_role}`.")
        if len(markdown) > 9000:
            actions.append("Compress older or less relevant experience to reduce resume length.")
        return actions

    def _quality_gate_issues(self, quality_gate: dict[str, Any]) -> list[str]:
        issues = quality_gate.get("issues") or []
        if not issues:
            return ["Quality gate reported no issues."]
        result = []
        for issue in issues:
            if isinstance(issue, dict):
                result.append(f"{issue.get('severity', 'unknown')}: {issue.get('code', 'unknown')} - {issue.get('message', '')}")
        return result or ["Quality gate reported issues in an unsupported shape."]

    def _bullets(self, values: list[str]) -> list[str]:
        return [f"- {value}" for value in values]
