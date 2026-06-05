from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from job_search.application.services.candidate_source_service import CandidateSourceService


class _ArtifactRepositoryStub:
    def find_reusable_artifact(self, **kwargs):
        return None


class CandidateSourceServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.service = CandidateSourceService(_ArtifactRepositoryStub(), self.root / "artifacts")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rejects_unsupported_file_extension(self) -> None:
        path = self.root / "resume.exe"
        path.write_text("not a resume", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Unsupported source file extension"):
            self.service._read_local_file(path)

    def test_rejects_too_large_source_file(self) -> None:
        path = self.root / "resume.txt"
        path.write_bytes(b"x" * (CandidateSourceService.MAX_SOURCE_FILE_BYTES + 1))

        with self.assertRaisesRegex(ValueError, "too large"):
            self.service._read_local_file(path)

    def test_rejects_docx_with_too_large_member(self) -> None:
        path = self.root / "resume.docx"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "x" * (CandidateSourceService.MAX_DOCX_MEMBER_BYTES + 1))

        with self.assertRaisesRegex(RuntimeError, "DOCX member is too large"):
            self.service._read_local_file(path)

    def test_reads_docx_without_xml_parser(self) -> None:
        path = self.root / "resume.docx"
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Example Candidate</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Platform Engineering</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)

        text = self.service._read_local_file(path)
        self.assertIn("Example Candidate", text)
        self.assertIn("Platform Engineering", text)


if __name__ == "__main__":
    unittest.main()
