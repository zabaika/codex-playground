from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kb_index.config import load_runtime_config
from kb_index.indexer import iter_note_paths


class ConfigScopeTests(unittest.TestCase):
    def test_scope_is_loaded_from_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / 'runtime.local.toml'
            config_path.write_text(
                "[vault]\n"
                f"root = '{tmp_dir}'\n\n"
                "[scope]\n"
                "include_roots = ['A', 'B']\n"
                "exclude_roots = ['Templates', 'Ideas/attachments']\n"
                "exclude_globs = ['*.canvas']\n\n"
                "[retrieval]\n"
                "default_limit = 6\n"
                "min_term_coverage_ratio = 0.5\n"
                "min_score_ratio_to_top = 0.45\n"
                "always_keep_top_n = 3\n\n"
                "[ranking.weights]\n"
                "fts = 0.3\n"
                "title = 0.2\n"
                "title_exact = 0.2\n"
                "lead_summary = 0.15\n"
                "heading = 0.05\n"
                "tags = 0.05\n"
                "links_out = 0.08\n"
                "note_type = 0.05\n\n"
                "[ranking.note_type_weights]\n"
                "concept = 0.8\n"
                "idea = 0.75\n"
                "job = 0.65\n"
                "daily = 0.35\n"
                "other = 0.5\n\n"
                "[ranking.exact_title_bonus]\n"
                "exact_match = 1.0\n"
                "substring_match = 0.85\n"
                "all_terms_match = 0.75\n"
                "near_match = 0.45\n",
                encoding='utf-8',
            )
            config = load_runtime_config(config_path)
            self.assertEqual(config.scope.include_roots, ['A', 'B'])
            self.assertEqual(config.scope.exclude_roots, ['Templates', 'Ideas/attachments'])
            self.assertEqual(config.scope.exclude_globs, ['*.canvas'])
            self.assertEqual(config.retrieval.default_limit, 6)
            self.assertEqual(config.retrieval.min_term_coverage_ratio, 0.5)
            self.assertEqual(config.retrieval.min_score_ratio_to_top, 0.45)
            self.assertEqual(config.retrieval.always_keep_top_n, 3)

    def test_missing_behavioral_settings_fail_fast(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / 'runtime.local.toml'
            config_path.write_text(
                "[vault]\n"
                f"root = '{tmp_dir}'\n",
                encoding='utf-8',
            )
            with self.assertRaises(KeyError):
                load_runtime_config(config_path)

    def test_iter_note_paths_uses_configured_scope(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            (root / 'Job').mkdir()
            (root / 'Templates').mkdir()
            (root / 'Ideas' / 'One.md').write_text('Text', encoding='utf-8')
            (root / 'Ideas' / 'attachments').mkdir(parents=True)
            (root / 'Ideas' / 'attachments' / 'Nested.md').write_text('Text', encoding='utf-8')
            (root / 'Job' / 'Two.md').write_text('Text', encoding='utf-8')
            (root / 'Templates' / 'Skip.md').write_text('Text', encoding='utf-8')
            config_path = root / 'runtime.local.toml'
            config_path.write_text(
                "[vault]\n"
                f"root = '{tmp_dir}'\n\n"
                "[scope]\n"
                "include_roots = ['Ideas']\n"
                "exclude_roots = ['Templates', 'Ideas/attachments']\n"
                "exclude_globs = []\n\n"
                "[retrieval]\n"
                "default_limit = 5\n"
                "min_term_coverage_ratio = 0.5\n"
                "min_score_ratio_to_top = 0.45\n"
                "always_keep_top_n = 3\n\n"
                "[ranking.weights]\n"
                "fts = 0.3\n"
                "title = 0.2\n"
                "title_exact = 0.2\n"
                "lead_summary = 0.15\n"
                "heading = 0.05\n"
                "tags = 0.05\n"
                "links_out = 0.08\n"
                "note_type = 0.05\n\n"
                "[ranking.note_type_weights]\n"
                "concept = 0.8\n"
                "idea = 0.75\n"
                "job = 0.65\n"
                "daily = 0.35\n"
                "other = 0.5\n\n"
                "[ranking.exact_title_bonus]\n"
                "exact_match = 1.0\n"
                "substring_match = 0.85\n"
                "all_terms_match = 0.75\n"
                "near_match = 0.45\n",
                encoding='utf-8',
            )
            config = load_runtime_config(config_path)
            paths = [str(path.relative_to(root)) for path in iter_note_paths(root, config.scope)]
            self.assertEqual(paths, ['Ideas/One.md'])


if __name__ == '__main__':
    unittest.main()
