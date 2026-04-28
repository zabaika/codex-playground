from __future__ import annotations

import json
import time
from pathlib import Path

from .config import IndexScope
from .index_db import connect, delete_missing_notes, get_note_record, init_db, upsert_note
from .parser import parse_note


def iter_note_paths(vault_root: Path, scope: IndexScope) -> list[Path]:
    paths: list[Path] = []
    exclude_roots = [Path(item) for item in scope.exclude_roots]
    exclude_globs = list(scope.exclude_globs)
    for directory in scope.include_roots:
        root = vault_root / directory
        if not root.exists():
            continue
        for path in root.rglob('*.md'):
            rel = path.relative_to(vault_root)
            if any(rel == excluded or excluded in rel.parents for excluded in exclude_roots):
                continue
            if any(path.match(pattern) or rel.match(pattern) for pattern in exclude_globs):
                continue
            paths.append(path)
    return sorted(paths)


def write_state(state_path: Path, payload: dict[str, object]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def read_state(state_path: Path) -> dict[str, object]:
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding='utf-8'))


def build_or_update_index(vault_root: Path, db_path: Path, state_path: Path, scope: IndexScope) -> dict[str, object]:
    started_at = time.time()
    attempt_at = int(started_at)
    previous_state = read_state(state_path)
    conn = connect(db_path)
    scanned = 0
    try:
        init_db(conn)

        note_paths = iter_note_paths(vault_root, scope)
        existing_rel_paths = {str(path.relative_to(vault_root)) for path in note_paths}
        updated = 0
        unchanged = 0

        for note_path in note_paths:
            scanned += 1
            relative_path = str(note_path.relative_to(vault_root))
            stat = note_path.stat()
            existing = get_note_record(conn, relative_path)
            if existing and existing['mtime'] == int(stat.st_mtime) and existing['size_bytes'] == stat.st_size:
                unchanged += 1
                continue
            parsed = parse_note(vault_root, note_path)
            if existing and existing['content_hash'] == parsed.content_hash:
                conn.execute(
                    'UPDATE notes SET mtime = ?, size_bytes = ?, indexed_at = ? WHERE path = ?',
                    (parsed.mtime, parsed.size_bytes, int(time.time()), relative_path),
                )
                unchanged += 1
                continue
            upsert_note(conn, parsed)
            updated += 1

        deleted = delete_missing_notes(conn, existing_rel_paths)
        conn.commit()
        duration_ms = int((time.time() - started_at) * 1000)
        payload = {
            'last_successful_update_at': int(time.time()),
            'last_attempt_at': attempt_at,
            'last_update_duration_ms': duration_ms,
            'updated_notes_count': updated,
            'deleted_notes_count': deleted,
            'unchanged_notes_count': unchanged,
            'scanned_notes_count': len(note_paths),
            'scope': {
                'include_roots': scope.include_roots,
                'exclude_roots': scope.exclude_roots,
                'exclude_globs': scope.exclude_globs,
            },
            'last_error': None,
        }
        write_state(state_path, payload)
        return payload
    except Exception as exc:
        conn.rollback()
        duration_ms = int((time.time() - started_at) * 1000)
        payload = {
            'last_successful_update_at': previous_state.get('last_successful_update_at'),
            'last_attempt_at': attempt_at,
            'last_update_duration_ms': duration_ms,
            'updated_notes_count': 0,
            'deleted_notes_count': 0,
            'unchanged_notes_count': 0,
            'scanned_notes_count': scanned,
            'scope': {
                'include_roots': scope.include_roots,
                'exclude_roots': scope.exclude_roots,
                'exclude_globs': scope.exclude_globs,
            },
            'last_error': str(exc),
        }
        write_state(state_path, payload)
        raise
    finally:
        conn.close()
