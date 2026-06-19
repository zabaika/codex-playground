import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "audit_schema_heading_literals.py"
)
SPEC = importlib.util.spec_from_file_location("audit_schema_heading_literals", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

collect_findings = MODULE.collect_findings
heading = MODULE.HEADINGS.__getitem__
main = MODULE.main


class AuditSchemaHeadingLiteralsTests(unittest.TestCase):
    def _copy_minimal_skill(self, tmpdir: str) -> Path:
        source = Path(__file__).resolve().parents[1]
        target = Path(tmpdir) / "skill"
        for subdir in ["config", "references", "scripts", "tests", "templates"]:
            (target / subdir).mkdir(parents=True, exist_ok=True)
        shutil.copy(source / "config" / "note_schema.yaml", target / "config" / "note_schema.yaml")
        return target

    def test_reports_rendered_heading_literal_in_reference_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = self._copy_minimal_skill(tmpdir)
            (skill_dir / "references" / "sample.md").write_text(
                f"Use `{heading('evidence')}` here.\n",
                encoding="utf-8",
            )
            findings = collect_findings(skill_dir)
        self.assertEqual(["headings.evidence"], [finding.heading_key for finding in findings])

    def test_allows_schema_key_in_reference_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = self._copy_minimal_skill(tmpdir)
            (skill_dir / "references" / "sample.md").write_text(
                "Use `headings.evidence` here.\n",
                encoding="utf-8",
            )
            findings = collect_findings(skill_dir)
        self.assertEqual([], findings)

    def test_allows_markdown_fixture_specimens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = self._copy_minimal_skill(tmpdir)
            fixture_dir = skill_dir / "tests" / "fixtures"
            fixture_dir.mkdir(parents=True, exist_ok=True)
            (fixture_dir / "sample.md").write_text(
                f"{heading('evidence')}\n- 2026-01-01: факт.\n",
                encoding="utf-8",
            )
            findings = collect_findings(skill_dir)
        self.assertEqual([], findings)

    def test_cli_success_for_clean_skill_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = self._copy_minimal_skill(tmpdir)
            (skill_dir / "references" / "sample.md").write_text(
                "Use `headings.evidence` here.\n",
                encoding="utf-8",
            )
            exit_code = main(["--skill-dir", str(skill_dir)])
        self.assertEqual(0, exit_code)


if __name__ == "__main__":
    unittest.main()
