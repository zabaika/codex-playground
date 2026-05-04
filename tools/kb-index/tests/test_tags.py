from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kb_index.config import IndexScope
from kb_index.indexer import build_or_update_index
from kb_index.tags import list_tags_index


class TagLookupTests(unittest.TestCase):
    def test_list_all_tags_returns_counts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            (root / 'Ideas' / 'One.md').write_text(
                "---\n"
                "tags:\n"
                "  - developer-productivity\n"
                "  - metrics\n"
                "---\n"
                "One body\n",
                encoding='utf-8',
            )
            (root / 'Ideas' / 'Two.md').write_text(
                "---\n"
                "tags:\n"
                "  - developer-productivity\n"
                "---\n"
                "Two body\n",
                encoding='utf-8',
            )
            db_path = root / 'index.sqlite'
            state_path = root / 'state.json'
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)

            results = list_tags_index(db_path)

            self.assertEqual(results[0], {'tag': 'developer-productivity', 'note_count': 2})
            self.assertIn({'tag': 'metrics', 'note_count': 1}, results)

    def test_exact_tag_lookup_returns_notes(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            (root / 'Ideas' / 'One.md').write_text(
                "---\n"
                "tags:\n"
                "  - ai-governance\n"
                "---\n"
                "One body\n",
                encoding='utf-8',
            )
            db_path = root / 'index.sqlite'
            state_path = root / 'state.json'
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)

            results = list_tags_index(db_path, tag='ai-governance')

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['tag'], 'ai-governance')
            self.assertEqual(results[0]['note_count'], 1)
            self.assertEqual(results[0]['notes'][0]['path'], 'Ideas/One.md')

    def test_prefix_lookup_returns_matching_tags_and_notes(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            (root / 'Ideas' / 'One.md').write_text(
                "---\n"
                "tags:\n"
                "  - developer-productivity\n"
                "  - developer-experience\n"
                "---\n"
                "One body\n",
                encoding='utf-8',
            )
            db_path = root / 'index.sqlite'
            state_path = root / 'state.json'
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)

            results = list_tags_index(db_path, prefix='developer-')

            self.assertEqual([item['tag'] for item in results], ['developer-experience', 'developer-productivity'])
            self.assertEqual(results[0]['notes'][0]['path'], 'Ideas/One.md')
            self.assertEqual(results[1]['notes'][0]['path'], 'Ideas/One.md')


if __name__ == '__main__':
    unittest.main()
