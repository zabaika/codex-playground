from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit

from job_search.application.commands.vacancy import VacancyImportItem


GENERIC_URL_RE = re.compile(r"https?://[^\s),;]+|www\.[^\s),;]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class GenericVacancyTextExtraction:
    items: list[VacancyImportItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class GenericVacancyTextAdapter:
    """Extract local, user-provided vacancy blocks without platform credentials."""

    _LABEL_ALIASES = {
        "title": "title",
        "role": "title",
        "position": "title",
        "job_title": "title",
        "company": "company_name",
        "company_name": "company_name",
        "employer": "company_name",
        "location": "location_text",
        "job_location": "location_text",
        "url": "source_url",
        "job_url": "source_url",
        "source_url": "source_url",
        "link": "source_url",
    }

    def extract_from_text(self, content: str, *, source_origin: str) -> GenericVacancyTextExtraction:
        blocks = self._split_blocks(content)
        items: list[VacancyImportItem] = []
        warnings: list[str] = []

        for index, block in enumerate(blocks, start=1):
            labels = self._extract_labels(block)
            lines = self._content_lines(block)
            url = labels.get("source_url") or self._first_url(block)
            title = labels.get("title")
            company_name = labels.get("company_name")
            location_text = labels.get("location_text")
            if not title or not company_name:
                inferred = self._infer_title_company_location(lines)
                title = title or inferred.get("title")
                company_name = company_name or inferred.get("company_name")
                location_text = location_text or inferred.get("location_text")

            if not title or not company_name:
                warnings.append(f"block_{index}: vacancy text requires title and company before import")
                continue

            items.append(
                VacancyImportItem(
                    title=title,
                    company_name=company_name,
                    location_text=location_text or None,
                    source_url=self._normalize_url(url) if url else None,
                    raw_text=f"source_origin={source_origin}\n{block.strip()}",
                )
            )

        if not items and not warnings:
            warnings.append("no_importable_vacancy_blocks_found")
        return GenericVacancyTextExtraction(items=items, warnings=warnings)

    def _split_blocks(self, content: str) -> list[str]:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        return [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]

    def _extract_labels(self, block: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if not sep or not value.strip():
                continue
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
            mapped = self._LABEL_ALIASES.get(normalized_key)
            if mapped:
                labels[mapped] = value.strip()
        return labels

    def _content_lines(self, block: str) -> list[str]:
        lines: list[str] = []
        for line in block.splitlines():
            value = line.strip()
            if not value or GENERIC_URL_RE.fullmatch(value):
                continue
            key, sep, _ = value.partition(":")
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
            if sep and normalized_key in self._LABEL_ALIASES:
                continue
            lines.append(value)
        return lines

    def _infer_title_company_location(self, lines: list[str]) -> dict[str, str]:
        if len(lines) < 2:
            return {}
        title = lines[0]
        company_name, location_text = self._split_company_location(lines[1])
        if len(lines) >= 3 and not location_text:
            location_text = lines[2]
        return {"title": title, "company_name": company_name, "location_text": location_text}

    def _split_company_location(self, value: str) -> tuple[str, str]:
        for separator in (" · ", " - ", " | ", " — "):
            if separator in value:
                company, location = value.split(separator, 1)
                return company.strip(), location.strip()
        return value.strip(), ""

    def _first_url(self, block: str) -> str | None:
        match = GENERIC_URL_RE.search(block)
        return match.group(0) if match else None

    def _normalize_url(self, value: str) -> str:
        raw = value.strip().rstrip(".,;)")
        if raw.startswith("www."):
            raw = f"https://{raw}"
        parts = urlsplit(raw)
        path = parts.path.rstrip("/") or "/"
        return parts._replace(scheme=parts.scheme.lower() or "https", netloc=parts.netloc.lower(), path=path, fragment="").geturl()
