#!/usr/bin/env python3

import argparse
import importlib.util
import json
import tomllib
from pathlib import Path

from council_payload_schema import load_council_payload, normalize_advisors, normalize_council_payload
from render_common import (
    normalize_anonymization_mapping,
    normalize_peer_reviews,
    normalize_string_list,
    require_object,
    payload_cleanup_enabled,
    sanitize_optional_text,
    sanitize_required_text,
    sanitize_run_status,
    validate_council_run_status,
    validate_anonymization_mapping,
    parse_allowed_roots,
    validate_output_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config" / "runtime.local.toml"

def final_note_title(payload) -> str:
    title = payload["title"] or "Решение совета"
    marker = run_marker(payload)
    if marker and not title.endswith(f"({marker})"):
        title = f"{title} ({marker})"
    return title


def resolve_article_writer_script() -> Path:
    candidate = SKILL_DIR.parent / "article-to-obsidian-kb" / "scripts" / "write_structured_note.py"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "Could not locate article-to-obsidian-kb structured writer next to llm-council"
    )


def load_article_writer_module():
    script_path = resolve_article_writer_script()
    spec = importlib.util.spec_from_file_location(
        "article_to_obsidian_structured_writer", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load structured writer module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_payload_data(
    data: dict,
    source_path: Path | None = None,
    cleanup_enabled: bool | None = None,
    default_payload_source: str | None = None,
):
    return normalize_council_payload(
        data,
        cleanup_enabled=cleanup_enabled,
        default_payload_source=default_payload_source,
    )


def load_payload(path: Path):
    return load_council_payload(path)


def run_marker(payload):
    timestamp = payload.get("timestamp", "")
    if len(timestamp) >= 19:
        return timestamp[11:19].replace(":", "")
    return ""


def to_structured_payload(payload):
    return {
        "type": "council-verdict",
        "title": final_note_title(payload),
        "timestamp": payload["timestamp"],
        "year": payload["year"],
        "question": payload["question"],
        "framed_question": payload["framed_question"],
        "payload_source": payload["payload_source"],
        "run_status": payload["run_status"],
        "verdict": payload["verdict"],
        "advisors": payload["advisors"],
        "peer_reviews": payload["peer_reviews"],
        "anonymization_mapping": payload["anonymization_mapping"],
        "related_notes": payload["related_notes"],
    }


def to_canonical_payload(payload):
    return {
        "type": payload["type"],
        "title": payload["title"],
        "timestamp": payload["timestamp"],
        "year": payload["year"],
        "question": payload["question"],
        "framed_question": payload["framed_question"],
        "payload_source": payload["payload_source"],
        "run_status": payload["run_status"],
        "verdict": payload["verdict"],
        "advisors": payload["advisors"],
        "peer_reviews": payload["peer_reviews"],
        "anonymization_mapping": payload["anonymization_mapping"],
        "related_notes": payload["related_notes"],
    }


def resolve_canonical_payload_root(config_path: Path | None = None) -> Path:
    resolved_config_path = (
        config_path.expanduser() if config_path is not None else DEFAULT_CONFIG_PATH
    )
    if not resolved_config_path.exists():
        raise ValueError(
            f"Canonical payload writing requires a local config with `paths.temp_root`: {resolved_config_path}"
        )
    config = tomllib.loads(resolved_config_path.read_text(encoding="utf-8"))
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        raise ValueError("Config field `paths` must be a table when present")
    temp_root = paths.get("temp_root")
    if not isinstance(temp_root, str) or not temp_root.strip():
        raise ValueError(
            "Canonical payload writing requires `paths.temp_root` in local config"
        )
    allowed_roots = parse_allowed_roots([temp_root])
    return allowed_roots[0]


def write_canonical_payload(
    raw_payload: dict,
    output_path: Path,
    cleanup_enabled: bool | None = None,
    config_path: Path | None = None,
):
    resolved_output_path = validate_output_path(
        output_path, [resolve_canonical_payload_root(config_path)]
    )
    normalized = normalize_payload_data(
        raw_payload,
        source_path=resolved_output_path,
        cleanup_enabled=cleanup_enabled,
        default_payload_source=str(resolved_output_path),
    )
    canonical = to_canonical_payload(normalized)
    resolved_output_path.write_text(
        json.dumps(canonical, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return canonical


def build_markdown(payload):
    article_writer = load_article_writer_module()
    structured_payload = to_structured_payload(payload)
    return article_writer.build_markdown(structured_payload)


def write_verdict_note(
    payload,
    *,
    output_path: Path | None = None,
    config_path: Path | None = None,
):
    article_writer = load_article_writer_module()
    structured_payload = to_structured_payload(payload)
    writer_config_path = (
        config_path
        if config_path is not None
        else article_writer.DEFAULT_CONFIG_PATH
    )
    return article_writer.write_structured_note(
        structured_payload,
        output_path=output_path,
        config_path=writer_config_path,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Delegate council verdict-note writing to article-to-obsidian-kb structured mode."
    )
    parser.add_argument("input_json", help="Path to the JSON payload")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional explicit output path for the final markdown note.",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        help="Optional config path for article-to-obsidian-kb structured-note routing.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_json)
    payload = load_payload(input_path)
    output_path = write_verdict_note(
        payload,
        output_path=args.output,
        config_path=args.config_path,
    )
    print(output_path)


if __name__ == "__main__":
    main()
