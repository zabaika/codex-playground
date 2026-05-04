import json
import sys
import tempfile
import unittest
from pathlib import Path
import importlib.util


TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
FIXTURES_DIR = TESTS_DIR / "fixtures"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import render_council_report as report_renderer
import render_common


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def write_temp_payload(tmp_dir: Path, fixture_name: str) -> Path:
    path = tmp_dir / fixture_name
    path.write_text(json.dumps(load_fixture(fixture_name), ensure_ascii=False, indent=2))
    return path


def write_temp_runtime_config(
    tmp_root: Path, temp_root: Path, filename: str = "payload-runtime.local.toml"
) -> Path:
    config_path = tmp_root / filename
    config_path.write_text(
        "[paths]\n"
        f'temp_root = "{temp_root}"\n',
        encoding="utf-8",
    )
    return config_path


class RendererContractTests(unittest.TestCase):
    def test_skill_uses_external_prompt_and_payload_owners(self):
        skill_text = (SKILL_DIR / "SKILL.md").read_text()
        prompts_text = (SKILL_DIR / "references" / "role-prompts.md").read_text()
        payload_text = (SKILL_DIR / "references" / "payload-contract.md").read_text()

        self.assertIn("references/role-prompts.md", skill_text)
        self.assertIn("references/payload-contract.md", skill_text)
        self.assertNotIn("## Renderer payload", skill_text)
        self.assertNotIn("prompt-and-transcript-templates.md", skill_text)
        self.assertNotIn("markdown-contract.md", skill_text)
        self.assertIn("## Advisor prompt", prompts_text)
        self.assertIn("## Reviewer prompt", prompts_text)
        self.assertIn("## Chairman prompt", prompts_text)
        self.assertIn("## Required shape", payload_text)
        self.assertIn("## Text-format rules", payload_text)

    def test_soft_sanitizer_removes_decorative_markdown(self):
        raw = """### Заголовок
**Сильный тезис**
`Response A`
[[Note|Алиас]]
[Ссылка](https://example.com)
```text
code
```"""
        cleaned = render_common.sanitize_payload_text(raw, enabled=True)
        self.assertNotIn("###", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("`Response A`", cleaned)
        self.assertNotIn("[[", cleaned)
        self.assertNotIn("](https://", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertIn("Сильный тезис", cleaned)
        self.assertIn("Response A", cleaned)
        self.assertIn("Алиас", cleaned)
        self.assertIn("Ссылка", cleaned)
        self.assertIn("code", cleaned)

    def test_payload_cleanup_config_defaults_enabled_and_can_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp)
            config_dir = skill_dir / "config"
            config_dir.mkdir()

            self.assertTrue(render_common.payload_cleanup_enabled(skill_dir))

            (config_dir / "runtime.local.toml").write_text(
                "[payload_cleanup]\nenabled = false\n"
            )
            self.assertFalse(render_common.payload_cleanup_enabled(skill_dir))

    def test_normalize_payload_data_cleans_canonical_payload_for_storage(self):
        raw = {
            "type": "council-verdict",
            "title": "Тест",
            "timestamp": "2026-05-03 23:59:00",
            "question": "### Вопрос\n**Нужно ли это делать?**",
            "framed_question": "`Сформулированный` вопрос",
            "payload_source": "scratch/llm-council/council-payload-20260503-235900.json",
            "run_status": {
                "status": "degraded",
                "details": "Тестовая фикстура использует один ответ советника для проверки очистки payload.",
            },
            "verdict": {
                "agrees": "`Response A` прав",
                "clashes": "**Есть разногласия**",
                "blind_spots": "[См. заметку](https://example.com)",
                "recommendation": "[[Совет|Совет]] один",
                "first_step": "1. Сделать шаг 1 2. Сделать шаг 2",
            },
            "advisors": [
                {
                    "name": "Contrarian",
                    "headline": "**Сильный тезис**",
                    "stance": "`Keep bilingual`",
                    "response": "### Ответ\n[[Note|Алиас]]",
                }
            ],
            "peer_reviews": [{"reviewer": "Reviewer 1", "response": "`Response A` strongest"}],
            "anonymization_mapping": [{"label": "Response A", "advisor": "Contrarian"}],
        }
        normalized = report_renderer.normalize_payload_data(raw)
        canonical = report_renderer.to_canonical_payload(normalized)

        self.assertEqual("council-verdict", canonical["type"])
        self.assertEqual("Вопрос\nНужно ли это делать?", canonical["question"])
        self.assertEqual("Сформулированный вопрос", canonical["framed_question"])
        self.assertEqual("Response A прав", canonical["verdict"]["agrees"])
        self.assertEqual("Есть разногласия", canonical["verdict"]["clashes"])
        self.assertEqual("См. заметку", canonical["verdict"]["blind_spots"])
        self.assertEqual("Совет один", canonical["verdict"]["recommendation"])
        self.assertEqual("Сильный тезис", canonical["advisors"][0]["headline"])
        self.assertEqual("Keep bilingual", canonical["advisors"][0]["stance"])
        self.assertEqual("Ответ\nАлиас", canonical["advisors"][0]["response"])
        self.assertEqual("Response A strongest", canonical["peer_reviews"][0]["response"])

    def test_write_canonical_payload_persists_cleaned_json(self):
        raw = {
            "type": "council-verdict",
            "title": "Тест",
            "timestamp": "2026-05-03 23:59:00",
            "question": "### Вопрос\n**Нужно ли это делать?**",
            "framed_question": "`Сформулированный` вопрос",
            "payload_source": "scratch/llm-council/council-payload-20260503-235900.json",
            "run_status": {
                "status": "degraded",
                "details": "Тестовая фикстура использует один ответ советника для проверки записи canonical payload.",
            },
            "verdict": {
                "agrees": "`Response A` прав",
                "clashes": "**Есть разногласия**",
                "blind_spots": "[См. заметку](https://example.com)",
                "recommendation": "[[Совет|Совет]] один",
                "first_step": "1. Сделать шаг 1 2. Сделать шаг 2",
            },
            "advisors": [
                {
                    "name": "Contrarian",
                    "headline": "**Сильный тезис**",
                    "stance": "`Keep bilingual`",
                    "response": "### Ответ\n[[Note|Алиас]]",
                }
            ],
            "peer_reviews": [{"reviewer": "Reviewer 1", "response": "`Response A` strongest"}],
            "anonymization_mapping": [{"label": "Response A", "advisor": "Contrarian"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            payload_root = tmp_root / "payloads"
            payload_root.mkdir()
            output_path = payload_root / "council-payload.json"
            config_path = write_temp_runtime_config(tmp_root, payload_root)
            canonical = report_renderer.write_canonical_payload(
                raw,
                output_path,
                cleanup_enabled=True,
                config_path=config_path,
            )
            persisted = json.loads(output_path.read_text())

        self.assertEqual(canonical, persisted)
        self.assertEqual("council-verdict", persisted["type"])
        self.assertEqual(
            str(output_path.resolve(strict=False)),
            persisted["payload_source"],
        )
        self.assertEqual("Вопрос\nНужно ли это делать?", persisted["question"])
        self.assertEqual("Сформулированный вопрос", persisted["framed_question"])
        self.assertEqual("Сильный тезис", persisted["advisors"][0]["headline"])
        self.assertEqual("Keep bilingual", persisted["advisors"][0]["stance"])

    def test_write_canonical_payload_can_skip_cleanup(self):
        raw = {
            "type": "council-verdict",
            "title": "Тест",
            "timestamp": "2026-05-03 23:59:00",
            "question": "### Вопрос\n**Нужно ли это делать?**",
            "framed_question": "Нейтральный вопрос",
            "run_status": {
                "status": "degraded",
                "details": "Тестовая фикстура использует один ответ советника для режима без cleanup.",
            },
            "verdict": {
                "agrees": "`Response A` прав",
                "clashes": "**Есть разногласия**",
                "blind_spots": "[См. заметку](https://example.com)",
                "recommendation": "[[Совет|Совет]] один",
                "first_step": "1. Сделать шаг 1 2. Сделать шаг 2",
            },
            "advisors": [
                {
                    "name": "Contrarian",
                    "headline": "**Сильный тезис**",
                    "stance": "`Keep bilingual`",
                    "response": "### Ответ\n[[Note|Алиас]]",
                }
            ],
            "peer_reviews": [{"reviewer": "Reviewer 1", "response": "`Response A` strongest"}],
            "anonymization_mapping": [{"label": "Response A", "advisor": "Contrarian"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            payload_root = tmp_root / "payloads"
            payload_root.mkdir()
            output_path = payload_root / "council-payload.json"
            config_path = write_temp_runtime_config(tmp_root, payload_root)
            persisted = report_renderer.write_canonical_payload(
                raw,
                output_path,
                cleanup_enabled=False,
                config_path=config_path,
            )

        self.assertEqual("### Вопрос\n**Нужно ли это делать?**", persisted["question"])
        self.assertEqual("**Сильный тезис**", persisted["advisors"][0]["headline"])
        self.assertEqual("`Keep bilingual`", persisted["advisors"][0]["stance"])

    def test_load_payload_rejects_missing_required_fields(self):
        base = load_fixture("full_payload.json")
        for field in ("type", "title", "timestamp", "framed_question", "payload_source", "peer_reviews"):
            with self.subTest(field=field):
                payload = dict(base)
                payload.pop(field, None)
                with tempfile.TemporaryDirectory() as tmp:
                    payload_path = Path(tmp) / "payload.json"
                    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                    with self.assertRaises(ValueError):
                        report_renderer.load_payload(payload_path)

    def test_load_payload_rejects_full_run_without_five_advisors(self):
        payload = load_fixture("degraded_payload.json")
        payload.pop("run_status", None)
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            with self.assertRaisesRegex(
                ValueError,
                "may be `full` only when exactly 5 advisor responses were completed",
            ):
                report_renderer.load_payload(payload_path)

    def test_load_payload_rejects_full_run_without_peer_reviews(self):
        payload = load_fixture("full_payload.json")
        payload["peer_reviews"] = []
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            with self.assertRaisesRegex(
                ValueError,
                "may be `full` only when peer review responses are present",
            ):
                report_renderer.load_payload(payload_path)

    def test_load_payload_requires_degraded_details(self):
        payload = load_fixture("degraded_payload.json")
        payload["run_status"]["details"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            with self.assertRaisesRegex(
                ValueError,
                "Field 'run_status.details' must explain why the run was degraded",
            ):
                report_renderer.load_payload(payload_path)

    def test_report_frontmatter_escapes_single_line_scalars(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = write_temp_payload(Path(tmp), "frontmatter_injection_payload.json")
            payload = report_renderer.load_payload(payload_path)
            markdown = report_renderer.build_markdown(payload)

        frontmatter = markdown.split("---\n", 2)[1]
        self.assertIn("title: 'Нормальный заголовок malicious: true (164403)'", frontmatter)
        self.assertIn(
            "  - 'scratch/llm-council/council-payload-20260503-164403.json extra'",
            frontmatter,
        )
        self.assertIn("date: '2026 injected'", frontmatter)
        self.assertNotIn("\nmalicious:", frontmatter)

    def test_report_frontmatter_uses_payload_source(self):
        payload = load_fixture("full_payload.json")
        payload["payload_source"] = "scratch/llm-council/council-payload-20260503-164403.json"
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            loaded = report_renderer.load_payload(payload_path)
            markdown = report_renderer.build_markdown(loaded)

        frontmatter = markdown.split("---\n", 2)[1]
        self.assertIn(
            "  - 'scratch/llm-council/council-payload-20260503-164403.json'",
            frontmatter,
        )
        self.assertNotIn("council://", frontmatter)

    def test_report_reveals_only_response_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = write_temp_payload(Path(tmp), "full_payload.json")
            payload = report_renderer.load_payload(payload_path)
            markdown = report_renderer.build_markdown(payload)

        self.assertIn("[[#Contrarian|Contrarian]]", markdown)
        self.assertNotIn("`[[#Contrarian|Contrarian]]`", markdown)
        self.assertIn("вариант A", markdown)
        self.assertIn("вариант B", markdown)
        self.assertIn("A/B test", markdown)
        self.assertNotIn("Response A", markdown)

    def test_report_adds_status_section_for_degraded_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = write_temp_payload(Path(tmp), "degraded_payload.json")
            payload = report_renderer.load_payload(payload_path)
            markdown = report_renderer.build_markdown(payload)

        self.assertIn("## Статус прогона", markdown)
        self.assertIn("Этот прогон был деградированным", markdown)
        self.assertIn("Один советник упал дважды", markdown)

    def test_report_rejects_unknown_advisor_in_anonymization_mapping(self):
        payload = load_fixture("full_payload.json")
        payload["anonymization_mapping"][0]["advisor"] = "Wrong Name"
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            with self.assertRaisesRegex(
                ValueError, "Field 'anonymization_mapping' references unknown advisors"
            ):
                report_renderer.load_payload(payload_path)

    def test_write_verdict_note_delegates_full_writer_path(self):
        article_skill_dir = SKILL_DIR.parent / "article-to-obsidian-kb"
        script_path = article_skill_dir / "scripts" / "write_structured_note.py"
        spec = importlib.util.spec_from_file_location("article_writer_for_llm_council_test", script_path)
        assert spec is not None and spec.loader is not None
        article_writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(article_writer)

        payload = load_fixture("full_payload.json")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            verdict_root = tmp_root / "verdicts"
            verdict_root.mkdir()
            config_path = tmp_root / "runtime.local.toml"
            config_path.write_text(
                "[structured_note_roots]\n"
                f"council_verdict = \"{verdict_root}\"\n",
                encoding="utf-8",
            )
            payload_path = tmp_root / "payload.json"
            payload_root = tmp_root / "payloads"
            payload_root.mkdir()
            payload_path = payload_root / "payload.json"
            payload_config = write_temp_runtime_config(tmp_root, payload_root)
            report_renderer.write_canonical_payload(
                payload,
                payload_path,
                cleanup_enabled=True,
                config_path=payload_config,
            )
            loaded = report_renderer.load_payload(payload_path)

            output_path = report_renderer.write_verdict_note(
                loaded,
                config_path=config_path,
            )

            self.assertTrue(output_path.exists())
            self.assertEqual(
                (verdict_root / "Проверка deanonymization (164403).md").resolve(),
                output_path.resolve(),
            )
            violations = article_writer.collect_violations(
                output_path,
                expect="structured-council-verdict",
                require_intro_before_first_heading=False,
                check_title_matches_filename=False,
            )
            self.assertEqual([], violations)

    def test_end_to_end_payload_to_structured_note_chain(self):
        article_skill_dir = SKILL_DIR.parent / "article-to-obsidian-kb"
        script_path = article_skill_dir / "scripts" / "write_structured_note.py"
        spec = importlib.util.spec_from_file_location(
            "article_writer_for_llm_council_e2e_test",
            script_path,
        )
        assert spec is not None and spec.loader is not None
        article_writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(article_writer)

        raw_payload = {
            "type": "council-verdict",
            "title": "E2E council verdict",
            "timestamp": "2026-05-03 23:59:30",
            "question": "### Вопрос\n**Нужно ли сохранять два языка в профиле?**",
            "framed_question": "`Нейтральный` вопрос для совета",
            "run_status": {
                "status": "degraded",
                "details": "E2E тест использует сокращенный состав советников ради компактной фикстуры.",
            },
            "verdict": {
                "agrees": "`Response A` прав в оценке inbound риска",
                "clashes": "**Разногласие** по глубине русского слоя",
                "blind_spots": "[Outbound](https://example.com) недооценен",
                "recommendation": "[[English-first|English-first]] плюс короткий русский слой",
                "first_step": "1. Переписать Headline 2. Переписать About 3. Измерить inbound",
            },
            "advisors": [
                {
                    "name": "Contrarian",
                    "headline": "**Не режь полезный inbound**",
                    "stance": "`Keep bilingual`",
                    "response": "### Ответ\n[[Note|Русский слой нужен]]",
                },
                {
                    "name": "Executor",
                    "headline": "Нужен контролируемый второй слой",
                    "stance": "English-first with Russian support",
                    "response": "Практичнее сохранить единый narrative и короткий русский conversion layer.",
                },
            ],
            "peer_reviews": [
                {
                    "reviewer": "Reviewer 1",
                    "response": "1. Самый сильный: `Response B`. 2. Слабее всего `Response A`.",
                }
            ],
            "anonymization_mapping": [
                {"label": "Response A", "advisor": "Contrarian"},
                {"label": "Response B", "advisor": "Executor"},
            ],
            "related_notes": ["Поиск работы в зарубежных компаниях"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            verdict_root = tmp_root / "verdicts"
            verdict_root.mkdir()
            config_path = tmp_root / "runtime.local.toml"
            config_path.write_text(
                "[structured_note_roots]\n"
                f"council_verdict = \"{verdict_root}\"\n",
                encoding="utf-8",
            )
            payload_root = tmp_root / "payloads"
            payload_root.mkdir()
            canonical_payload_path = payload_root / "council-payload.json"
            payload_config = write_temp_runtime_config(tmp_root, payload_root)
            canonical = report_renderer.write_canonical_payload(
                raw_payload,
                canonical_payload_path,
                cleanup_enabled=True,
                config_path=payload_config,
            )
            loaded = report_renderer.load_payload(canonical_payload_path)
            output_path = report_renderer.write_verdict_note(
                loaded,
                config_path=config_path,
            )

            self.assertEqual(
                canonical["payload_source"],
                str(canonical_payload_path.resolve(strict=False)),
            )
            self.assertTrue(output_path.exists())
            text = output_path.read_text(encoding="utf-8")
            self.assertIn(str(canonical_payload_path.resolve(strict=False)), text)
            self.assertIn("## Вердикт совета", text)
            self.assertIn("## Позиции советников", text)
            self.assertIn("[[#Executor|Executor]]", text)
            self.assertNotIn("**Не режь полезный inbound**", text)
            self.assertNotIn("`Keep bilingual`", text)

            violations = article_writer.collect_violations(
                output_path,
                expect="structured-council-verdict",
                require_intro_before_first_heading=False,
                check_title_matches_filename=False,
            )
            self.assertEqual([], violations)

    def test_write_canonical_payload_rejects_output_outside_configured_temp_root(self):
        raw = load_fixture("degraded_payload.json")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            allowed_root = tmp_root / "payloads"
            allowed_root.mkdir()
            outside_root = tmp_root / "outside"
            outside_root.mkdir()
            output_path = outside_root / "council-payload.json"
            config_path = write_temp_runtime_config(tmp_root, allowed_root)
            with self.assertRaisesRegex(
                ValueError,
                "Output path must stay under one of the allowed roots",
            ):
                report_renderer.write_canonical_payload(
                    raw,
                    output_path,
                    cleanup_enabled=True,
                    config_path=config_path,
                )

if __name__ == "__main__":
    unittest.main()
