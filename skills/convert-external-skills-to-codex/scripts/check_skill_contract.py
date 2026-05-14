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
    "## Core conversion rule",
    "## Mandatory user disclosure",
    "## Workflow",
    "## Rewrite rules",
    "## Output contract",
]

REQUIRED_README_SECTIONS = [
    "## Purpose",
    "## How To Run",
    "## Output Families",
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
