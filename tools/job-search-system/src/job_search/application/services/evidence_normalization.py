from __future__ import annotations

from typing import Any


def dedupe_evidence_map(evidence: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    deduped: dict[str, list[dict[str, Any]]] = {}
    for field_name, entries in evidence.items():
        seen: set[tuple[tuple[str, str], ...]] = set()
        unique_entries: list[dict[str, Any]] = []
        for entry in entries:
            key = tuple(sorted((str(k), str(v)) for k, v in entry.items()))
            if key in seen:
                continue
            seen.add(key)
            unique_entries.append(entry)
        deduped[field_name] = unique_entries
    return deduped


def dedupe_record_list(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        key = tuple(sorted((str(k), str(v)) for k, v in record.items() if v not in (None, "", [])))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped
