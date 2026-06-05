from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import uuid
import zipfile

from job_search.application.dto.candidate_profile_draft import CandidateSourceRegistrationDTO
from job_search.application.services.artifact_path_service import ArtifactPathService
from job_search.domain.enums import ArtifactType, SourceKind, SourceOrigin
from job_search.infrastructure.repositories.artifact_repository import ArtifactRepository


@dataclass(slots=True)
class MaterializedSource:
    artifact_id: str
    artifact_type: str
    storage_path: str
    content_text: str
    content_hash: str
    created_file: bool


class CandidateSourceService:
    ALLOWED_FILE_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
    MAX_SOURCE_FILE_BYTES = 12 * 1024 * 1024
    MAX_DOCX_MEMBER_BYTES = 5 * 1024 * 1024
    PDF_READ_TIMEOUT_SECONDS = 20
    _DOCX_PARAGRAPH_RE = re.compile(r"<w:p\b[^>]*>(.*?)</w:p>", re.DOTALL)
    _DOCX_TEXT_RE = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.DOTALL)
    _DOCX_BREAK_RE = re.compile(r"<w:br\b[^>]*/>")
    _DOCX_TAB_RE = re.compile(r"<w:tab\b[^>]*/>")

    def __init__(self, artifact_repository: ArtifactRepository, artifact_root: Path) -> None:
        self._artifact_repository = artifact_repository
        self._artifact_root = artifact_root

    def materialize(self, source: CandidateSourceRegistrationDTO, *, candidate_label: str | None = None) -> MaterializedSource:
        content_text = self._load_content_text(source)
        content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        artifact_type = self._artifact_type_for(source.source_kind)
        existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=source.candidate_id,
            artifact_type=artifact_type,
            content_hash=content_hash,
        )
        if existing is not None:
            return MaterializedSource(
                artifact_id=existing["artifact_id"],
                artifact_type=existing["artifact_type"],
                storage_path=existing["storage_path"],
                content_text=Path(str(existing["storage_path"])).read_text(encoding="utf-8"),
                content_hash=existing["content_hash"],
                created_file=False,
            )

        artifact_id = str(uuid.uuid4())
        storage_path = self.artifact_storage_path(
            source.candidate_id,
            artifact_id,
            artifact_type,
            candidate_label=candidate_label,
            artifact_label=self._artifact_label_for(source),
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(content_text, encoding="utf-8")
        return MaterializedSource(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            storage_path=str(storage_path),
            content_text=content_text,
            content_hash=content_hash,
            created_file=True,
        )

    def _load_content_text(self, source: CandidateSourceRegistrationDTO) -> str:
        origin = SourceOrigin(source.source_origin)
        if origin is SourceOrigin.TEXT:
            if not source.content_text:
                raise ValueError("content_text is required for text sources")
            return source.content_text.strip()
        if origin is SourceOrigin.FILE:
            if not source.file_path:
                raise ValueError("file_path is required for file sources")
            return self._read_local_file(Path(source.file_path))
        if origin is SourceOrigin.URL:
            if not source.source_url:
                raise ValueError("source_url is required for URL sources")
            return source.content_text.strip() if source.content_text else source.source_url.strip()
        if origin is SourceOrigin.EXISTING_ARTIFACT:
            if not source.existing_artifact_id:
                raise ValueError("existing_artifact_id is required for existing artifact sources")
            existing = self._artifact_repository.get_artifact(source.existing_artifact_id)
            if existing is None:
                raise KeyError(f"Unknown artifact_id: {source.existing_artifact_id}")
            if str(existing.get("candidate_id")) != source.candidate_id:
                raise PermissionError("existing_artifact_id does not belong to the requested candidate")
            return Path(existing["storage_path"]).read_text(encoding="utf-8")
        raise ValueError(f"Unsupported source origin: {source.source_origin}")

    def _artifact_type_for(self, source_kind: str) -> str:
        kind = SourceKind(source_kind)
        if kind is SourceKind.RESUME:
            return ArtifactType.RESUME_SOURCE.value
        if kind is SourceKind.LINKEDIN:
            return ArtifactType.LINKEDIN_SOURCE.value
        return ArtifactType.PROFILE_SOURCE.value

    def artifact_storage_path(
        self,
        candidate_id: str,
        artifact_id: str,
        artifact_type: str,
        *,
        candidate_label: str | None = None,
        artifact_label: str | None = None,
    ) -> Path:
        return ArtifactPathService.candidate_artifact_path(
            artifact_root=self._artifact_root,
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            candidate_label=candidate_label,
            artifact_label=artifact_label,
        )

    def _artifact_label_for(self, source: CandidateSourceRegistrationDTO) -> str | None:
        if source.file_path:
            return Path(source.file_path).stem
        if source.source_url:
            return source.source_url
        return source.source_kind

    def _read_local_file(self, path: Path) -> str:
        self._validate_local_file(path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._read_pdf(path)
        if suffix == ".docx":
            return self._read_docx(path)
        return self._read_text_like(path)

    def _validate_local_file(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"Source file does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"Source path must be a file: {path}")
        suffix = path.suffix.lower()
        if suffix not in self.ALLOWED_FILE_EXTENSIONS:
            allowed = ", ".join(sorted(self.ALLOWED_FILE_EXTENSIONS))
            raise ValueError(f"Unsupported source file extension '{suffix}'. Allowed: {allowed}")
        size = path.stat().st_size
        if size > self.MAX_SOURCE_FILE_BYTES:
            raise ValueError(f"Source file is too large: {size} bytes; max {self.MAX_SOURCE_FILE_BYTES} bytes")

    def _read_text_like(self, path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "cp1251"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="replace")

    def _read_pdf(self, path: Path) -> str:
        pdftotext_path = shutil.which("pdftotext")
        if pdftotext_path is None:
            raise RuntimeError("pdftotext is required for PDF source ingestion")
        try:
            completed = subprocess.run(
                [pdftotext_path, str(path), "-"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.PDF_READ_TIMEOUT_SECONDS,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"pdftotext failed for {path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"pdftotext timed out for {path}") from exc
        return completed.stdout.strip()

    def _read_docx(self, path: Path) -> str:
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.file_size > self.MAX_DOCX_MEMBER_BYTES:
                        raise RuntimeError(f"DOCX member is too large: {member.filename}")
                xml_bytes = archive.read("word/document.xml")
        except (FileNotFoundError, KeyError, zipfile.BadZipFile) as exc:
            raise RuntimeError(f"Cannot read DOCX source: {path}") from exc
        return self._extract_docx_text(xml_bytes)

    def _extract_docx_text(self, xml_bytes: bytes) -> str:
        xml_text = xml_bytes.decode("utf-8", errors="replace")
        paragraphs: list[str] = []
        for paragraph_match in self._DOCX_PARAGRAPH_RE.finditer(xml_text):
            paragraph_xml = paragraph_match.group(1)
            paragraph_xml = self._DOCX_BREAK_RE.sub("\n", paragraph_xml)
            paragraph_xml = self._DOCX_TAB_RE.sub("\t", paragraph_xml)
            parts = [html.unescape(fragment.strip()) for fragment in self._DOCX_TEXT_RE.findall(paragraph_xml)]
            paragraph_text = "".join(part for part in parts if part)
            if paragraph_text:
                paragraphs.append(paragraph_text)
        return "\n".join(paragraphs).strip()
