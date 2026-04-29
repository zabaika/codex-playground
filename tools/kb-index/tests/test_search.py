from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kb_index.config import IndexScope, RankingConfig, RetrievalConfig
from kb_index.indexer import build_or_update_index
from kb_index.search import search_index


class SearchTests(unittest.TestCase):
    def make_ranking(self) -> RankingConfig:
        return RankingConfig(
            weights={
                "fts": 0.3,
                "title": 0.2,
                "title_exact": 0.2,
                "lead_summary": 0.15,
                "heading": 0.05,
                "tags": 0.05,
                "links_out": 0.08,
                "note_type": 0.05,
            },
            note_type_weights={
                "concept": 0.8,
                "idea": 0.75,
                "job": 0.65,
                "daily": 0.35,
                "other": 0.5,
            },
            exact_title_bonus={
                "exact_match": 1.0,
                "substring_match": 0.85,
                "all_terms_match": 0.75,
                "near_match": 0.45,
            },
        )

    def make_retrieval(self) -> RetrievalConfig:
        return RetrievalConfig(
            default_limit=5,
            min_term_coverage_ratio=0.5,
            min_score_ratio_to_top=0.45,
            always_keep_top_n=3,
        )

    def test_search_uses_lead_summary(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "Ideas").mkdir()
            (root / "Ideas" / "Alpha.md").write_text(
                "---\n"
                "tags:\n"
                "  - ai-governance\n"
                "---\n"
                "Этот абзац про проверку AI-ответов и доверие к результату.\n\n"
                "## Детали\n"
                "Остальной текст.\n",
                encoding="utf-8",
            )
            db_path = root / "index.sqlite"
            state_path = root / "state.json"
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)
            results = search_index(
                db_path,
                "доверие к результату",
                ranking=self.make_ranking(),
                retrieval=self.make_retrieval(),
                limit=3,
            )
            self.assertTrue(results)
            self.assertEqual(results[0]["path"], "Ideas/Alpha.md")
            self.assertEqual(results[0]["title"], "Alpha")
            self.assertEqual(results[0]["lead_summary"], "Этот абзац про проверку AI-ответов и доверие к результату.")

    def test_search_filters_low_coverage_lexical_false_positive(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "Ideas").mkdir()
            (root / "Ideas" / "Trust Tax.md").write_text(
                "---\n"
                "tags:\n"
                "  - ai-governance\n"
                "---\n"
                "Налог недоверия к AI описывает дополнительную стоимость проверки, верификации и ручного контроля AI-ответов.\n",
                encoding="utf-8",
            )
            (root / "Ideas" / "Spain Taxes.md").write_text(
                "---\n"
                "tags:\n"
                "  - relocation\n"
                "---\n"
                "Налоги Испании для физлиц зависят от резидентства, ставок и региональных правил.\n",
                encoding="utf-8",
            )
            db_path = root / "index.sqlite"
            state_path = root / "state.json"
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)
            results = search_index(
                db_path,
                "Налог недоверия к AI",
                ranking=self.make_ranking(),
                retrieval=self.make_retrieval(),
                limit=5,
            )
            self.assertTrue(results)
            self.assertEqual(results[0]["path"], "Ideas/Trust Tax.md")
            self.assertNotIn("Ideas/Spain Taxes.md", [item["path"] for item in results])

    def test_search_uses_links_out_for_related_note_discovery(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "Ideas").mkdir()
            (root / "Ideas" / "Concept.md").write_text(
                "---\n"
                "title: Налог недоверия к AI\n"
                "---\n"
                "Налог недоверия к AI описывает цену последующей проверки AI-ответов.\n",
                encoding="utf-8",
            )
            (root / "Ideas" / "Article.md").write_text(
                "---\n"
                "title: DX article\n"
                "---\n"
                "Эта заметка про умеренный эффект AI на производительность.\n\n"
                "## Evidence\n"
                "Команды сталкиваются со скрытой ценой верификации и перепроверки, что хорошо видно через [[Налог недоверия к AI]].\n",
                encoding="utf-8",
            )
            db_path = root / "index.sqlite"
            state_path = root / "state.json"
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)
            results = search_index(
                db_path,
                "Налог недоверия к AI",
                ranking=self.make_ranking(),
                retrieval=self.make_retrieval(),
                limit=5,
            )
            self.assertTrue(results)
            self.assertEqual(results[0]["path"], "Ideas/Concept.md")
            self.assertIn("Ideas/Article.md", [item["path"] for item in results])
            article = next(item for item in results if item["path"] == "Ideas/Article.md")
            self.assertIn("Налог недоверия к AI", article["links_out"])
            self.assertIn("links_out", article["candidate_sources"])

    def test_title_first_mode_finds_known_concept_note_without_filesystem_scan(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "Ideas" / "Concepts").mkdir(parents=True)
            (root / "Ideas").mkdir(exist_ok=True)
            (root / "Ideas" / "Concepts" / "Социотехническая продуктивность.md").write_text(
                "---\n"
                "type: concept\n"
                "tags:\n"
                "  - developer-productivity\n"
                "---\n"
                "Социотехническая продуктивность описывает переплетение инженерных и организационных ограничений.\n",
                encoding="utf-8",
            )
            (root / "Ideas" / "AI ускоряет код, но не доставку - узкие места инженерной системы.md").write_text(
                "---\n"
                "tags:\n"
                "  - developer-productivity\n"
                "---\n"
                "Эта заметка объясняет, почему локальное ускорение кодинга не снимает ограничения всей системы.\n\n"
                "## Related\n"
                "См. также [[Социотехническая продуктивность]].\n",
                encoding="utf-8",
            )
            db_path = root / "index.sqlite"
            state_path = root / "state.json"
            scope = IndexScope(include_roots=['Ideas'], exclude_roots=['Templates'], exclude_globs=[])
            build_or_update_index(root, db_path, state_path, scope)
            results = search_index(
                db_path,
                "Социотехническая продуктивность",
                ranking=self.make_ranking(),
                retrieval=self.make_retrieval(),
                limit=5,
                mode='title-first',
                note_type='concept',
            )
            self.assertTrue(results)
            self.assertEqual(results[0]["path"], "Ideas/Concepts/Социотехническая продуктивность.md")
            self.assertIn("title", results[0]["candidate_sources"])
            self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
