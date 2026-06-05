from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from job_search.application.commands.vacancy import VacancyImportItem
from job_search.infrastructure.board_adapters.generic_vacancy_text_adapter import GenericVacancyTextAdapter
from job_search.infrastructure.board_adapters.hh_ru_vacancy_adapter import HhRuVacancyAdapter
from job_search.infrastructure.board_adapters.linkedin_vacancy_adapter import LinkedInVacancyAdapter


class VacancyUrlEnrichmentService:
    def build_preview(
        self,
        *,
        seed: dict[str, Any],
        content_text: str,
        source_origin: str,
    ) -> dict[str, Any]:
        content = content_text.strip()
        if not content:
            raise ValueError("content_text is required for URL enrichment preview")
        source_url = str(seed["source_url"])
        platform = str(seed["platform"])
        extraction_content = self._content_with_seed_url(source_url, content, platform)
        extraction = self._extract(platform=platform, content=extraction_content, source_origin=source_origin)
        items = [self._item_with_seed_url(item, source_url) for item in extraction.items]
        warnings = list(extraction.warnings)
        if len(items) > 1:
            warnings.append("multiple_vacancies_extracted_from_single_url_seed")
        return {
            "url_seed_id": seed["url_seed_id"],
            "candidate_id": seed["candidate_id"],
            "platform": platform,
            "source_url": source_url,
            "source_origin": source_origin,
            "items": [asdict(item) for item in items],
            "warnings": warnings,
            "importable": len(items) == 1,
            "next_step": (
                "confirm-url-seed-import"
                if len(items) == 1
                else "Provide page text that yields exactly one vacancy before confirming import."
            ),
        }

    def infer_platform(self, source_url: str) -> str:
        host = urlsplit(source_url).netloc.lower()
        if "linkedin.com" in host:
            return "linkedin"
        if host == "hh.ru" or host.endswith(".hh.ru"):
            return "hh_ru"
        return "generic"

    def normalize_url(self, source_url: str) -> str:
        raw = source_url.strip().rstrip(".,;)")
        if raw.startswith("www."):
            raw = f"https://{raw}"
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("source_url must be an http(s) URL")
        path = parts.path.rstrip("/") or "/"
        return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), path=path, fragment="").geturl()

    def _extract(self, *, platform: str, content: str, source_origin: str):
        if platform == "linkedin":
            return LinkedInVacancyAdapter().extract_from_text(content, source_origin=source_origin)
        if platform == "hh_ru":
            return HhRuVacancyAdapter().extract_from_text(content, source_origin=source_origin)
        return GenericVacancyTextAdapter().extract_from_text(content, source_origin=source_origin)

    def _content_with_seed_url(self, source_url: str, content: str, platform: str) -> str:
        if source_url in content:
            return content
        if platform == "generic":
            return f"URL: {source_url}\n{content}"
        return f"{source_url}\n{content}"

    def _item_with_seed_url(self, item: VacancyImportItem, source_url: str) -> VacancyImportItem:
        source_url_value = item.source_url or source_url
        return VacancyImportItem(
            title=item.title,
            company_name=item.company_name,
            location_text=item.location_text,
            source_url=source_url_value,
            external_vacancy_id=item.external_vacancy_id,
            source_published_at=item.source_published_at,
            source_updated_at=item.source_updated_at,
            raw_text=item.raw_text,
        )
