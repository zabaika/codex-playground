#!/usr/bin/env python3
"""Canonical language-term registry loader for article-to-obsidian-kb."""

from __future__ import annotations

import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REGISTRY_PATH = SKILL_DIR / "config" / "language_terms.yaml"


def load_language_terms() -> dict[str, object]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


REGISTRY = load_language_terms()


def _section(name: str) -> dict[str, list[str]]:
    value = REGISTRY.get(name)
    if not isinstance(value, dict):
        raise KeyError(f"Unknown language-term section: {name}")
    return value  # type: ignore[return-value]


def single_terms(name: str) -> set[str]:
    section = _section(name)
    values = section.get("single", [])
    if not isinstance(values, list):
        raise KeyError(f"Invalid single-term registry section: {name}")
    return {value for value in values if isinstance(value, str) and value.strip()}


def phrase_terms(name: str) -> set[str]:
    section = _section(name)
    values = section.get("phrases", [])
    if not isinstance(values, list):
        raise KeyError(f"Invalid phrase-term registry section: {name}")
    return {value for value in values if isinstance(value, str) and value.strip()}


def mapped_single_terms(name: str) -> dict[str, str]:
    section = _section(name)
    values = section.get("single", {})
    if not isinstance(values, dict):
        raise KeyError(f"Invalid mapped single-term registry section: {name}")
    return {
        key: value
        for key, value in values.items()
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
    }


def mapped_phrase_terms(name: str) -> dict[str, str]:
    section = _section(name)
    values = section.get("phrases", {})
    if not isinstance(values, dict):
        raise KeyError(f"Invalid mapped phrase-term registry section: {name}")
    return {
        key: value
        for key, value in values.items()
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
    }


def canonical_single_terms() -> set[str]:
    return single_terms("canonical_terms")


def canonical_phrases() -> set[str]:
    return phrase_terms("canonical_terms")


def role_label_phrases() -> set[str]:
    return phrase_terms("role_labels")


def named_entity_phrases() -> set[str]:
    return phrase_terms("named_entities")


def named_entity_single_terms() -> set[str]:
    return single_terms("named_entities")


def discouraged_single_terms() -> set[str]:
    return set(mapped_single_terms("discouraged_prose_terms"))


def discouraged_phrases() -> set[str]:
    return set(mapped_phrase_terms("discouraged_prose_terms"))


def discouraged_translations() -> dict[str, str]:
    translations = mapped_single_terms("discouraged_prose_terms")
    translations.update(mapped_phrase_terms("discouraged_prose_terms"))
    return translations
