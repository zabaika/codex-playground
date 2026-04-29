from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kb_index.config import IndexScope
from kb_index.indexer import build_or_update_index


class StateTests(unittest.TestCase):
    def test_manual_update_writes_readable_state_timestamps(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            (root / 'Ideas' / 'Alpha.md').write_text('Alpha body\n', encoding='utf-8')
            db_path = root / 'index.sqlite'
            state_path = root / 'state.json'
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])

            payload = build_or_update_index(root, db_path, state_path, scope)
            state = json.loads(state_path.read_text(encoding='utf-8'))

            self.assertEqual(payload, state)
            self.assertRegex(state['last_successful_update_at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$')
            self.assertRegex(state['last_attempt_at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$')

    def test_failed_update_preserves_previous_successful_timestamp_text(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            note_path = root / 'Ideas' / 'Alpha.md'
            note_path.write_text('Alpha body\n', encoding='utf-8')
            db_path = root / 'index.sqlite'
            state_path = root / 'state.json'
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])

            first_state = build_or_update_index(root, db_path, state_path, scope)

            with patch('kb_index.indexer.parse_note', side_effect=RuntimeError('boom')):
                note_path.write_text('Changed body\n', encoding='utf-8')
                with self.assertRaises(RuntimeError):
                    build_or_update_index(root, db_path, state_path, scope)

            failed_state = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(failed_state['last_successful_update_at'], first_state['last_successful_update_at'])
            self.assertRegex(failed_state['last_attempt_at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$')
            self.assertEqual(failed_state['last_error'], 'boom')


if __name__ == '__main__':
    unittest.main()
