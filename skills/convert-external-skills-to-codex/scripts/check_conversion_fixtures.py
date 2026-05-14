#!/usr/bin/env python3
"""Regression checker for converted output fixtures."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests/fixtures/manifest.json"
VENDOR_RESIDUE = [
    "Claude",
    "Anthropic",
    "allowed-tools:",
    "Upload this file to a Claude project",
    "paste into Claude",
]


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


def load_manifest() -> list[dict[str, object]]:
    data = json.loads(MANIFEST_PATH.read_text())
    return data["fixtures"]


def count_code_fences(text: str) -> int:
    return text.count("```")


def check_no_vendor_residue(text: str, label: str) -> list[Violation]:
    violations: list[Violation] = []
    for needle in VENDOR_RESIDUE:
        if needle in text:
            violations.append(
                Violation(
                    f"{label}.vendor-residue:{needle}",
                    f"В `{label}` найден vendor residue `{needle}`.",
                )
            )
    return violations


def check_file_exists(path: Path, code: str) -> list[Violation]:
    if path.exists():
        return []
    return [Violation(code, f"Отсутствует обязательный fixture-файл `{path}`.")]


def check_project_pack(fixture: dict[str, object]) -> list[Violation]:
    base = ROOT / str(fixture["path"])
    handbook = base / str(fixture.get("handbook_file", "handbook.md"))
    runtime = base / str(fixture.get("runtime_file", "project-instructions.md"))
    report = base / str(fixture.get("report_file", "conversion-report.md"))
    examples = base / str(fixture.get("examples_file", "examples-pack.md"))
    violations: list[Violation] = []
    violations.extend(check_file_exists(handbook, f"{fixture['name']}.missing:handbook"))
    violations.extend(check_file_exists(runtime, f"{fixture['name']}.missing:runtime"))
    violations.extend(check_file_exists(report, f"{fixture['name']}.missing:report"))
    if fixture.get("requires_examples"):
        violations.extend(check_file_exists(examples, f"{fixture['name']}.missing:examples"))
    if violations:
        return violations

    handbook_text = handbook.read_text()
    runtime_text = runtime.read_text()
    report_text = report.read_text()
    examples_text = examples.read_text() if examples.exists() else ""

    for anchor in (
        "## How to use this handbook",
        "## When not to use this handbook",
        "## Module router",
    ):
        if anchor not in handbook_text:
            violations.append(
                Violation(
                    f"{fixture['name']}.handbook.missing-anchor:{anchor}",
                    f"В handbook fixture `{fixture['name']}` отсутствует `{anchor}`.",
                )
            )

    for anchor in (
        "### Routing",
        "### Minimum blocking questions",
        "### Freshness-sensitive triggers",
    ):
        if anchor not in runtime_text:
            violations.append(
                Violation(
                    f"{fixture['name']}.runtime.missing-anchor:{anchor}",
                    f"В runtime fixture `{fixture['name']}` отсутствует `{anchor}`.",
                )
            )

    if "primary reference" not in runtime_text:
        violations.append(
            Violation(
                f"{fixture['name']}.runtime.missing-handbook-pointer",
                f"Runtime fixture `{fixture['name']}` не указывает на handbook как на primary reference.",
            )
        )

    handbook_name = str(fixture.get("handbook_file", "handbook.md"))
    examples_name = str(fixture.get("examples_file", "examples-pack.md"))
    if handbook_name not in runtime_text:
        violations.append(
            Violation(
                f"{fixture['name']}.runtime.missing-handbook-filename",
                f"Runtime fixture `{fixture['name']}` не указывает точное имя handbook-файла `{handbook_name}`.",
            )
        )
    if fixture.get("requires_examples") and examples_name not in runtime_text:
        violations.append(
            Violation(
                f"{fixture['name']}.runtime.missing-examples-filename",
                f"Runtime fixture `{fixture['name']}` не указывает точное имя examples-файла `{examples_name}`.",
            )
        )

    for anchor in (
        "### Selected output family",
        "### Package contents",
        "### What was removed",
        "### What was substantially adapted",
    ):
        if anchor not in report_text:
            violations.append(
                Violation(
                    f"{fixture['name']}.report.missing-anchor:{anchor}",
                    f"В report fixture `{fixture['name']}` отсутствует `{anchor}`.",
                )
            )

    if "`chatgpt-project-pack`" not in report_text:
        violations.append(
            Violation(
                f"{fixture['name']}.report.invalid-family",
                f"В report fixture `{fixture['name']}` не зафиксирован `chatgpt-project-pack`.",
            )
        )

    if examples.exists() and count_code_fences(examples_text) < 2:
        violations.append(
            Violation(
                f"{fixture['name']}.examples.too-thin",
                f"Examples fixture `{fixture['name']}` выглядит слишком тонким для regression-образца.",
            )
        )

    for label, text in (
        ("handbook", handbook_text),
        ("runtime", runtime_text),
        ("examples", examples_text),
    ):
        violations.extend(check_no_vendor_residue(text, f"{fixture['name']}.{label}"))

    return violations


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


def check_codex_skill(fixture: dict[str, object]) -> list[Violation]:
    base = ROOT / str(fixture["path"])
    skill = base / str(fixture.get("skill_file", "SKILL.md"))
    report = base / str(fixture.get("report_file", "conversion-report.md"))
    violations: list[Violation] = []
    violations.extend(check_file_exists(skill, f"{fixture['name']}.missing:SKILL"))
    violations.extend(check_file_exists(report, f"{fixture['name']}.missing:report"))
    for ref in fixture.get("reference_files", []):
        violations.extend(
            check_file_exists(base / str(ref), f"{fixture['name']}.missing:{ref}")
        )
    if fixture.get("requires_openai_yaml"):
        violations.extend(
            check_file_exists(
                base / "agents/openai.yaml",
                f"{fixture['name']}.missing:agents/openai.yaml",
            )
        )
    if violations:
        return violations

    skill_text = skill.read_text()
    frontmatter, body = split_frontmatter(skill_text)
    report_text = report.read_text()

    for key in ("name", "description"):
        if key not in frontmatter or not frontmatter[key]:
            violations.append(
                Violation(
                    f"{fixture['name']}.skill.missing-frontmatter:{key}",
                    f"В fixture `{fixture['name']}` отсутствует frontmatter поле `{key}`.",
                )
            )

    for anchor in (
        "## Overview",
        "## When To Use",
        "## When Not To Use",
        "## Workflow",
        "## Response Contract",
        "## Reference",
    ):
        if anchor not in body:
            violations.append(
                Violation(
                    f"{fixture['name']}.skill.missing-anchor:{anchor}",
                    f"В SKILL fixture `{fixture['name']}` отсутствует `{anchor}`.",
                )
            )

    if "broad prompting handbook" in skill_text.lower():
        pass

    if "`codex-skill`" not in report_text:
        violations.append(
            Violation(
                f"{fixture['name']}.report.invalid-family",
                f"В report fixture `{fixture['name']}` не зафиксирован `codex-skill`.",
            )
        )

    for anchor in (
        "## Selected Output Family",
        "## What Was Substantially Adapted",
        "## What Was Removed",
    ):
        if anchor not in report_text:
            violations.append(
                Violation(
                    f"{fixture['name']}.report.missing-anchor:{anchor}",
                    f"В report fixture `{fixture['name']}` отсутствует `{anchor}`.",
                )
            )

    violations.extend(check_no_vendor_residue(skill_text, f"{fixture['name']}.skill"))

    for ref in fixture.get("reference_files", []):
        ref_path = base / str(ref)
        ref_text = ref_path.read_text()
        if str(ref) not in skill_text:
            violations.append(
                Violation(
                    f"{fixture['name']}.skill.missing-reference-link:{ref}",
                    f"SKILL fixture `{fixture['name']}` не ссылается на `{ref}`.",
                )
            )
        min_fences = int(fixture.get("reference_min_code_fences", 0))
        if min_fences and count_code_fences(ref_text) < min_fences:
            violations.append(
                Violation(
                    f"{fixture['name']}.reference.too-thin:{ref}",
                    f"Reference fixture `{fixture['name']}` должен содержать минимум {min_fences} code fences.",
                )
            )
        violations.extend(
            check_no_vendor_residue(ref_text, f"{fixture['name']}.{ref_path.name}")
        )

    if fixture.get("requires_openai_yaml"):
        yaml_text = (base / "agents/openai.yaml").read_text()
        if "interface:" not in yaml_text:
            violations.append(
                Violation(
                    f"{fixture['name']}.openai-yaml.missing-interface",
                    f"В fixture `{fixture['name']}` отсутствует блок `interface:` в openai.yaml.",
                )
            )

    return violations


def check_all() -> list[Violation]:
    violations: list[Violation] = []
    for fixture in load_manifest():
        family = fixture["family"]
        if family == "chatgpt-project-pack":
            violations.extend(check_project_pack(fixture))
        elif family == "codex-skill":
            violations.extend(check_codex_skill(fixture))
        else:
            violations.append(
                Violation(
                    f"{fixture['name']}.unknown-family",
                    f"Неизвестный fixture family `{family}`.",
                )
            )
    return violations


def main() -> int:
    violations = check_all()
    if violations:
        for violation in violations:
            print(f"{violation.code}: {violation.message}")
        return 1
    print("OK: conversion fixtures passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
