from __future__ import annotations

import json
from pathlib import Path

from .index_db import connect, init_db


def _normalize_tag(text: str) -> str:
    return text.strip().lower()


def list_tags_index(
    db_path: Path,
    *,
    tag: str | None = None,
    prefix: str | None = None,
) -> list[dict[str, object]]:
    conn = connect(db_path)
    init_db(conn)
    rows = conn.execute(
        """
        SELECT path, title, note_type, tags_json
        FROM notes
        ORDER BY path
        """
    ).fetchall()
    conn.close()

    tag_entries: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        for raw_tag in json.loads(row['tags_json'] or '[]'):
            normalized_tag = _normalize_tag(str(raw_tag))
            if not normalized_tag:
                continue
            tag_entries.setdefault(normalized_tag, []).append(
                {
                    'path': row['path'],
                    'title': row['title'],
                    'note_type': row['note_type'],
                }
            )

    exact_filter = _normalize_tag(tag) if tag else None
    prefix_filter = _normalize_tag(prefix) if prefix else None
    if exact_filter and prefix_filter:
        raise ValueError('Use either tag or prefix, not both')

    if exact_filter:
        matched_tags = [exact_filter] if exact_filter in tag_entries else []
    elif prefix_filter:
        matched_tags = [item for item in sorted(tag_entries) if item.startswith(prefix_filter)]
    else:
        matched_tags = sorted(tag_entries, key=lambda item: (-len(tag_entries[item]), item))

    results: list[dict[str, object]] = []
    for matched_tag in matched_tags:
        notes = sorted(tag_entries[matched_tag], key=lambda item: item['path'])
        payload: dict[str, object] = {
            'tag': matched_tag,
            'note_count': len(notes),
        }
        if exact_filter or prefix_filter:
            payload['notes'] = notes
        results.append(payload)
    return results
