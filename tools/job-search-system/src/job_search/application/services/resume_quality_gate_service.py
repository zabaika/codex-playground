from __future__ import annotations

import re
from typing import Any


class ResumeQualityGateService:
    def check_markdown(
        self,
        *,
        markdown: str,
        candidate_profile: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        issues: list[dict[str, object]] = []
        lines = [line.strip() for line in markdown.splitlines()]
        nonempty = [line for line in lines if line]
        first_heading = nonempty[0] if nonempty else ""
        if not first_heading.startswith("# "):
            issues.append(self._issue("missing_title_heading", "fail", "Resume must start with a top-level title heading."))
        elif first_heading in {"# Candidate", "# Кандидат"} or re.fullmatch(r"# Candidate [A-Z]", first_heading):
            issues.append(self._issue("generic_title_heading", "fail", "Resume title must contain the candidate name."))

        if not re.search(r"[\w.+-]+@[\w.-]+\.\w+", markdown) and "linkedin.com" not in markdown.lower():
            issues.append(
                self._issue(
                    "missing_contact_channel",
                    "fail",
                    "Resume must contain at least one external contact channel such as email or public profile URL.",
                )
            )

        if "## Experience" not in markdown and "## Опыт" not in markdown:
            if "## Summary" not in markdown and "## Профиль" not in markdown:
                issues.append(
                    self._issue(
                        "missing_summary_and_experience",
                        "fail",
                        "Resume must contain at least a summary or experience section.",
                    )
                )

        if "TODO" in markdown or "None" in markdown or "null" in markdown:
            issues.append(
                self._issue(
                    "placeholder_content_detected",
                    "warn",
                    "Resume still contains placeholder-like content or unresolved null markers.",
                )
            )

        if re.search(r"(?im)^\s*Page\s+\d+\s+of\s+\d+\s*$", markdown):
            issues.append(
                self._issue(
                    "pdf_page_marker_detected",
                    "warn",
                    "Resume contains PDF extraction page markers that should be removed before external use.",
                )
            )

        duplicate_bullets = self._duplicate_bullet_lines(lines)
        if duplicate_bullets:
            issues.append(
                self._issue(
                    "duplicate_bullets_detected",
                    "warn",
                    "Resume contains repeated bullet lines that look like deterministic extraction noise.",
                    details={"examples": duplicate_bullets[:3]},
                )
            )

        misparsed_headings = self._misparsed_experience_headings(lines)
        if misparsed_headings:
            issues.append(
                self._issue(
                    "misparsed_experience_entries",
                    "warn",
                    "Resume contains experience headings that look like extraction noise rather than role/company entries.",
                    details={"examples": misparsed_headings[:3]},
                )
            )

        if candidate_profile:
            targets = candidate_profile.get("targets", {}).get("target_roles", [])
            if targets and not any(role and str(role) in markdown for role in targets[:3]):
                issues.append(
                    self._issue(
                        "target_role_not_visible",
                        "warn",
                        "Candidate has target roles, but none of the primary target roles are visible in the generated resume.",
                    )
                )

        status = "pass"
        if any(issue["severity"] == "fail" for issue in issues):
            status = "fail"
        elif issues:
            status = "warn"
        return {"status": status, "issues": issues}

    def check_application_message(
        self,
        *,
        markdown: str,
        target_role: str | None = None,
        target_company: str | None = None,
    ) -> dict[str, object]:
        issues: list[dict[str, object]] = []
        nonempty = [line.strip() for line in markdown.splitlines() if line.strip()]
        if not nonempty or not nonempty[0].startswith("# "):
            issues.append(self._issue("missing_title_heading", "fail", "Application draft must start with a title heading."))
        if len(nonempty) < 4:
            issues.append(self._issue("too_short", "fail", "Application draft is too short to be useful."))
        lowered = markdown.lower()
        if target_role and target_role.lower() not in lowered:
            issues.append(
                self._issue(
                    "target_role_not_visible",
                    "warn",
                    "Application draft does not mention the target role explicitly.",
                )
            )
        if target_company and target_company.lower() not in lowered:
            issues.append(
                self._issue(
                    "target_company_not_visible",
                    "warn",
                    "Application draft does not mention the target company explicitly.",
                )
            )
        if "your company" in lowered:
            issues.append(
                self._issue(
                    "generic_company_placeholder",
                    "fail",
                    "Application draft still uses a generic company placeholder instead of the target company.",
                )
            )
        if "todo" in lowered or "null" in lowered or "none" in lowered:
            issues.append(
                self._issue(
                    "placeholder_content_detected",
                    "warn",
                    "Application draft still contains placeholder-like content or unresolved null markers.",
                )
            )
        if re.search(r"\bCandidate [A-Z]\b", markdown):
            issues.append(
                self._issue(
                    "generic_candidate_name",
                    "fail",
                    "Application draft still uses a generic candidate placeholder instead of the candidate name.",
                )
            )
        status = "pass"
        if any(issue["severity"] == "fail" for issue in issues):
            status = "fail"
        elif issues:
            status = "warn"
        return {"status": status, "issues": issues}

    def _duplicate_bullet_lines(self, lines: list[str]) -> list[str]:
        counts: dict[str, int] = {}
        for line in lines:
            normalized = re.sub(r"\s+", " ", line.strip())
            if not normalized.startswith(("- ", "* ")):
                continue
            if len(normalized) < 24:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
        return [line for line, count in counts.items() if count > 1]

    def _misparsed_experience_headings(self, lines: list[str]) -> list[str]:
        headings = []
        for line in lines:
            normalized = re.sub(r"\s+", " ", line.strip())
            if not normalized.startswith("### "):
                continue
            heading = normalized.removeprefix("### ").strip()
            if re.fullmatch(r"Page \d+ of \d+", heading, flags=re.IGNORECASE):
                headings.append(normalized)
                continue
            if len(heading.split()) > 18:
                headings.append(normalized)
                continue
            if heading.lower() in {"experience", "опыт работы", "work experience"}:
                headings.append(normalized)
        return headings

    def _issue(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        issue: dict[str, object] = {"code": code, "severity": severity, "message": message}
        if details:
            issue["details"] = details
        return issue
