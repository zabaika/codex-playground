#!/usr/bin/env python3
"""Audit expected graph terms for inline wikilinks in stable note body sections."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from note_schema import heading


EXCLUDED_SECTION_HEADINGS = {
    heading("evidence"),
    heading("additional_insights"),
    heading("observed_practices"),
    heading("related_notes"),
}


@dataclass(frozen=True)
class AuditTerm:
    title: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    code: str
    title: str
    line: int
    message: str

    def format(self) -> str:
        return f"{self.code}:{self.title}:L{self.line}: {self.message}"


def _load_terms_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.terms_file:
        return json.loads(Path(args.terms_file).read_text(encoding="utf-8"))
    if args.terms_json:
        return json.loads(args.terms_json)
    raise ValueError("Provide --terms-file or --terms-json")


def load_terms(args: argparse.Namespace) -> list[AuditTerm]:
    payload = _load_terms_payload(args)
    terms_value = payload.get("terms")
    if not isinstance(terms_value, list) or not terms_value:
        raise ValueError("Terms payload must contain a non-empty `terms` list")

    terms: list[AuditTerm] = []
    for index, item in enumerate(terms_value):
        if not isinstance(item, dict):
            raise ValueError(f"Term #{index + 1} must be an object")
        title = item.get("title")
        aliases = item.get("aliases", [])
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Term #{index + 1} must have a non-empty string `title`")
        if not isinstance(aliases, list):
            raise ValueError(f"Term #{index + 1} must have an `aliases` list")
        clean_aliases = [alias for alias in aliases if isinstance(alias, str) and alias.strip()]
        all_aliases = tuple(dict.fromkeys([title.strip(), *[alias.strip() for alias in clean_aliases]]))
        terms.append(AuditTerm(title=title.strip(), aliases=all_aliases))
    return terms


def _strip_frontmatter(lines: list[str]) -> list[tuple[int, str]]:
    if not lines or lines[0].strip() != "---":
        return [(index + 1, line) for index, line in enumerate(lines)]
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return [(line_number + 1, line) for line_number, line in enumerate(lines[index + 1 :], start=index + 1)]
    return [(index + 1, line) for index, line in enumerate(lines)]


def _heading_identity(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    level = len(match.group(1))
    return level, f"{match.group(1)} {match.group(2).strip()}"


def stable_body_lines(markdown: str) -> list[tuple[int, str]]:
    numbered_lines = _strip_frontmatter(markdown.splitlines())
    stable: list[tuple[int, str]] = []
    excluded_level: int | None = None

    for line_number, line in numbered_lines:
        heading_identity = _heading_identity(line)
        if heading_identity:
            level, heading_text = heading_identity
            if excluded_level is not None and level <= excluded_level:
                excluded_level = None
            if heading_text in EXCLUDED_SECTION_HEADINGS:
                excluded_level = level
                continue
        if excluded_level is None:
            stable.append((line_number, line))
    return stable


def _line_spans(pattern: re.Pattern[str], line: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in pattern.finditer(line)]


def _mask_spans(line: str, spans: list[tuple[int, int]]) -> str:
    chars = list(line)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def _mask_stable_lines(lines: list[tuple[int, str]], *, mask_inline_code: bool, mask_wikilinks: bool) -> list[tuple[int, str]]:
    masked: list[tuple[int, str]] = []
    in_fence = False
    fence_pattern = re.compile(r"^\s*(```|~~~)")
    wikilink_pattern = re.compile(r"\[\[[^\]]+\]\]")
    inline_code_pattern = re.compile(r"`[^`\n]+`")

    for line_number, line in lines:
        if fence_pattern.match(line):
            in_fence = not in_fence
            masked.append((line_number, " " * len(line)))
            continue
        if in_fence:
            masked.append((line_number, " " * len(line)))
            continue

        spans: list[tuple[int, int]] = []
        if mask_wikilinks:
            spans.extend(_line_spans(wikilink_pattern, line))
        if mask_inline_code:
            spans.extend(_line_spans(inline_code_pattern, line))
        masked.append((line_number, _mask_spans(line, spans)))
    return masked


def _wikilink_targets(lines: list[tuple[int, str]]) -> dict[str, list[int]]:
    targets: dict[str, list[int]] = {}
    masked_lines = _mask_stable_lines(lines, mask_inline_code=False, mask_wikilinks=False)
    wikilink_pattern = re.compile(r"\[\[([^\]]+)\]\]")

    for line_number, line in masked_lines:
        for match in wikilink_pattern.finditer(line):
            raw_target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            if raw_target:
                targets.setdefault(raw_target, []).append(line_number)
    return targets


def _contains_alias(line: str, alias: str) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)
    return bool(pattern.search(line))


def collect_findings(markdown: str, terms: list[AuditTerm]) -> list[Finding]:
    stable_lines = stable_body_lines(markdown)
    targets = _wikilink_targets(stable_lines)
    plain_lines = _mask_stable_lines(stable_lines, mask_inline_code=True, mask_wikilinks=True)
    code_lines = _mask_stable_lines(stable_lines, mask_inline_code=False, mask_wikilinks=True)
    inline_code_pattern = re.compile(r"`([^`\n]+)`")
    findings: list[Finding] = []

    for term in terms:
        has_body_link = term.title in targets
        first_plain: tuple[int, str] | None = None
        first_backticked: tuple[int, str] | None = None

        for line_number, line in plain_lines:
            for alias in term.aliases:
                if _contains_alias(line, alias):
                    first_plain = (line_number, alias)
                    break
            if first_plain:
                break

        for line_number, line in code_lines:
            for code_match in inline_code_pattern.finditer(line):
                code_text = code_match.group(1)
                for alias in term.aliases:
                    if code_text == alias:
                        first_backticked = (line_number, alias)
                        break
                if first_backticked:
                    break
            if first_backticked:
                break

        if first_backticked:
            line_number, alias = first_backticked
            findings.append(
                Finding(
                    code="backticked-concept",
                    title=term.title,
                    line=line_number,
                    message=f"alias `{alias}` appears in inline code; use [[{term.title}]] or an Obsidian alias instead",
                )
            )

        if first_plain and not has_body_link:
            line_number, alias = first_plain
            findings.append(
                Finding(
                    code="plain-wikilink-missing",
                    title=term.title,
                    line=line_number,
                    message=f"alias `{alias}` appears in stable body, but no body wikilink to [[{term.title}]] was found",
                )
            )

    return findings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--note", required=True, help="Markdown note to audit")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--terms-file", help="JSON file with audit terms")
    source.add_argument("--terms-json", help="Inline JSON payload with audit terms")
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        note_path = Path(args.note)
        if not note_path.exists():
            raise ValueError(f"Note does not exist: {note_path}")
        terms = load_terms(args)
        findings = collect_findings(note_path.read_text(encoding="utf-8"), terms)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([finding.__dict__ for finding in findings], ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(finding.format())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
