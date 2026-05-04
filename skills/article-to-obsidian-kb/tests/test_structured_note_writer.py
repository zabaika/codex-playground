import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "write_structured_note.py"
CHECKER_PATH = SKILL_DIR / "scripts" / "check_note_contract.py"
FIXTURE_PAYLOAD = TESTS_DIR / "fixtures" / "council_verdict_payload.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WRITER = load_module(SCRIPT_PATH, "write_structured_note")
CHECKER = load_module(CHECKER_PATH, "structured_check_note_contract")


class StructuredNoteWriterTests(unittest.TestCase):
    def _valid_council_markdown(self) -> str:
        payload = WRITER.load_payload(FIXTURE_PAYLOAD)
        return WRITER.build_markdown(payload)

    def test_writer_requires_explicit_structured_mode(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--type",
                "council-verdict",
                "--payload",
                str(FIXTURE_PAYLOAD),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Default mode is `source`", result.stderr or result.stdout)

    def test_council_verdict_write_passes_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            verdict_root = tmp_root / "verdicts"
            verdict_root.mkdir()
            config_path = tmp_root / "runtime.local.toml"
            config_path.write_text(
                "[structured_note_roots]\n"
                f"council_verdict = \"{verdict_root}\"\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--mode",
                    "structured",
                    "--type",
                    "council-verdict",
                    "--payload",
                    str(FIXTURE_PAYLOAD),
                    "--config-path",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

            output_path = verdict_root / "Тестовый council verdict для CTO CIO.md"
            self.assertTrue(output_path.exists())
            text = output_path.read_text(encoding="utf-8")
            self.assertIn("type: council-verdict", text)
            self.assertIn("scratch/llm-council/council-payload-20260503-231500.json", text)
            self.assertNotIn("/Users/andrejzabaev/Documents/Playground/scratch/llm-council", text)
            self.assertIn("## Вердикт совета", text)
            self.assertIn("## Позиции советников", text)
            self.assertIn("## Взаимная проверка", text)
            self.assertIn("[[#Executor|Executor]]", text)

            violations = CHECKER.collect_violations(
                output_path,
                expect="structured-council-verdict",
                require_intro_before_first_heading=False,
            )
            self.assertEqual([], violations)

    def test_writer_rejects_duplicate_advisor_names(self) -> None:
        payload = json.loads(FIXTURE_PAYLOAD.read_text(encoding="utf-8"))
        payload["advisors"][1]["name"] = payload["advisors"][0]["name"]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            payload_path = tmp_root / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "Advisor names must be unique before anonymization mapping is validated",
            ):
                WRITER.load_payload(payload_path)

    def test_writer_rejects_duplicate_mapping_labels(self) -> None:
        payload = json.loads(FIXTURE_PAYLOAD.read_text(encoding="utf-8"))
        payload["anonymization_mapping"][1]["label"] = "Response A"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            payload_path = tmp_root / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "Field 'anonymization_mapping' must not contain duplicate labels",
            ):
                WRITER.load_payload(payload_path)

    def test_writer_rejects_duplicate_mapping_advisors(self) -> None:
        payload = json.loads(FIXTURE_PAYLOAD.read_text(encoding="utf-8"))
        payload["anonymization_mapping"][1]["advisor"] = payload["anonymization_mapping"][0]["advisor"]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            payload_path = tmp_root / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "Field 'anonymization_mapping' must not contain duplicate advisor names",
            ):
                WRITER.load_payload(payload_path)

    def test_display_source_path_relativizes_project_local_absolute_paths(self) -> None:
        absolute = "/Users/andrejzabaev/Documents/Playground/scratch/llm-council/council-payload-20260504-000458.json"
        self.assertEqual(
            "scratch/llm-council/council-payload-20260504-000458.json",
            WRITER.display_source_path(absolute),
        )

    def test_display_source_path_uses_project_root_override_for_installed_copy_layout(self) -> None:
        absolute = "/Users/andrejzabaev/Documents/Playground/scratch/llm-council/council-payload-20260504-000458.json"
        self.assertEqual(
            "scratch/llm-council/council-payload-20260504-000458.json",
            WRITER.display_source_path(
                absolute,
                config={"paths": {"project_root": "/Users/andrejzabaev/Documents/Playground"}},
            ),
        )

    def test_display_source_path_prefers_env_project_root_over_config(self) -> None:
        absolute = "/Users/andrejzabaev/Documents/Playground/scratch/llm-council/council-payload-20260504-000458.json"
        old_env = os.environ.get("CODEX_PLAYGROUND_PROJECT_ROOT")
        try:
            os.environ["CODEX_PLAYGROUND_PROJECT_ROOT"] = "/Users/andrejzabaev/Documents/Playground"
            self.assertEqual(
                "scratch/llm-council/council-payload-20260504-000458.json",
                WRITER.display_source_path(
                    absolute,
                    config={"paths": {"project_root": "/Users/andrejzabaev/WrongRoot"}},
                ),
            )
        finally:
            if old_env is None:
                os.environ.pop("CODEX_PLAYGROUND_PROJECT_ROOT", None)
            else:
                os.environ["CODEX_PLAYGROUND_PROJECT_ROOT"] = old_env

    def test_writer_does_not_create_destination_file_when_prewrite_validation_fails(self) -> None:
        payload = WRITER.load_payload(FIXTURE_PAYLOAD)
        invalid_markdown = "---\ntitle: 'bad'\nsource:\n  - 'scratch/x.json'\ntype: council-verdict\ntags:\n  - council-verdict\ndate: '2026'\n---\n## Вердикт совета\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            output_path = tmp_root / "invalid.md"
            original_build_markdown = WRITER.build_markdown
            try:
                WRITER.build_markdown = lambda _payload, config=None: invalid_markdown
                with self.assertRaisesRegex(ValueError, "Structured note contract failed"):
                    WRITER.write_structured_note(payload, output_path=output_path)
            finally:
                WRITER.build_markdown = original_build_markdown
            self.assertFalse(output_path.exists())

    def test_checker_rejects_council_section_order_regression(self) -> None:
        markdown = self._valid_council_markdown()
        advisors_start = markdown.index("## Позиции советников")
        reviews_start = markdown.index("## Взаимная проверка")
        related_start = markdown.index("# Связанные заметки")
        advisors_block = markdown[advisors_start:reviews_start]
        reviews_block = markdown[reviews_start:related_start]
        markdown = (
            markdown[:advisors_start]
            + reviews_block
            + advisors_block
            + markdown[related_start:]
        )
        violations = CHECKER.collect_violations_from_text(
            markdown,
            expect="structured-council-verdict",
            require_intro_before_first_heading=False,
            check_title_matches_filename=False,
        )
        self.assertTrue(
            any(v.code == "structure.invalid-council-section-order" for v in violations),
            violations,
        )

    def test_checker_rejects_broken_advisor_block_shape(self) -> None:
        markdown = self._valid_council_markdown().replace(
            "- **Позиция:** Keep bilingual\n",
            "",
            1,
        )
        violations = CHECKER.collect_violations_from_text(
            markdown,
            expect="structured-council-verdict",
            require_intro_before_first_heading=False,
            check_title_matches_filename=False,
        )
        self.assertTrue(
            any(v.code == "structure.invalid-advisor-stance-line" for v in violations),
            violations,
        )

    def test_checker_rejects_empty_peer_review_block(self) -> None:
        markdown = self._valid_council_markdown()
        markdown = markdown.replace(
            "### Reviewer 1\n1. Самый сильный: [[#Executor|Executor]]. Его practical move должен дожить до финального плана. 2. Самое большое слепое пятно у [[#Contrarian|Contrarian]]. 3. Все недооценили outbound networking.\n\n### Reviewer 2",
            "### Reviewer 1\n### Reviewer 2",
            1,
        )
        violations = CHECKER.collect_violations_from_text(
            markdown,
            expect="structured-council-verdict",
            require_intro_before_first_heading=False,
            check_title_matches_filename=False,
        )
        self.assertTrue(
            any(v.code == "structure.empty-peer-review-body" for v in violations),
            violations,
        )

    def test_checker_rejects_degraded_status_without_details(self) -> None:
        payload = WRITER.load_payload(FIXTURE_PAYLOAD)
        payload["run_status"] = {
            "status": "degraded",
            "details": "Короткая причина деградации.",
        }
        markdown = WRITER.build_markdown(payload).replace(
            "Этот прогон был деградированным.\nКороткая причина деградации.\n",
            "Этот прогон был деградированным.\n",
            1,
        )
        violations = CHECKER.collect_violations_from_text(
            markdown,
            expect="structured-council-verdict",
            require_intro_before_first_heading=False,
            check_title_matches_filename=False,
        )
        self.assertTrue(
            any(v.code == "structure.missing-degraded-status-details" for v in violations),
            violations,
        )


if __name__ == "__main__":
    unittest.main()
