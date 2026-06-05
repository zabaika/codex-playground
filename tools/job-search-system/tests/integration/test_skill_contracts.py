from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND_ROOT = PROJECT_ROOT.parents[1]
SKILLS_ROOT = PLAYGROUND_ROOT / "skills"
DOCS_ROOT = PROJECT_ROOT / "docs"


class JobSearchSkillContractTest(unittest.TestCase):
    def test_jss_skills_have_valid_frontmatter_and_runtime_contract(self) -> None:
        expected = {
            "jss-candidate-intake",
            "jss-resume-positioning",
            "jss-vacancy-pipeline",
            "jss-job-board-operations",
            "jss-job-search-playbook",
            "jss-career-pathing",
        }
        for skill_name in expected:
            with self.subTest(skill_name=skill_name):
                skill_dir = SKILLS_ROOT / skill_name
                skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                frontmatter, body = self._split_frontmatter(skill_text)

                self.assertEqual(frontmatter.get("name"), skill_name)
                self.assertTrue(frontmatter.get("description"))
                self.assertIn("API-lite", body)
                self.assertIn("CLI", body)
                self.assertIn("Never write SQLite directly.", body)
                self.assertTrue((skill_dir / "references" / "commands.md").is_file())
                self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())
                self.assertTrue((skill_dir / "install-local.sh").is_file())
                agent_text = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
                self.assertIn('display_name: "JSS ', agent_text)
                self.assertIn("short_description:", agent_text)
                self.assertIn("default_prompt:", agent_text)

    def test_job_board_skill_stays_manual_sync_only(self) -> None:
        skill_text = (SKILLS_ROOT / "jss-job-board-operations" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("manual-sync", skill_text)
        self.assertIn("Never use browser automation", skill_text)
        self.assertIn("record-board-action", skill_text)
        self.assertIn("board-checklist", skill_text)

    def test_capability_coverage_register_marks_each_matrix_row(self) -> None:
        coverage_text = (DOCS_ROOT / "capability-coverage.md").read_text(encoding="utf-8")
        backlog_text = (DOCS_ROOT / "stage3-backlog.md").read_text(encoding="utf-8")

        self.assertIn("docs/capability-coverage.md", backlog_text)
        self.assertIn("## Implementation Groups", backlog_text)
        self.assertIn("### 15. Workflow UI And UI-Specific API Expansion", backlog_text)
        self.assertIn("Source docs:", backlog_text)
        self.assertIn("intentionally ignores `job-search-skills-design-backup.md`", coverage_text)

        valid_statuses = (
            "backend/API/CLI implemented",
            "skill wrapper implemented",
            "explicitly deferred to stage3-backlog.md",
        )
        in_matrix = False
        checked_rows = 0
        for line in coverage_text.splitlines():
            if line == "## Stage 1 / Stage 2 Coverage Matrix":
                in_matrix = True
                continue
            if in_matrix and line.startswith("## "):
                break
            if not in_matrix or not line.startswith("| ") or line.startswith("| Capability") or line.startswith("| ---"):
                continue
            checked_rows += 1
            self.assertTrue(any(status in line for status in valid_statuses), f"Unmarked capability row: {line}")
            if "explicitly deferred to stage3-backlog.md" in line:
                self.assertIn("Stage 3 group:", line, f"Deferred capability has no Stage 3 group: {line}")
        self.assertGreater(checked_rows, 40)

    def _split_frontmatter(self, text: str) -> tuple[dict[str, str], str]:
        self.assertTrue(text.startswith("---\n"))
        end = text.find("\n---\n", 4)
        self.assertNotEqual(end, -1)
        raw_frontmatter = text[4:end]
        body = text[end + 5 :]
        parsed: dict[str, str] = {}
        for line in raw_frontmatter.splitlines():
            key, sep, value = line.partition(":")
            self.assertTrue(sep, f"Invalid frontmatter line: {line}")
            parsed[key.strip()] = value.strip().strip('"')
        return parsed, body


if __name__ == "__main__":
    unittest.main()
