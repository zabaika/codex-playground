from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kb_index.config import IndexScope, load_runtime_config
from kb_index.indexer import build_or_update_index, iter_note_paths


class ConfigScopeTests(unittest.TestCase):
    def test_file_extensions_and_exclusions(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'Ideas').mkdir()
            (root / 'Ideas' / 'Nested').mkdir()
            (root / 'Ideas' / 'Directory.md').mkdir()
            for name in ('One.md', 'Nested/Two.txt', 'draft-three.txt', 'Skip.csv', 'Upper.MD'):
                (root / 'Ideas' / name).write_text('Text', encoding='utf-8')
            scope = IndexScope(
                include_roots=['Ideas'], exclude_roots=[],
                exclude_globs=['draft-*.txt'], file_extensions=['.md', '.txt'],
            )
            paths = iter_note_paths(root, scope)
            self.assertEqual(
                [str(path.relative_to(root)) for path in paths],
                ['Ideas/Nested/Two.txt', 'Ideas/One.md'],
            )
            db_path = root / 'index.sqlite'
            state_path = root / 'state.json'
            result = build_or_update_index(root, db_path, state_path, scope)
            self.assertEqual(result['updated_notes_count'], 2)
            self.assertEqual(result['scope']['file_extensions'], ['.md', '.txt'])
            scope.file_extensions = ['.txt']
            result = build_or_update_index(root, db_path, state_path, scope)
            self.assertEqual(result['scanned_notes_count'], 1)
            self.assertEqual(result['deleted_notes_count'], 1)
            scope.file_extensions = []
            self.assertEqual(iter_note_paths(root, scope), [])

    def test_file_extensions_is_required(self) -> None:
        example = Path(__file__).resolve().parents[1] / 'config/runtime.example.toml'
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / 'runtime.toml'
            config_path.write_text(
                example.read_text().replace("file_extensions = ['.md']\n", ''),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(KeyError, 'file_extensions'):
                load_runtime_config(config_path)

    def test_scope_is_loaded_from_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / 'runtime.local.toml'
            config_path.write_text(
                "[vault]\n"
                f"root = '{tmp_dir}'\n\n"
                "[auto_update]\n"
                "enabled = true\n"
                "mode = 'launchd'\n"
                "interval_minutes = 15\n"
                "launchd_label = 'local.kb-index.auto-update'\n"
                f"plist_path = '{tmp_dir}/auto-update.plist'\n"
                "run_on_load = true\n"
                "run_total_timeout_seconds = 120\n"
                "termination_grace_seconds = 5\n"
                "poll_interval_seconds = 0.5\n"
                "timeout_exit_code = 124\n"
                "term_signal = 'TERM'\n"
                "kill_signal = 'KILL'\n\n"
                "[scope]\n"
                "file_extensions = ['.md']\n"
                "include_roots = ['A', 'B']\n"
                "exclude_roots = ['Templates', 'Ideas/attachments']\n"
                "exclude_globs = ['*.canvas']\n\n"
                "[retrieval]\n"
                "default_limit = 6\n"
                "fts_candidate_limit = 60\n"
                "title_candidate_limit = 55\n"
                "links_out_candidate_limit = 25\n"
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
            self.assertEqual(config.scope.file_extensions, ['.md'])
            self.assertEqual(config.scope.include_roots, ['A', 'B'])
            self.assertEqual(config.scope.exclude_roots, ['Templates', 'Ideas/attachments'])
            self.assertEqual(config.scope.exclude_globs, ['*.canvas'])
            self.assertEqual(config.retrieval.default_limit, 6)
            self.assertEqual(config.retrieval.fts_candidate_limit, 60)
            self.assertEqual(config.retrieval.title_candidate_limit, 55)
            self.assertEqual(config.retrieval.links_out_candidate_limit, 25)
            self.assertEqual(config.retrieval.min_term_coverage_ratio, 0.5)
            self.assertEqual(config.retrieval.min_score_ratio_to_top, 0.45)
            self.assertEqual(config.retrieval.always_keep_top_n, 3)
            self.assertTrue(config.auto_update.enabled)
            self.assertEqual(config.auto_update.mode, 'launchd')
            self.assertEqual(config.auto_update.interval_minutes, 15)
            self.assertEqual(config.auto_update.run_total_timeout_seconds, 120)
            self.assertEqual(config.auto_update.termination_grace_seconds, 5)
            self.assertEqual(config.auto_update.poll_interval_seconds, 0.5)
            self.assertEqual(config.auto_update.timeout_exit_code, 124)
            self.assertEqual(config.auto_update.term_signal, 'TERM')
            self.assertEqual(config.auto_update.kill_signal, 'KILL')

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
                "[auto_update]\n"
                "enabled = true\n"
                "mode = 'launchd'\n"
                "interval_minutes = 15\n"
                "launchd_label = 'local.kb-index.auto-update'\n"
                f"plist_path = '{tmp_dir}/auto-update.plist'\n"
                "run_on_load = true\n"
                "run_total_timeout_seconds = 120\n"
                "termination_grace_seconds = 5\n"
                "poll_interval_seconds = 0.5\n"
                "timeout_exit_code = 124\n"
                "term_signal = 'TERM'\n"
                "kill_signal = 'KILL'\n\n"
                "[scope]\n"
                "file_extensions = ['.md']\n"
                "include_roots = ['Ideas']\n"
                "exclude_roots = ['Templates', 'Ideas/attachments']\n"
                "exclude_globs = []\n\n"
                "[retrieval]\n"
                "default_limit = 5\n"
                "fts_candidate_limit = 50\n"
                "title_candidate_limit = 50\n"
                "links_out_candidate_limit = 20\n"
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
