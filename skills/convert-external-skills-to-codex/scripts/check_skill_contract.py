#!/usr/bin/env python3
"""Mechanical contract checker for convert-external-skills-to-codex."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


EXPECTED_FILES = [
    "SKILL.md",
    "README.md",
    "install-local.sh",
    "agents/openai.yaml",
    "config/runtime.example.toml",
    "config/runtime.local.toml",
    "references/security-audit-checklist.md",
    "references/openai-surface-guidance.md",
    "references/test-matrix.md",
    "scripts/check_skill_contract.py",
    "scripts/check_conversion_fixtures.py",
    "tests/fixtures/manifest.json",
    "tests/test_conversion_fixture_regression.py",
]

REQUIRED_SKILL_SECTIONS = [
    "## File responsibilities",
    "## Supported output families",
    "## Codex-skill placement confirmation rules",
    "## Core conversion rule",
    "## Mandatory user disclosure",
    "## Functional-parity branch confirmation rules",
    "## Workflow",
    "## Rewrite rules",
    "## Output contract",
]

REQUIRED_README_SECTIONS = [
    "## Purpose",
    "## How To Run",
    "## Output Families",
    "## Output Placement Modes",
    "## Source Of Truth",
    "## Installation",
    "## Main Files",
    "## Maintenance",
    "## Troubleshooting",
]

REQUIRED_SECURITY_AUDIT_ANCHORS = [
    "This file owns only the source-risk audit layer:",
    "It does not own:",
    "## Handoff to the main workflow",
]

FORBIDDEN_SECURITY_AUDIT_RESTATEMENTS = [
    "## Rewrite rules",
    "## Output contract",
    "## Response Contract",
]

FORBIDDEN_SECURITY_AUDIT_AUTO_REPORT_ONLY_SNIPPETS = [
    "Stop direct conversion and recommend `conversion-report-only` when the source:",
]

REQUIRED_SURFACE_GUIDANCE_ANCHORS = [
    "This file owns only:",
    "## Output families in v1",
    "## Operational notes from current OpenAI docs",
    "## Official documentation anchors",
]

REQUIRED_SURFACE_GUIDANCE_LINKS = [
    "https://developers.openai.com/codex/guides/agents-md#layer-project-instructions",
    "https://developers.openai.com/codex/agent-approvals-security#sandbox-and-approvals",
    "https://developers.openai.com/codex/config-reference#configtoml",
    "https://help.openai.com/en/articles/10169521-using-projects-in-chatgpt",
    "https://developers.openai.com/api/docs/guides/latest-model.md",
]

FORBIDDEN_BUNDLE_ONLY_SKILL_SNIPPETS = [
    "apply placement confirmation only to `codex-skill` bundles",
    "If the chosen family is `codex-skill` and the conversion will emit a bundle",
    "If the conversion emits only one `codex-skill`, keep the existing normal conversion path unless the user explicitly requests a scratch-root-backed destination.",
]

FORBIDDEN_AUTO_REPORT_ONLY_SKILL_SNIPPETS = [
    "Switch to `conversion-report-only` and explain that the source belongs to a future specialized MCP or app-guidance converter.",
    "stop direct conversion and switch to `conversion-report-only`",
    "If the source is too broad to become safe in one pass, stop at `conversion-report-only`.",
]

REQUIRED_GENERAL_PLACEMENT_SKILL_SNIPPETS = [
    "Apply placement confirmation to every `codex-skill` conversion, whether it emits one skill or several.",
    "If the chosen family is `codex-skill`, explicitly confirm the placement mode unless the command already did so:",
]

FORBIDDEN_BUNDLE_ONLY_README_SNIPPETS = [
    "If the command does not say how a bundled `codex-skill` result should be placed, the skill should ask before converting.",
    "This choice applies only when the chosen output is a bundled `codex-skill` conversion.",
]

REQUIRED_GENERAL_PLACEMENT_README_SNIPPETS = [
    "If the command does not say how a `codex-skill` result should be placed, the skill should ask before converting.",
    "This choice applies when the chosen output is `codex-skill`, whether the result is one skill or several.",
]

REQUIRED_FUNCTIONAL_BRANCH_SKILL_SNIPPETS = [
    "Treat that as a functional-parity branch.",
    "Do not resolve such a branch by silently choosing the safest, narrowest, or easiest path even if you plan to disclose it later in the report.",
    "Record every functional-parity branch, the options presented, and the user's chosen path both:",
    "Do not enter this mode automatically when a functional-parity branch exists; the user must have selected it explicitly unless the user asked for `conversion-report-only` from the start.",
    "Every migration report must start with machine-readable YAML frontmatter for routine status fields that programs may need without bloating the human-facing body.",
    "Do not emit no-branch, no-collision, no-router-needed, or similar happy-path filler sections in the body. Record routine pass states only in report frontmatter.",
]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text()


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data, body


def check_expected_files() -> list[Violation]:
    violations: list[Violation] = []
    for rel in EXPECTED_FILES:
        if not (ROOT / rel).exists():
            violations.append(
                Violation(
                    f"package.missing-file:{rel}",
                    f"Отсутствует обязательный файл пакета `{rel}`.",
                )
            )
    return violations


def check_skill() -> list[Violation]:
    text = read_text("SKILL.md")
    frontmatter, body = split_frontmatter(text)
    violations: list[Violation] = []
    for key in ("name", "description", "allowed-tools"):
        if key not in frontmatter or not frontmatter[key]:
            violations.append(
                Violation(
                    f"frontmatter.missing:{key}",
                    f"Во frontmatter `SKILL.md` отсутствует поле `{key}`.",
                )
            )
    if frontmatter.get("allowed-tools") != "Read Write":
        violations.append(
            Violation(
                "frontmatter.invalid-allowed-tools",
                "В `SKILL.md` ожидается `allowed-tools: Read Write`.",
            )
        )
    for section in REQUIRED_SKILL_SECTIONS:
        if section not in body:
            violations.append(
                Violation(
                    f"skill.missing-section:{section}",
                    f"В `SKILL.md` отсутствует обязательный раздел `{section}`.",
                )
            )
    for snippet in REQUIRED_GENERAL_PLACEMENT_SKILL_SNIPPETS:
        if snippet not in body:
            violations.append(
                Violation(
                    f"skill.missing-general-placement-snippet:{snippet}",
                    "В `SKILL.md` отсутствует обязательная общая формулировка placement-правила для любого `codex-skill` результата.",
                )
            )
    for snippet in REQUIRED_FUNCTIONAL_BRANCH_SKILL_SNIPPETS:
        if snippet not in body:
            violations.append(
                Violation(
                    f"skill.missing-functional-branch-snippet:{snippet}",
                    "В `SKILL.md` отсутствует обязательная формулировка про user-confirmed functional-parity branches.",
                )
            )
    for snippet in FORBIDDEN_BUNDLE_ONLY_SKILL_SNIPPETS:
        if snippet in body:
            violations.append(
                Violation(
                    f"skill.forbidden-bundle-only-snippet:{snippet}",
                    "В `SKILL.md` осталась устаревшая bundle-only формулировка placement-правила.",
                )
            )
    for snippet in FORBIDDEN_AUTO_REPORT_ONLY_SKILL_SNIPPETS:
        if snippet in body:
            violations.append(
                Violation(
                    f"skill.forbidden-auto-report-only-snippet:{snippet}",
                    "В `SKILL.md` осталась формулировка, разрешающая автоматический уход в `conversion-report-only` без user-choice.",
                )
            )
    return violations


def check_readme() -> list[Violation]:
    text = read_text("README.md")
    violations: list[Violation] = []
    for section in REQUIRED_README_SECTIONS:
        if section not in text:
            violations.append(
                Violation(
                    f"readme.missing-section:{section}",
                    f"В `README.md` отсутствует раздел `{section}`.",
                )
            )
    if "For the detailed conversion contract, use `SKILL.md` as the canonical owner." not in text:
        violations.append(
            Violation(
                "readme.missing-canonical-owner-pointer",
                "В `README.md` нет явного указателя на `SKILL.md` как на канонического владельца контракта.",
            )
        )
    for marker in ("CODEX_PLAYGROUND_PROJECT_ROOT", "paths.scratch_root", "config/runtime.local.toml"):
        if marker not in text:
            violations.append(
                Violation(
                    f"readme.missing-runtime-path-marker:{marker}",
                    f"В `README.md` отсутствует обязательный runtime-path marker `{marker}`.",
                )
            )
    for snippet in REQUIRED_GENERAL_PLACEMENT_README_SNIPPETS:
        if snippet not in text:
            violations.append(
                Violation(
                    f"readme.missing-general-placement-snippet:{snippet}",
                    "В `README.md` отсутствует обязательная operator-facing формулировка placement-правила для любого `codex-skill` результата.",
                )
            )
    for snippet in FORBIDDEN_BUNDLE_ONLY_README_SNIPPETS:
        if snippet in text:
            violations.append(
                Violation(
                    f"readme.forbidden-bundle-only-snippet:{snippet}",
                    "В `README.md` осталась устаревшая bundle-only формулировка placement-правила.",
                )
            )
    return violations


def check_security_audit() -> list[Violation]:
    text = read_text("references/security-audit-checklist.md")
    violations: list[Violation] = []
    for anchor in REQUIRED_SECURITY_AUDIT_ANCHORS:
        if anchor not in text:
            violations.append(
                Violation(
                    f"security-audit.missing-anchor:{anchor}",
                    f"В `security-audit-checklist.md` отсутствует опорный якорь `{anchor}`.",
                )
            )
    for marker in FORBIDDEN_SECURITY_AUDIT_RESTATEMENTS:
        if marker in text:
            violations.append(
                Violation(
                    f"security-audit.forbidden-restatement:{marker}",
                    f"`security-audit-checklist.md` снова содержит чужой rule family `{marker}`.",
                )
            )
    for snippet in FORBIDDEN_SECURITY_AUDIT_AUTO_REPORT_ONLY_SNIPPETS:
        if snippet in text:
            violations.append(
                Violation(
                    f"security-audit.forbidden-auto-report-only-snippet:{snippet}",
                    "`security-audit-checklist.md` не должен молча уводить в `conversion-report-only` без user-choice.",
                )
            )
    return violations


def check_surface_guidance() -> list[Violation]:
    text = read_text("references/openai-surface-guidance.md")
    violations: list[Violation] = []
    for anchor in REQUIRED_SURFACE_GUIDANCE_ANCHORS:
        if anchor not in text:
            violations.append(
                Violation(
                    f"surface-guidance.missing-anchor:{anchor}",
                    f"В `openai-surface-guidance.md` отсутствует опорный якорь `{anchor}`.",
                )
            )
    for link in REQUIRED_SURFACE_GUIDANCE_LINKS:
        if link not in text:
            violations.append(
                Violation(
                    f"surface-guidance.missing-doc-link:{link}",
                    f"В `openai-surface-guidance.md` отсутствует обязательная опорная ссылка `{link}`.",
                )
            )
    return violations


def check_openai_yaml() -> list[Violation]:
    text = read_text("agents/openai.yaml")
    violations: list[Violation] = []
    if "interface:" not in text:
        violations.append(
            Violation(
                "openai-yaml.missing-interface",
                "В `agents/openai.yaml` отсутствует блок `interface:`.",
            )
        )
    for key in ("display_name:", "short_description:"):
        if key not in text:
            violations.append(
                Violation(
                    f"openai-yaml.missing-field:{key}",
                    f"В `agents/openai.yaml` отсутствует поле `{key}`.",
                )
            )
    return violations


def check_install_script() -> list[Violation]:
    text = read_text("install-local.sh")
    violations: list[Violation] = []
    if "convert-external-skills-to-codex" not in text:
        violations.append(
            Violation(
                "install.missing-target-path",
                "В `install-local.sh` не найден target path skill-пакета.",
            )
        )
    if "cp -R" not in text:
        violations.append(
            Violation(
                "install.missing-copy-step",
                "В `install-local.sh` не найден шаг копирования skill-пакета.",
            )
        )
    if "config/runtime.local.toml" not in text or "ln -s" not in text:
        violations.append(
            Violation(
                "install.missing-runtime-local-link",
                "В `install-local.sh` ожидается symlink installed `config/runtime.local.toml` обратно на repository copy.",
            )
        )
    return violations


def main() -> int:
    violations: list[Violation] = []
    violations.extend(check_expected_files())
    violations.extend(check_skill())
    violations.extend(check_readme())
    violations.extend(check_security_audit())
    violations.extend(check_surface_guidance())
    violations.extend(check_openai_yaml())
    violations.extend(check_install_script())

    if violations:
        for violation in violations:
            print(f"{violation.code}: {violation.message}")
        return 1

    print("OK: convert-external-skills-to-codex contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
