from __future__ import annotations

from pathlib import Path
import re


class ArtifactPathService:
    _CYRILLIC_TRANSLITERATION = str.maketrans(
        {
            "а": "a",
            "б": "b",
            "в": "v",
            "г": "g",
            "д": "d",
            "е": "e",
            "ё": "e",
            "ж": "zh",
            "з": "z",
            "и": "i",
            "й": "j",
            "к": "k",
            "л": "l",
            "м": "m",
            "н": "n",
            "о": "o",
            "п": "p",
            "р": "r",
            "с": "s",
            "т": "t",
            "у": "u",
            "ф": "f",
            "х": "h",
            "ц": "c",
            "ч": "ch",
            "ш": "sh",
            "щ": "sch",
            "ъ": "",
            "ы": "y",
            "ь": "",
            "э": "e",
            "ю": "yu",
            "я": "ya",
        }
    )

    @staticmethod
    def candidate_artifact_path(
        *,
        artifact_root: Path,
        candidate_id: str,
        artifact_id: str,
        artifact_type: str,
        candidate_label: str | None = None,
        artifact_label: str | None = None,
    ) -> Path:
        if artifact_type.endswith("_source"):
            folder = "sources"
        elif artifact_type.endswith("_final"):
            folder = "final"
        else:
            folder = "drafts"
        filename = ArtifactPathService.artifact_filename(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            artifact_label=artifact_label,
        )
        return artifact_root / "candidates" / ArtifactPathService.candidate_folder(candidate_id, candidate_label) / folder / filename

    @staticmethod
    def candidate_folder(candidate_id: str, candidate_label: str | None = None) -> str:
        short_id = candidate_id.split("-", 1)[0] or candidate_id[:8]
        slug = ArtifactPathService._slugify(candidate_label or "")
        if not slug:
            slug = "candidate"
        return f"{slug}--{short_id}"

    @staticmethod
    def artifact_filename(*, artifact_id: str, artifact_type: str, artifact_label: str | None = None) -> str:
        short_id = artifact_id.split("-", 1)[0] or artifact_id[:8]
        type_slug = ArtifactPathService._type_slug(artifact_type)
        label_slug = ArtifactPathService._slugify(artifact_label or "")
        suffix = ArtifactPathService._extension_for(artifact_type)
        if label_slug:
            return f"{type_slug}--{label_slug}--{short_id}{suffix}"
        return f"{type_slug}--{short_id}{suffix}"

    @staticmethod
    def _extension_for(artifact_type: str) -> str:
        if artifact_type == "candidate_profile_draft":
            return ".json"
        return ".md"

    @staticmethod
    def _type_slug(artifact_type: str) -> str:
        if artifact_type == "resume_markdown_final":
            return "resume-final"
        if artifact_type == "resume_vacancy":
            return "resume-vacancy"
        if artifact_type == "resume_vacancy_final":
            return "resume-vacancy-final"
        return ArtifactPathService._slugify(artifact_type.replace("_", "-")) or "artifact"

    @staticmethod
    def _slugify(value: str) -> str:
        lowered = value.strip().casefold().translate(ArtifactPathService._CYRILLIC_TRANSLITERATION)
        lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
        lowered = re.sub(r"-+", "-", lowered).strip("-")
        return lowered[:80]
