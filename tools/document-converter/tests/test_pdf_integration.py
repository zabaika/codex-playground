from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOL_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    importlib.util.find_spec("pymupdf"), "PyMuPDF is required for PDF integration"
)
class PdfIntegrationTests(unittest.TestCase):
    def test_dry_run_json_and_pdf_index(self) -> None:
        import pymupdf

        scratch = TOOL_ROOT.parents[1] / "scratch/document-converter-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            pdf_path = source / "sample.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((50, 50), "PDF integration regression")
                pdf.save(pdf_path)
            original = pdf_path.read_bytes()
            original_mtime = pdf_path.stat().st_mtime_ns
            dest = root / "output"
            command = [
                sys.executable,
                str(TOOL_ROOT / "bin/document-converter"),
                str(source),
                str(dest),
                "--build-index",
            ]
            dry_run = subprocess.run(
                [*command, "--dry-run"],
                capture_output=True, text=True, check=True, timeout=90,
            )
            plan = json.loads(dry_run.stdout)
            self.assertTrue(plan["dependencies"]["pymupdf"]["available"])
            self.assertFalse(dest.exists())
            subprocess.run(
                [*command, "--force"],
                capture_output=True, text=True, check=True, timeout=90,
            )
            index = dest / "index"
            report = json.loads((index / "conversion-report.json").read_text())
            self.assertTrue(report["source_unchanged"])
            self.assertEqual(report["status_counts"].get("failed", 0), 0)
            documents = [
                json.loads(line)
                for line in (index / "documents.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["index_backend"], "pymupdf")
            chunks = (index / "chunks.jsonl").read_text()
            self.assertIn("PDF integration regression", chunks)
            self.assertEqual(pdf_path.read_bytes(), original)
            self.assertEqual(pdf_path.stat().st_mtime_ns, original_mtime)


if __name__ == "__main__":
    unittest.main()
