#!/usr/bin/env python3
"""Canonical note schema loader for article-to-obsidian-kb."""

from __future__ import annotations

import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SCHEMA_PATH = SKILL_DIR / "config" / "note_schema.yaml"


def load_note_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


SCHEMA = load_note_schema()
HEADINGS = SCHEMA["headings"]
NOTE_SHAPES = SCHEMA["note_shapes"]


def heading(key: str) -> str:
    value = HEADINGS.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KeyError(f"Unknown note-schema heading key: {key}")
    return value
