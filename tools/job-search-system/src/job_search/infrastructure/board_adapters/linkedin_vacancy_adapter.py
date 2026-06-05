from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import StringIO
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_search.application.commands.vacancy import VacancyImportItem


LINKEDIN_JOB_URL_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/jobs/view/(?P<job_id>\d+)[^\s)]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LinkedInVacancyExtraction:
    items: list[VacancyImportItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class LinkedInVacancyAdapter:
    def extract_from_text(self, content: str, *, source_origin: str) -> LinkedInVacancyExtraction:
        csv_extraction = self._extract_from_csv_like_rows(content, source_origin=source_origin)
        if csv_extraction.items:
            return csv_extraction

        manual_page_extraction = self._extract_manual_page(content, source_origin=source_origin)
        if manual_page_extraction.items:
            return manual_page_extraction

        email_card_extraction = self._extract_from_markdown_email_cards(content, source_origin=source_origin)
        if email_card_extraction.items:
            return email_card_extraction

        search_result_extraction = self._extract_from_markdown_search_result_cards(content, source_origin=source_origin)
        if search_result_extraction.items:
            return search_result_extraction

        blocks = self._split_blocks(content)
        items: list[VacancyImportItem] = []
        warnings: list[str] = []

        for index, block in enumerate(blocks, start=1):
            url_match = LINKEDIN_JOB_URL_RE.search(block)
            if not url_match:
                continue

            url = self._normalize_linkedin_job_url(url_match.group(0))
            labels = self._extract_labels(block)
            lines = self._content_lines(block)

            title = labels.get("title")
            company_name = labels.get("company") or labels.get("company_name")
            location_text = labels.get("location")
            if not title or not company_name:
                inferred = self._infer_title_company_location(lines)
                title = title or inferred.get("title")
                company_name = company_name or inferred.get("company_name")
                location_text = location_text or inferred.get("location_text")

            if not title or not company_name:
                warnings.append(f"block_{index}: LinkedIn job URL requires title and company before import")
                continue

            items.append(
                VacancyImportItem(
                    title=title,
                    company_name=company_name,
                    location_text=location_text,
                    source_url=url,
                    external_vacancy_id=url_match.group("job_id"),
                    raw_text=f"source_origin={source_origin}\n{block.strip()}",
                )
            )

        if not items and not warnings:
            warnings.append("no_linkedin_job_urls_found")
        return LinkedInVacancyExtraction(items=items, warnings=warnings)

    def _extract_from_markdown_email_cards(self, content: str, *, source_origin: str) -> LinkedInVacancyExtraction:
        items_by_job_id: dict[str, VacancyImportItem] = {}
        for label, raw_url, job_id, _start, _end in self._iter_markdown_job_links(content):
            lines = self._content_lines(label)
            if len(lines) < 2:
                continue
            title = lines[0].strip()
            company_name, location_text = self._split_company_location(lines[1])
            if not title or not company_name or not location_text:
                continue
            location_text = self._clean_email_card_location(location_text)
            workplace_type = self._workplace_type_from_text(location_text)
            items_by_job_id[job_id] = VacancyImportItem(
                title=title,
                company_name=company_name,
                location_text=self._join_location_and_workplace(location_text, workplace_type),
                source_url=self._normalize_linkedin_job_url(raw_url),
                external_vacancy_id=job_id,
                raw_text=f"{self._raw_metadata_prefix(source_origin=source_origin, workplace_type=workplace_type)}\n{label.strip()}",
            )
        return LinkedInVacancyExtraction(items=list(items_by_job_id.values()))

    def _extract_from_markdown_search_result_cards(self, content: str, *, source_origin: str) -> LinkedInVacancyExtraction:
        links = self._iter_markdown_job_links(content)
        items_by_job_id: dict[str, VacancyImportItem] = {}
        for index, (title, raw_url, job_id, _start, end) in enumerate(links):
            next_start = links[index + 1][3] if index + 1 < len(links) else len(content)
            details = self._extract_search_result_card_details(content[end:next_start])
            company_name = details.get("company_name", "")
            location_text = details.get("location_text", "")
            if not title or not company_name or not location_text:
                continue
            workplace_type = self._workplace_type_from_text(location_text)
            items_by_job_id[job_id] = VacancyImportItem(
                title=title,
                company_name=company_name,
                location_text=self._join_location_and_workplace(location_text, workplace_type),
                source_url=self._normalize_linkedin_job_url(raw_url),
                external_vacancy_id=job_id,
                raw_text=f"{self._raw_metadata_prefix(source_origin=source_origin, workplace_type=workplace_type)}\n{title}\n{company_name}\n{location_text}",
            )
        return LinkedInVacancyExtraction(items=list(items_by_job_id.values()))

    def _iter_markdown_job_links(self, content: str) -> list[tuple[str, str, str, int, int]]:
        matches: list[tuple[str, str, str, int, int]] = []
        pattern = re.compile(
            r"\[(?P<label>[^\[\]]+?)\]\((?P<url>https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/(?P<job_id>\d+)[^)]*)\)",
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(content):
            matches.append(
                (
                    self._clean_markdown_label(match.group("label")),
                    match.group("url"),
                    match.group("job_id"),
                    match.start(),
                    match.end(),
                )
            )
        return matches

    def _extract_search_result_card_details(self, content: str) -> dict[str, str]:
        lines: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^-\s*", "", line).strip()
            if not line or line.startswith("![") or LINKEDIN_JOB_URL_RE.search(line):
                continue
            if line in {"Viewed", "Promoted", "Easy Apply"}:
                continue
            if "applicant" in line.casefold() or "connection" in line.casefold() or "review time" in line.casefold():
                continue
            lines.append(self._clean_markdown_label(line))
        if len(lines) < 2:
            return {}
        return {"company_name": lines[0], "location_text": lines[1]}

    def _clean_markdown_label(self, value: str) -> str:
        cleaned = value.strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"__(.*?)__", r"\1", cleaned, flags=re.DOTALL)
        return cleaned.strip("` \n\t")

    def _extract_manual_page(self, content: str, *, source_origin: str) -> LinkedInVacancyExtraction:
        url_match = LINKEDIN_JOB_URL_RE.search(content)
        lines = self._content_lines(content)
        if not any(line.lower() == "about the job" for line in content.splitlines()):
            return LinkedInVacancyExtraction()
        header = self._extract_manual_page_header(lines)
        title = header.get("title", "")
        company_name = header.get("company_name", "")
        location_text = header.get("location_text", "")
        if not title or not company_name:
            return LinkedInVacancyExtraction(warnings=["block_1: LinkedIn manual page copy requires title and company before import"])
        poster_requirements = self._extract_poster_requirements(content)
        raw_prefix = self._raw_metadata_prefix(
            source_origin=source_origin,
            workplace_type=header.get("workplace_type"),
            employment_type=header.get("employment_type"),
            poster_requirements=poster_requirements,
        )
        return LinkedInVacancyExtraction(
            items=[
                VacancyImportItem(
                    title=title,
                    company_name=company_name,
                    location_text=location_text or None,
                    source_url=self._normalize_linkedin_job_url(url_match.group(0)) if url_match else None,
                    external_vacancy_id=url_match.group("job_id") if url_match else None,
                    raw_text=f"{raw_prefix}\n{content.strip()}",
                )
            ],
            warnings=[] if url_match else ["block_1: LinkedIn job URL missing; imported without external_vacancy_id"],
        )

    def _split_blocks(self, content: str) -> list[str]:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
        if len(blocks) == 1 and len(LINKEDIN_JOB_URL_RE.findall(normalized)) > 1:
            parts = re.split(r"(?=https?://(?:www\.)?linkedin\.com/jobs/view/\d+)", normalized, flags=re.IGNORECASE)
            return [part.strip() for part in parts if part.strip()]
        return blocks

    def _extract_labels(self, block: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            normalized_key = key.strip().lower().replace(" ", "_")
            if normalized_key in {"title", "role", "company", "company_name", "location"} and value.strip():
                labels[normalized_key] = value.strip()
        return labels

    def _content_lines(self, block: str) -> list[str]:
        result: list[str] = []
        for line in block.splitlines():
            value = line.strip()
            if not value or LINKEDIN_JOB_URL_RE.search(value):
                continue
            result.append(value)
        return result

    def _extract_manual_page_header(self, lines: list[str]) -> dict[str, str]:
        compact_card = self._extract_compact_job_card(lines)
        if compact_card:
            metadata_index = self._first_linkedin_metadata_line_index(lines)
            employment_type = self._extract_employment_type(lines, start_index=metadata_index or 0)
            return {**compact_card, "employment_type": employment_type}
        metadata_index = self._first_linkedin_metadata_line_index(lines)
        if metadata_index is None or metadata_index < 2:
            return self._infer_title_company_location(lines)
        company_name = lines[metadata_index - 2].strip()
        title = lines[metadata_index - 1].strip()
        location = lines[metadata_index].split("·", 1)[0].strip()
        workplace_type = self._extract_workplace_type(lines, start_index=metadata_index + 1)
        employment_type = self._extract_employment_type(lines, start_index=metadata_index + 1)
        location_text = self._join_location_and_workplace(location, workplace_type)
        return {
            "title": title,
            "company_name": company_name,
            "location_text": location_text,
            "workplace_type": workplace_type,
            "employment_type": employment_type,
        }

    def _extract_compact_job_card(self, lines: list[str]) -> dict[str, str]:
        for index in range(0, max(0, len(lines) - 1)):
            title = lines[index].strip()
            details = lines[index + 1].strip()
            if not title or " · " not in details:
                continue
            normalized_details = details.casefold()
            if " ago" in normalized_details or "applicant" in normalized_details:
                continue
            company_name, location_text = self._split_company_location(details)
            if not company_name or not location_text:
                continue
            workplace_type = self._workplace_type_from_text(location_text) or self._extract_workplace_type(lines, start_index=index + 2)
            return {
                "title": title,
                "company_name": company_name,
                "location_text": self._join_location_and_workplace(location_text, workplace_type),
                "workplace_type": workplace_type,
            }
        return {}

    def _extract_workplace_type(self, lines: list[str], *, start_index: int) -> str:
        for line in lines[start_index:]:
            normalized = line.strip().casefold()
            if normalized == "about the job":
                break
            if self._is_workplace_type(normalized):
                return line.strip()
        return ""

    def _extract_employment_type(self, lines: list[str], *, start_index: int) -> str:
        for line in lines[start_index:]:
            normalized = line.strip().casefold()
            if normalized == "about the job":
                break
            if self._is_employment_type(normalized):
                return line.strip()
        return ""

    def _workplace_type_from_text(self, value: str) -> str:
        match = re.search(r"\((remote|hybrid|on-site|onsite|on site)\)", value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        for token in ("Remote", "Hybrid", "On-site", "Onsite", "On site"):
            if token.casefold() in value.casefold():
                return token
        return ""

    def _is_workplace_type(self, normalized_line: str) -> bool:
        return normalized_line in {"remote", "hybrid", "on-site", "onsite", "on site"}

    def _is_employment_type(self, normalized_line: str) -> bool:
        return normalized_line in {
            "full-time",
            "full time",
            "part-time",
            "part time",
            "contract",
            "temporary",
            "internship",
            "volunteer",
            "freelance",
        }

    def _clean_email_card_location(self, value: str) -> str:
        cleaned = value.strip()
        for marker in (
            "Actively recruiting",
            "Easy Apply",
            "Premium",
            "Fast growing",
        ):
            cleaned = re.sub(rf"\s*\b{re.escape(marker)}\b\s*", " ", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _join_location_and_workplace(self, location: str, workplace_type: str) -> str:
        if not workplace_type:
            return location
        normalized_location = re.sub(
            rf"\s*\(\s*{re.escape(workplace_type)}\s*\)\s*",
            " ",
            location,
            flags=re.IGNORECASE,
        ).strip()
        location = normalized_location or location
        if not location:
            return workplace_type
        if workplace_type.casefold() in location.casefold():
            return location
        return f"{location} · {workplace_type}"

    def _raw_metadata_prefix(
        self,
        *,
        source_origin: str,
        workplace_type: str | None = None,
        employment_type: str | None = None,
        poster_requirements: list[str] | None = None,
    ) -> str:
        lines = [f"source_origin={source_origin}"]
        if workplace_type:
            lines.append(f"linkedin_workplace_type={workplace_type}")
        if employment_type:
            lines.append(f"linkedin_employment_type={employment_type}")
        if poster_requirements:
            lines.append(f"linkedin_poster_requirements_json={json.dumps(poster_requirements, ensure_ascii=False)}")
        return "\n".join(lines)

    def _extract_poster_requirements(self, content: str) -> list[str]:
        marker = "requirements added by the job poster"
        lines = self._content_lines(content)
        start_index = None
        for index, line in enumerate(lines):
            if line.strip().casefold() == marker:
                start_index = index + 1
                break
        if start_index is None:
            return []
        requirements: list[str] = []
        for line in lines[start_index:]:
            value = line.strip().lstrip("•-*").strip()
            if not value:
                continue
            requirements.append(value)
        return requirements[:20]

    def _first_linkedin_metadata_line_index(self, lines: list[str]) -> int | None:
        for index, line in enumerate(lines):
            normalized = line.lower()
            if " · " in line and (" ago" in normalized or "applicant" in normalized):
                return index
        return None

    def _infer_title_company_location(self, lines: list[str]) -> dict[str, str]:
        if not lines:
            return {}
        title = lines[0]
        company_name = ""
        location_text = ""
        if len(lines) >= 2:
            company_name, location_text = self._split_company_location(lines[1])
        if len(lines) >= 3 and (not company_name or not location_text):
            company_name = lines[1]
            location_text = location_text or lines[2]
        return {"title": title, "company_name": company_name, "location_text": location_text}

    def _split_company_location(self, value: str) -> tuple[str, str]:
        for separator in (" · ", " - ", " | "):
            if separator in value:
                company, location = value.split(separator, 1)
                return company.strip(), location.strip()
        return value.strip(), ""

    def _normalize_linkedin_job_url(self, value: str) -> str:
        parts = urlsplit(value.strip().rstrip(".,;)"))
        path = parts.path.rstrip("/") or "/"
        if path.startswith("/comm/jobs/view/"):
            path = path.removeprefix("/comm")
        if re.fullmatch(r"/jobs/view/\d+", path):
            return urlunsplit(("https", "www.linkedin.com", path, "", ""))
        query = [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"trackingid", "trk"}
        ]
        return urlunsplit(("https", "www.linkedin.com", path, urlencode(query), ""))

    def _extract_from_csv_like_rows(self, content: str, *, source_origin: str) -> LinkedInVacancyExtraction:
        sample = content.lstrip()
        if not sample or "," not in sample.splitlines()[0]:
            return LinkedInVacancyExtraction()

        try:
            reader = csv.DictReader(StringIO(content))
        except csv.Error:
            return LinkedInVacancyExtraction()
        if not reader.fieldnames:
            return LinkedInVacancyExtraction()

        field_map = {self._normalize_header(name): name for name in reader.fieldnames if name}
        title_key = self._first_present(field_map, ["title", "job_title", "position", "role"])
        company_key = self._first_present(field_map, ["company", "company_name", "employer"])
        location_key = self._first_present(field_map, ["location", "job_location"])
        url_key = self._first_present(field_map, ["job_url", "url", "link", "linkedin_url", "source_url"])
        if not title_key or not company_key or not url_key:
            return LinkedInVacancyExtraction()

        items: list[VacancyImportItem] = []
        warnings: list[str] = []
        for index, row in enumerate(reader, start=2):
            title = str(row.get(title_key) or "").strip()
            company_name = str(row.get(company_key) or "").strip()
            location_text = str(row.get(location_key) or "").strip() if location_key else ""
            raw_url = str(row.get(url_key) or "").strip()
            url_match = LINKEDIN_JOB_URL_RE.search(raw_url)
            if not title or not company_name or not url_match:
                warnings.append(f"csv_row_{index}: CSV-like LinkedIn row requires title, company and LinkedIn job URL")
                continue
            url = self._normalize_linkedin_job_url(url_match.group(0))
            items.append(
                VacancyImportItem(
                    title=title,
                    company_name=company_name,
                    location_text=location_text or None,
                    source_url=url,
                    external_vacancy_id=url_match.group("job_id"),
                    raw_text=f"source_origin={source_origin}\n{row}",
                )
            )
        return LinkedInVacancyExtraction(items=items, warnings=warnings)

    def _normalize_header(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

    def _first_present(self, field_map: dict[str, str], candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in field_map:
                return field_map[candidate]
        return None
