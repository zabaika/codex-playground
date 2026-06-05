from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_search.application.services.input_validation_service import InputValidationService


class VacancyNormalizationService:
    def normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        title = InputValidationService.required_string(item.get("title"), "title", max_length=250)
        company_name = InputValidationService.required_string(item.get("company_name"), "company_name", max_length=250)
        location_text = str(item.get("location_text") or "").strip() or None
        normalized_title = self._normalize_text(title)
        normalized_company_name = self._normalize_text(company_name)
        normalized_location_text = self._normalize_text(location_text or "") or None
        source_url = self._normalize_source_url(item.get("source_url"))
        dedupe_key = hashlib.sha256(
            "|".join(
                [
                    normalized_title,
                    normalized_company_name,
                    normalized_location_text or "",
                ]
            ).encode("utf-8")
        ).hexdigest()
        raw_payload = {
            "title": title,
            "company_name": company_name,
            "location_text": location_text,
            "source_url": source_url,
            "external_vacancy_id": item.get("external_vacancy_id"),
            "source_published_at": item.get("source_published_at"),
            "source_updated_at": item.get("source_updated_at"),
            "raw_text": item.get("raw_text"),
        }
        content_hash = hashlib.sha256(json.dumps(raw_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return {
            "title": title,
            "company_name": company_name,
            "location_text": location_text,
            "normalized_title": normalized_title,
            "normalized_company_name": normalized_company_name,
            "normalized_location_text": normalized_location_text,
            "dedupe_key": dedupe_key,
            "raw_payload": raw_payload,
            "content_hash": content_hash,
            "source_url": source_url,
            "external_vacancy_id": item.get("external_vacancy_id"),
        }

    def _normalize_text(self, value: str) -> str:
        lowered = value.lower().strip()
        lowered = re.sub(r"\s+", " ", lowered)
        lowered = re.sub(r"[^\w\sа-яё/-]+", "", lowered, flags=re.IGNORECASE)
        return lowered.strip()

    def _normalize_source_url(self, value: object) -> str | None:
        raw = str(value or "").strip().rstrip(".,;)")
        if not raw:
            return None
        if raw.startswith("www."):
            raw = f"https://{raw}"
        InputValidationService.optional_http_url(raw, "source_url")
        parts = urlsplit(raw)
        scheme = parts.scheme.lower() or "https"
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"
        kept_query = [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "yclid"}
        ]
        return urlunsplit((scheme, netloc, path, urlencode(kept_query), ""))
