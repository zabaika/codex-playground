import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "detect_source_route.py"
)
SPEC = importlib.util.spec_from_file_location("detect_source_route", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
detect_route = MODULE.detect_route
load_source_text = MODULE.load_source_text


class DetectSourceRouteTests(unittest.TestCase):
    def write_source(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "source.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_zapier_operating_model_routes_to_engineering(self) -> None:
        source = self.write_source(
            "Using AI to accelerate hiring and productivity at Zapier\n\n"
            "Zapier built an internal ecosystem of lightweight AI agents that automate operational "
            "and administrative tasks across engineering. The operating model uses Slack as the "
            "coordination layer, sandbox channels for safe rollout, and feedback loops tied to "
            "throughput, meeting reduction, and agent governance.\n"
        )
        route, _, details = detect_route(load_source_text(source), "")
        self.assertEqual("engineering", route)
        self.assertIn("agent_operating_model", details["engineering_matches"])

    def test_harness_engineering_routes_to_engineering(self) -> None:
        source = self.write_source(
            "Harness engineering: leveraging Codex in an agent-first world\n\n"
            "OpenAI describes an agent-first engineering operating model where the repository is "
            "the system of record, AGENTS.md is a map, and the team relies on repository docs, "
            "mechanical checks, linters, worktrees, and architectural constraints.\n"
        )
        route, _, details = detect_route(load_source_text(source), "")
        self.assertEqual("engineering", route)
        self.assertIn("agent_operating_model", details["engineering_matches"])

    def test_hbr_future_of_work_routes_to_general(self) -> None:
        source = self.write_source(
            "AI Doesn’t Reduce Work—It Intensifies It\n\n"
            "The article argues that AI can intensify work by raising expectations, increasing "
            "throughput pressure, and contributing to burnout. It is broad future-of-work analysis, "
            "not a concrete engineering operating model.\n"
        )
        route, _, _ = detect_route(load_source_text(source), "")
        self.assertEqual("general", route)

    def test_job_search_source_routes_to_general(self) -> None:
        source = self.write_source(
            "How to improve your job search in the US tech market\n\n"
            "The material covers resume strategy, interviews, referrals, LinkedIn, recruiters, "
            "and visa constraints for candidates looking for work abroad.\n"
        )
        route, _, _ = detect_route(load_source_text(source), "")
        self.assertEqual("general", route)

    def test_load_source_text_reads_pdf_via_pdftotext(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "source.pdf"
        path.write_bytes(b"%PDF-1.7\n")
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout="Engineering platform workflow\n", stderr=""),
        ) as mocked_run:
            text = load_source_text(path)
        self.assertEqual("Engineering platform workflow", text)
        mocked_run.assert_called_once_with(
            ["pdftotext", str(path), "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=MODULE.PDFTOTEXT_TIMEOUT_SECONDS,
        )

    def test_load_source_text_raises_clear_error_when_pdftotext_is_missing(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "source.pdf"
        path.write_bytes(b"%PDF-1.7\n")
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            side_effect=FileNotFoundError("pdftotext not found"),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Could not extract text from PDF source .*Install `pdftotext` or provide an extracted text file",
            ):
                load_source_text(path)

    def test_load_source_text_raises_clear_error_when_pdftotext_times_out(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "source.pdf"
        path.write_bytes(b"%PDF-1.7\n")
        with mock.patch.object(
            MODULE.subprocess,
            "run",
            side_effect=MODULE.subprocess.TimeoutExpired(
                cmd=["pdftotext", str(path), "-"],
                timeout=MODULE.PDFTOTEXT_TIMEOUT_SECONDS,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                rf"Could not extract text from PDF source .*`pdftotext` exceeded {MODULE.PDFTOTEXT_TIMEOUT_SECONDS}s",
            ):
                load_source_text(path)


if __name__ == "__main__":
    unittest.main()
