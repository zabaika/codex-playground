#!/usr/bin/env python3
"""Contract checker for article-to-obsidian-kb note fixtures."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


SOURCE_NOTE_TYPES = {"lessons", "general", "operating-model"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")
URL_RE = re.compile(r"https?://\S+")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
LATIN_TOKEN_RE = re.compile(
    r"[A-Za-z]+(?:[/-][A-Za-z0-9]+)*|[A-Za-z]+(?:[/-][A-Za-z0-9]+)*-[А-Яа-яЁё]+|[А-Яа-яЁё]+-[A-Za-z]+(?:[/-][A-Za-z0-9]+)*"
)


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    line: int | None = None


def _split_frontmatter(text: str) -> tuple[str | None, str, int]:
    if not text.startswith("---\n"):
        return None, text, 0
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text, 0
    block = text[4:end]
    frontmatter_lines = block.count("\n") + 2
    return block, text[end + 5 :], frontmatter_lines


def _parse_frontmatter_block(block: str | None) -> dict[str, object]:
    if block is None:
        return {}
    data: dict[str, object] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if re.match(r"^[A-Za-z0-9_-]+:\s*$", line):
            key = line.split(":", 1)[0].strip()
            values: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith("  - "):
                values.append(lines[i][4:].strip())
                i += 1
            data[key] = values
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
        i += 1
    return data


def _body_line_number(body: str, needle: str) -> int | None:
    idx = body.find(needle)
    if idx == -1:
        return None
    return body.count("\n", 0, idx) + 1


def _offset_violations(
    violations: list[Violation], line_offset: int
) -> list[Violation]:
    return [
        Violation(
            violation.code,
            violation.message,
            None if violation.line is None else violation.line + line_offset,
        )
        for violation in violations
    ]


def _extract_headings(body: str) -> list[tuple[int, str, int]]:
    headings: list[tuple[int, str, int]] = []
    for idx, line in enumerate(body.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if match:
            headings.append((idx, match.group(0), len(match.group(1))))
    return headings


def _find_heading_block(body: str, heading: str) -> tuple[int | None, list[str]]:
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            return idx + 1, lines[idx + 1 :]
    return None, []


def _closing_links_count(body: str) -> tuple[int | None, int]:
    line_no, tail = _find_heading_block(body, "# Связанные заметки")
    if line_no is None:
        return None, 0
    count = 0
    for line in tail:
        if line.startswith("# "):
            break
        if line.strip().startswith("[[") and line.strip().endswith("]]"):
            count += 1
    return line_no, count


def _extract_wikilink_targets(text: str) -> list[str]:
    return [match.group(1) for match in WIKILINK_RE.finditer(text)]


def _visible_text(line: str) -> str:
    line = URL_RE.sub("", line)
    line = WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), line)
    line = INLINE_CODE_RE.sub("", line)
    return line.replace("**", "").replace("*", "")


def _is_allowed_latin_token(token: str, allow_terms: set[str]) -> bool:
    if token in allow_terms or token.lower() in allow_terms:
        return True
    if token.isupper() and len(token) <= 5:
        return True
    if token in {"AI", "CPO", "JTBD", "PRD", "CSV"}:
        return True
    return False


def _check_required_frontmatter(
    frontmatter: dict[str, object], expect: str
) -> list[Violation]:
    violations: list[Violation] = []
    required = ["title", "type", "tags"]
    if expect == "source":
        required += ["source", "date"]
    for key in required:
        value = frontmatter.get(key)
        if value in (None, "", []):
            violations.append(
                Violation(
                    f"frontmatter.missing-{key}",
                    f"Во frontmatter отсутствует обязательное поле `{key}`.",
                    1,
                )
            )
    note_type = frontmatter.get("type")
    if expect == "concept" and note_type != "concept":
        violations.append(
            Violation(
                "frontmatter.invalid-type",
                "Concept note должен иметь `type: concept`.",
                1,
            )
        )
    if expect == "source" and note_type not in SOURCE_NOTE_TYPES:
        violations.append(
            Violation(
                "frontmatter.invalid-type",
                "Source-derived note должен иметь `type: lessons|general|operating-model`.",
                1,
            )
        )
    return violations


def _check_tag_rules(frontmatter: dict[str, object], expect: str) -> list[Violation]:
    violations: list[Violation] = []
    tags = frontmatter.get("tags")
    if not isinstance(tags, list):
        return violations
    min_tags = 4 if expect == "source" else 1
    max_tags = 8
    if not (min_tags <= len(tags) <= max_tags):
        violations.append(
            Violation(
                "frontmatter.invalid-tag-count",
                f"Количество тегов должно быть в диапазоне {min_tags}-{max_tags}, сейчас {len(tags)}.",
                1,
            )
        )
    for tag in tags:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tag):
            violations.append(
                Violation(
                    f"frontmatter.invalid-tag:{tag}",
                    f"Тег `{tag}` должен быть в lowercase kebab-case и только на английском.",
                    1,
                )
            )
    return violations


def _check_title_matches_filename(
    path: Path, frontmatter: dict[str, object]
) -> list[Violation]:
    title = frontmatter.get("title")
    if not isinstance(title, str):
        return []
    if path.stem != title:
        return [
            Violation(
                "frontmatter.title-filename-mismatch",
                "Filename должен совпадать с `title`.",
                1,
            )
        ]
    return []


def _check_intro_before_first_heading(body: str) -> list[Violation]:
    for idx, line in enumerate(body.splitlines(), start=1):
        if not line.strip():
            continue
        if line.startswith("#"):
            return [
                Violation(
                    "structure.missing-intro-before-first-heading",
                    "У source note должен быть вводный абзац до первого heading.",
                    idx,
                )
            ]
        return []
    return []


def _check_duplicate_headings(body: str) -> list[Violation]:
    violations: list[Violation] = []
    seen: dict[str, int] = {}
    for line_no, heading, _level in _extract_headings(body):
        if heading in seen:
            violations.append(
                Violation(
                    f"structure.duplicate-heading:{heading}",
                    f"Heading `{heading}` повторяется в заметке.",
                    line_no,
                )
            )
        else:
            seen[heading] = line_no
    return violations


def _check_required_headings(body: str, required_headings: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    lines = set(line.strip() for line in body.splitlines())
    for heading in required_headings:
        if heading not in lines:
            violations.append(
                Violation(
                    f"structure.missing-heading:{heading}",
                    f"В заметке отсутствует обязательный heading `{heading}`.",
                )
            )
    return violations


def _check_forbidden_headings(body: str, forbidden_headings: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for heading in forbidden_headings:
        line = _body_line_number(body, heading)
        if line is not None:
            violations.append(
                Violation(
                    f"structure.forbidden-heading:{heading}",
                    f"Heading `{heading}` не должен использоваться в этой форме.",
                    line,
                )
            )
    return violations


def _check_blank_lines_after_headings(body: str) -> list[Violation]:
    violations: list[Violation] = []
    lines = body.splitlines()
    for idx, line in enumerate(lines[:-1]):
        if line.startswith("#") and lines[idx + 1].strip() == "":
            violations.append(
                Violation(
                    "spacing.blank-line-after-heading",
                    "После heading не должно быть пустой строки.",
                    idx + 2,
                )
            )
    return violations


def _check_blank_lines_before_lists(body: str) -> list[Violation]:
    violations: list[Violation] = []
    lines = body.splitlines()
    for idx in range(1, len(lines)):
        if lines[idx].startswith("- ") and lines[idx - 1].strip() == "":
            violations.append(
                Violation(
                    "spacing.blank-line-before-list",
                    "Перед списком не должно быть пустой строки.",
                    idx + 1,
                )
            )
    return violations


def _check_double_blank_lines(body: str) -> list[Violation]:
    violations: list[Violation] = []
    lines = body.splitlines()
    for idx in range(len(lines) - 1):
        if lines[idx].strip() == "" and lines[idx + 1].strip() == "":
            violations.append(
                Violation(
                    "spacing.double-blank-line",
                    "В заметке не должно быть двойных пустых строк.",
                    idx + 1,
                )
            )
    return violations


def _check_forbidden_terms(body: str, forbidden_terms: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for term in forbidden_terms:
        line = _body_line_number(body, term)
        if line is not None:
            violations.append(
                Violation(
                    f"language.forbidden-term:{term}",
                    f"Остался запрещённый термин `{term}`.",
                    line,
                )
            )
    return violations


def _check_generic_latin_residue(
    body: str, allow_latin_terms: list[str]
) -> list[Violation]:
    violations: list[Violation] = []
    allow_terms = {term for term in allow_latin_terms}
    allow_terms.update(term.lower() for term in allow_latin_terms)
    for idx, line in enumerate(body.splitlines(), start=1):
        visible = _visible_text(line)
        for match in LATIN_TOKEN_RE.finditer(visible):
            token = match.group(0)
            if _is_allowed_latin_token(token, allow_terms):
                continue
            violations.append(
                Violation(
                    f"language.unexpected-latin:{token}",
                    f"В тексте остался неожиданный латинский или гибридный токен `{token}` вне allowlist.",
                    idx,
                )
            )
    return violations


def _check_required_linked_phrases(
    body: str, required_linked_phrases: list[str]
) -> list[Violation]:
    violations: list[Violation] = []
    for phrase in required_linked_phrases:
        linked_patterns = (f"[[{phrase}]]", f"|{phrase}]]")
        if any(pattern in body for pattern in linked_patterns):
            continue
        line = _body_line_number(body, phrase)
        if line is not None:
            violations.append(
                Violation(
                    f"links.unlinked-phrase:{phrase}",
                    f"Фраза `{phrase}` должна быть wikilink, а не plain text.",
                    line,
                )
            )
    return violations


def _check_required_examples(body: str, required_example_phrases: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for phrase in required_example_phrases:
        line = _body_line_number(body, phrase)
        if line is None:
            violations.append(
                Violation(
                    f"examples.missing:{phrase}",
                    f"В заметке потерян важный пример или кейс `{phrase}`.",
                )
            )
    return violations


def _check_bold_leading_bullets(
    body: str,
    enforce_leading_bold_under: list[str],
    leading_bold_threshold: int,
) -> list[Violation]:
    violations: list[Violation] = []
    headings = _extract_headings(body)
    lines = body.splitlines()
    target_headings = set(enforce_leading_bold_under)
    for idx, (line_no, heading, _level) in enumerate(headings):
        if heading not in target_headings:
            continue
        end_line = headings[idx + 1][0] - 1 if idx + 1 < len(headings) else len(lines)
        for current_line in range(line_no + 1, end_line + 1):
            line = lines[current_line - 1]
            if not line.startswith("- "):
                continue
            visible = _visible_text(line)
            if len(visible.strip()) < leading_bold_threshold:
                continue
            if not line.startswith("- **"):
                violations.append(
                    Violation(
                        f"emphasis.missing-leading-bold:{heading}",
                        f"Длинный bullet под `{heading}` должен начинаться с bold-leading clause.",
                        current_line,
                    )
                )
    return violations


def _check_required_related_links(
    body: str, required_related_links: list[str]
) -> list[Violation]:
    violations: list[Violation] = []
    _, tail = _find_heading_block(body, "# Связанные заметки")
    tail_text = "\n".join(tail)
    for target in required_related_links:
        if f"[[{target}]]" not in tail_text:
            violations.append(
                Violation(
                    f"closing.missing-related-link:{target}",
                    f"В closing section отсутствует обязательная ссылка `[[{target}]]`.",
                )
            )
    return violations


def _check_related_links_dedup(body: str) -> list[Violation]:
    violations: list[Violation] = []
    heading_line, tail = _find_heading_block(body, "# Связанные заметки")
    if heading_line is None:
        return violations
    body_before_closing = body.split("# Связанные заметки", 1)[0]
    inline_targets = set(_extract_wikilink_targets(body_before_closing))
    for offset, line in enumerate(tail, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            break
        match = WIKILINK_RE.fullmatch(stripped)
        if not match:
            continue
        target = match.group(1)
        if target in inline_targets:
            violations.append(
                Violation(
                    f"closing.duplicate-inline-link:{target}",
                    f"Ссылка `[[{target}]]` уже есть в теле заметки и не должна механически повторяться в `# Связанные заметки`.",
                    heading_line + offset,
                )
            )
    return violations


def _check_closing_section(
    body: str,
    min_related_links: int,
    require_related_section_final: bool,
    required_related_links: list[str],
) -> list[Violation]:
    violations: list[Violation] = []
    heading_line, links_count = _closing_links_count(body)
    if heading_line is None:
        for target in required_related_links:
            violations.append(
                Violation(
                    f"closing.missing-related-link:{target}",
                    f"В closing section отсутствует обязательная ссылка `[[{target}]]`.",
                )
            )
        return violations
    if links_count == 0:
        violations.append(
            Violation(
                "closing.empty-related-section",
                "Пустой блок `# Связанные заметки` нужно удалить целиком, а не оставлять пустой heading.",
                heading_line,
            )
        )
    elif min_related_links and links_count < min_related_links:
        violations.append(
            Violation(
                "closing.too-few-related-links",
                f"В `# Связанные заметки` найдено только {links_count} ссылок, нужно минимум {min_related_links}.",
                heading_line,
            )
        )
    if require_related_section_final:
        headings = _extract_headings(body)
        if headings and headings[-1][1] != "# Связанные заметки":
            violations.append(
                Violation(
                    "closing.related-section-not-final",
                    "Блок `# Связанные заметки` должен быть последним heading в заметке.",
                    heading_line,
                )
            )
    violations.extend(_check_required_related_links(body, required_related_links))
    violations.extend(_check_related_links_dedup(body))
    return violations


def collect_violations(
    path: Path,
    *,
    expect: str,
    min_related_links: int = 0,
    forbidden_terms: list[str] | None = None,
    required_linked_phrases: list[str] | None = None,
    required_example_phrases: list[str] | None = None,
    allow_latin_terms: list[str] | None = None,
    required_headings: list[str] | None = None,
    forbidden_headings: list[str] | None = None,
    enforce_leading_bold_under: list[str] | None = None,
    leading_bold_threshold: int = 40,
    require_intro_before_first_heading: bool = True,
    require_related_section_final: bool = True,
    required_related_links: list[str] | None = None,
    check_title_matches_filename: bool = False,
) -> list[Violation]:
    forbidden_terms = forbidden_terms or []
    required_linked_phrases = required_linked_phrases or []
    required_example_phrases = required_example_phrases or []
    allow_latin_terms = allow_latin_terms or []
    required_headings = required_headings or []
    forbidden_headings = forbidden_headings or []
    enforce_leading_bold_under = enforce_leading_bold_under or []
    required_related_links = required_related_links or []

    text = path.read_text(encoding="utf-8")
    frontmatter_block, body, body_line_offset = _split_frontmatter(text)
    frontmatter = _parse_frontmatter_block(frontmatter_block)
    violations: list[Violation] = []

    violations.extend(_check_required_frontmatter(frontmatter, expect))
    violations.extend(_check_tag_rules(frontmatter, expect))
    if check_title_matches_filename:
        violations.extend(_check_title_matches_filename(path, frontmatter))

    body_violations: list[Violation] = []
    if require_intro_before_first_heading and expect == "source":
        body_violations.extend(_check_intro_before_first_heading(body))
    body_violations.extend(_check_duplicate_headings(body))
    body_violations.extend(_check_required_headings(body, required_headings))
    body_violations.extend(_check_forbidden_headings(body, forbidden_headings))
    body_violations.extend(_check_blank_lines_after_headings(body))
    body_violations.extend(_check_blank_lines_before_lists(body))
    body_violations.extend(_check_double_blank_lines(body))
    body_violations.extend(_check_forbidden_terms(body, forbidden_terms))
    body_violations.extend(_check_generic_latin_residue(body, allow_latin_terms))
    body_violations.extend(_check_required_linked_phrases(body, required_linked_phrases))
    body_violations.extend(_check_required_examples(body, required_example_phrases))
    body_violations.extend(
        _check_bold_leading_bullets(
            body,
            enforce_leading_bold_under,
            leading_bold_threshold,
        )
    )
    body_violations.extend(
        _check_closing_section(
            body,
            min_related_links,
            require_related_section_final,
            required_related_links,
        )
    )
    violations.extend(_offset_violations(body_violations, body_line_offset))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--expect", choices=("source", "concept"), default="source")
    parser.add_argument("--min-related-links", type=int, default=0)
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("--allow-latin-term", action="append", default=[])
    parser.add_argument("--require-linked-phrase", action="append", default=[])
    parser.add_argument("--require-example-phrase", action="append", default=[])
    parser.add_argument("--require-heading", action="append", default=[])
    parser.add_argument("--forbid-heading", action="append", default=[])
    parser.add_argument("--enforce-leading-bold-under", action="append", default=[])
    parser.add_argument("--leading-bold-threshold", type=int, default=40)
    parser.add_argument("--require-related-link", action="append", default=[])
    parser.add_argument("--skip-intro-check", action="store_true")
    parser.add_argument("--allow-related-section-not-final", action="store_true")
    parser.add_argument("--check-title-filename-match", action="store_true")
    args = parser.parse_args()

    violations = collect_violations(
        args.path,
        expect=args.expect,
        min_related_links=args.min_related_links,
        forbidden_terms=args.forbid,
        required_linked_phrases=args.require_linked_phrase,
        required_example_phrases=args.require_example_phrase,
        allow_latin_terms=args.allow_latin_term,
        required_headings=args.require_heading,
        forbidden_headings=args.forbid_heading,
        enforce_leading_bold_under=args.enforce_leading_bold_under,
        leading_bold_threshold=args.leading_bold_threshold,
        require_intro_before_first_heading=not args.skip_intro_check,
        require_related_section_final=not args.allow_related_section_not_final,
        required_related_links=args.require_related_link,
        check_title_matches_filename=args.check_title_filename_match,
    )
    for violation in violations:
        if violation.line is None:
            print(f"{violation.code}: {violation.message}")
        else:
            print(f"{violation.code}:L{violation.line}: {violation.message}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
