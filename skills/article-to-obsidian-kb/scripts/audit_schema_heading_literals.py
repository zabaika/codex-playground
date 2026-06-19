#!/usr/bin/env python3
"""Reject hardcoded rendered schema headings outside schema-owned artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from note_schema import HEADINGS


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

DEFAULT_SCAN_GLOBS = (
    "SKILL.md",
    "references/*.md",
    "scripts/*.py",
    "tests/*.py",
    "templates/*",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    heading_key: str
    heading_value: str

    def format(self) -> str:
        return (
            f"schema-heading-literal:{self.path}:L{self.line}: "
            f"rendered heading `{self.heading_value}` from `{self.heading_key}` "
            "must come from the schema loader or be referenced as a schema key"
        )


def _schema_headings() -> dict[str, str]:
    return {
        key: value
        for key, value in HEADINGS.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }


def _iter_scan_paths(skill_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in DEFAULT_SCAN_GLOBS:
        paths.extend(path for path in skill_dir.glob(pattern) if path.is_file())
    return sorted(set(paths))


def _is_allowed_rendered_heading_owner(path: Path, skill_dir: Path) -> bool:
    rel = path.relative_to(skill_dir)
    if rel.as_posix() == "config/note_schema.yaml":
        return True
    if len(rel.parts) >= 3 and rel.parts[0] == "tests" and rel.parts[1] == "fixtures":
        return True
    return False


def _line_has_rendered_heading_literal(line: str, heading_value: str) -> bool:
    return bool(re.search(rf"(?<![#\w]){re.escape(heading_value)}(?![#\w])", line))


def collect_findings(skill_dir: Path = SKILL_DIR) -> list[Finding]:
    skill_dir = skill_dir.resolve()
    headings = _schema_headings()
    findings: list[Finding] = []

    for path in _iter_scan_paths(skill_dir):
        if _is_allowed_rendered_heading_owner(path, skill_dir):
            continue
        rel = path.relative_to(skill_dir).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for key, value in headings.items():
                if _line_has_rendered_heading_literal(line, value):
                    findings.append(
                        Finding(
                            path=rel,
                            line=line_number,
                            heading_key=f"headings.{key}",
                            heading_value=value,
                        )
                    )
    return findings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill-dir",
        default=str(SKILL_DIR),
        help="article-to-obsidian-kb skill directory to audit",
    )
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    skill_dir = Path(args.skill_dir)
    if not skill_dir.exists():
        print(f"input-error: skill dir does not exist: {skill_dir}", file=sys.stderr)
        return 2

    findings = collect_findings(skill_dir)
    if args.json:
        print(json.dumps([finding.__dict__ for finding in findings], ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(finding.format())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
