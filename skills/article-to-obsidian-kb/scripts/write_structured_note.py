#!/usr/bin/env python3
"""Structured-note writer for article-to-obsidian-kb."""

from __future__ import annotations

import argparse
import json
import importlib.util
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "templates"
DEFAULT_CONFIG_PATH = SKILL_DIR / "config" / "runtime.local.toml"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_note_contract import collect_violations, collect_violations_from_text
from runtime_paths import load_toml, resolve_project_root as resolve_project_root_path


def resolve_council_payload_schema_script() -> Path:
    candidate = SKILL_DIR.parent / "llm-council" / "scripts" / "council_payload_schema.py"
    if candidate.exists():
        return candidate
    raise FileNotFoundError("Could not locate llm-council payload schema next to article-to-obsidian-kb")


def load_council_payload_schema_module():
    script_path = resolve_council_payload_schema_script()
    spec = importlib.util.spec_from_file_location(
        "llm_council_payload_schema",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load council payload schema from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required text field: {key}")
    return value.strip()


def optional_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()


def normalize_text(value: str) -> str:
    lines = [line.rstrip() for line in value.strip().splitlines()]
    return "\n".join(lines).strip()


def text_block(value: str) -> str:
    return normalize_text(value)


def ordered_text_block(value: str) -> str:
    text = normalize_text(value)
    if text.startswith("1. "):
        text = re.sub(r"\s(?=(\d+)\.\s)", "\n", text)
    return text


def require_object(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Field '{key}' must be an object")
    return value


def normalize_string_list(value, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Field '{field_name}' must be a list")
    result = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Field '{field_name}' item #{idx} must be non-empty text")
        result.append(item.strip())
    return result


def load_payload(path: Path) -> dict[str, object]:
    schema = load_council_payload_schema_module()
    payload = schema.load_council_payload(path, cleanup_enabled=False)
    if payload["type"] != "council-verdict":
        raise ValueError("Only `type: council-verdict` is supported in structured mode")
    return payload


def yaml_quote(value: str) -> str:
    return "'" + re.sub(r"\s+", " ", value.strip()).replace("'", "''") + "'"


def resolve_project_root(config: dict[str, object] | None = None) -> Path:
    resolved = resolve_project_root_path(config=config, skill_dir=SKILL_DIR)
    if resolved is None:
        raise ValueError("Project root could not be resolved for display_source_path")
    return resolved


def display_source_path(value: str, config: dict[str, object] | None = None) -> str:
    raw = value.strip()
    if not raw or "://" in raw:
        return raw
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        return raw.replace("\\", "/")
    try:
        project_root = resolve_project_root(config)
    except ValueError:
        return raw.replace("\\", "/")
    try:
        relative = candidate.resolve(strict=False).relative_to(project_root)
    except ValueError:
        return raw.replace("\\", "/")
    return relative.as_posix()


def filesystem_safe_title(value: str) -> str:
    sanitized = re.sub(r'[\\/:*?"<>|]+', " ", value.strip())
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized or "council-verdict"


def advisor_link(name: str) -> str:
    return f"[[#{name}|{name}]]"


def reveal_advisor_labels(text: str, mapping: list[dict[str, str]]) -> str:
    result = text
    for item in mapping:
        label = item["label"]
        replacement = advisor_link(item["advisor"])
        result = re.sub(rf"`{re.escape(label)}`", replacement, result, flags=re.IGNORECASE)
        result = re.sub(rf"\b{re.escape(label)}\b", replacement, result, flags=re.IGNORECASE)
    return result


def render_frontmatter(payload: dict[str, object], config: dict[str, object] | None = None) -> str:
    year = payload["year"] or payload["timestamp"][:4]
    source = display_source_path(payload["payload_source"], config=config)
    lines = [
        "---",
        f"title: {yaml_quote(payload['title'])}",
        "source:",
        f"  - {yaml_quote(source)}",
        "type: council-verdict",
        "tags:",
        "  - council-verdict",
        f"date: {yaml_quote(year)}",
        "---",
    ]
    return "\n".join(lines)


def render_run_status_block(payload: dict[str, object]) -> str:
    run_status = payload["run_status"]
    if run_status["status"] != "degraded":
        return ""
    details = text_block(run_status["details"]) if run_status["details"] else ""
    lines = ["## Статус прогона", "Этот прогон был деградированным."]
    if details:
        lines.append(details)
    return "\n".join(lines)


def render_advisor_blocks(payload: dict[str, object]) -> str:
    lines: list[str] = []
    for advisor in payload["advisors"]:
        lines.append(f"### {advisor['name']}")
        lines.append(f"- **Позиция:** {advisor['stance']}")
        lines.append(f"- **Ключевой вывод:** {advisor['headline']}")
        lines.extend(text_block(advisor["response"]).splitlines())
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def render_peer_review_blocks(payload: dict[str, object]) -> str:
    reviews = payload["peer_reviews"]
    if not reviews:
        return "Секция peer review не была сохранена отдельно."
    lines: list[str] = []
    for review in reviews:
        lines.append(f"### {review['reviewer']}")
        lines.extend(
            reveal_advisor_labels(
                text_block(review["response"]), payload["anonymization_mapping"]
            ).splitlines()
        )
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def render_related_block(payload: dict[str, object]) -> str:
    notes = []
    seen = set()
    for note in payload["related_notes"]:
        if note not in seen:
            seen.add(note)
            notes.append(note)
    if not notes:
        return ""
    lines = ["", "# Связанные заметки"]
    lines.extend(f"[[{note}]]" for note in notes)
    return "\n".join(lines)


def build_markdown(payload: dict[str, object], config: dict[str, object] | None = None) -> str:
    verdict = payload["verdict"]
    template = (TEMPLATE_DIR / "council-verdict.md.tmpl").read_text(encoding="utf-8")
    rendered = template.format(
        frontmatter=render_frontmatter(payload, config=config),
        question=text_block(payload["question"]),
        run_status_block=render_run_status_block(payload),
        framed_question=text_block(payload["framed_question"]),
        agrees=reveal_advisor_labels(text_block(verdict["agrees"]), payload["anonymization_mapping"]),
        clashes=reveal_advisor_labels(text_block(verdict["clashes"]), payload["anonymization_mapping"]),
        blind_spots=reveal_advisor_labels(
            text_block(verdict["blind_spots"]), payload["anonymization_mapping"]
        ),
        recommendation=reveal_advisor_labels(
            text_block(verdict["recommendation"]), payload["anonymization_mapping"]
        ),
        first_step=reveal_advisor_labels(
            ordered_text_block(verdict["first_step"]), payload["anonymization_mapping"]
        ),
        advisor_blocks=render_advisor_blocks(payload),
        peer_review_blocks=render_peer_review_blocks(payload),
        related_block=render_related_block(payload),
    )
    return rendered.rstrip() + "\n"


def resolve_config(path: Path) -> dict[str, object]:
    return load_toml(path)


def resolve_output_path(
    payload: dict[str, object], output_path: Path | None, config: dict[str, object]
) -> Path:
    if output_path is not None:
        return output_path.expanduser()
    roots = config.get("structured_note_roots", {})
    if not isinstance(roots, dict):
        raise ValueError("Config field `structured_note_roots` must be a table when present")
    root = roots.get("council_verdict")
    if not isinstance(root, str) or not root.strip():
        raise ValueError(
            "Structured council verdict writing requires either --output or `structured_note_roots.council_verdict` in local config"
        )
    return Path(root).expanduser() / f"{filesystem_safe_title(payload['title'])}.md"


def validate_output_path(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError(f"Output path must be absolute: {path}")
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError(f"Parent output directory must already exist: {parent}")
    return path.resolve(strict=False)


def format_violations(violations) -> str:
    lines = []
    for item in violations:
        if item.line is None:
            lines.append(f"{item.code}: {item.message}")
        else:
            lines.append(f"{item.code}:L{item.line}: {item.message}")
    return "\n".join(lines)


def verify_markdown_with_contract_checker(markdown: str) -> None:
    violations = collect_violations_from_text(
        markdown,
        expect="structured-council-verdict",
        require_intro_before_first_heading=False,
        check_title_matches_filename=False,
    )
    if violations:
        raise ValueError("Structured note contract failed:\n" + format_violations(violations))


def verify_with_contract_checker(path: Path) -> None:
    violations = collect_violations(
        path,
        expect="structured-council-verdict",
        require_intro_before_first_heading=False,
        check_title_matches_filename=False,
    )
    if violations:
        raise ValueError("Structured note contract failed:\n" + format_violations(violations))


def write_structured_note(
    payload: dict[str, object],
    *,
    output_path: Path | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Path:
    config = resolve_config(config_path)
    resolved_output = validate_output_path(resolve_output_path(payload, output_path, config))
    markdown = build_markdown(payload, config=config)
    verify_markdown_with_contract_checker(markdown)
    resolved_output.write_text(markdown, encoding="utf-8")
    verify_with_contract_checker(resolved_output)
    return resolved_output


def main() -> int:
    parser = argparse.ArgumentParser(description="Write structured article-to-obsidian-kb notes.")
    parser.add_argument("--mode", choices=("source", "structured"), default="source")
    parser.add_argument("--type", dest="note_type")
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    if args.mode != "structured":
        raise SystemExit(
            "Default mode is `source`. Structured note writing requires `--mode structured`."
        )
    if args.note_type != "council-verdict":
        raise SystemExit("Structured mode currently supports only `--type council-verdict`.")
    if args.payload is None:
        raise SystemExit("Structured mode requires `--payload <path>`.")

    payload = load_payload(args.payload)
    write_structured_note(payload, output_path=args.output, config_path=args.config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
