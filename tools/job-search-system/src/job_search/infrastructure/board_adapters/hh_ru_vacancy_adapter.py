from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit, urlunsplit

from job_search.application.commands.vacancy import VacancyImportItem


HH_VACANCY_URL_RE = re.compile(r"https?://(?:www\.)?hh\.ru/vacancy/(?P<vacancy_id>\d+)[^\s)]*", re.IGNORECASE)
HH_EMPLOYER_URL_RE = re.compile(r"https?://(?:www\.)?hh\.ru/employer/\d+[^\s)]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class HhRuVacancyExtraction:
    items: list[VacancyImportItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class HhRuVacancyAdapter:
    """Extract user-provided hh.ru vacancy/search-result text without browser automation."""

    def extract_from_text(self, content: str, *, source_origin: str) -> HhRuVacancyExtraction:
        vacancy_page_extraction = self._extract_vacancy_page(content, source_origin=source_origin)
        if vacancy_page_extraction.items:
            return vacancy_page_extraction

        links = self._iter_vacancy_links(content)
        items: list[VacancyImportItem] = []
        warnings: list[str] = []
        for index, (title, raw_url, vacancy_id, _start, end) in enumerate(links, start=1):
            next_start = links[index][3] if index < len(links) else len(content)
            details = self._extract_card_details(content[end:next_start])
            company_name = details.get("company_name", "")
            if not title or not company_name:
                warnings.append(f"block_{index}: hh.ru vacancy card requires title and company before import")
                continue
            location_text = self._join_location_and_work_model(details.get("location_text", ""), details.get("work_model", ""))
            raw_text = self._raw_metadata_prefix(
                source_origin=source_origin,
                salary_text=details.get("salary_text", ""),
                experience_text=details.get("experience_text", ""),
                work_model=details.get("work_model", ""),
            )
            items.append(
                VacancyImportItem(
                    title=title,
                    company_name=company_name,
                    location_text=location_text or None,
                    source_url=self._normalize_hh_url(raw_url),
                    external_vacancy_id=vacancy_id,
                    raw_text=f"{raw_text}\n{content[end:next_start].strip()}",
                )
            )
        if not items and not warnings:
            warnings.append("no_hh_ru_vacancy_links_found")
        return HhRuVacancyExtraction(items=items, warnings=warnings)

    def _extract_vacancy_page(self, content: str, *, source_origin: str) -> HhRuVacancyExtraction:
        lines = self._content_lines(content)
        if len(lines) < 3:
            return HhRuVacancyExtraction()
        url_match = HH_VACANCY_URL_RE.fullmatch(lines[0])
        if not url_match:
            return HhRuVacancyExtraction()
        title = self._clean_markdown_text(lines[1])
        details = self._extract_vacancy_page_details(lines)
        company_name = details.get("company_name", "")
        if not title or not company_name:
            return HhRuVacancyExtraction(warnings=["block_1: hh.ru vacancy page requires title and company before import"])
        raw_text = self._raw_metadata_prefix(
            source_origin=source_origin,
            salary_text=details.get("salary_text", ""),
            experience_text=details.get("experience_text", ""),
            work_model=details.get("work_model", ""),
            published_at=details.get("published_at", ""),
            updated_at=details.get("updated_at", ""),
        )
        return HhRuVacancyExtraction(
            items=[
                VacancyImportItem(
                    title=title,
                    company_name=company_name,
                    location_text=self._join_location_and_work_model(details.get("location_text", ""), details.get("work_model", "")) or None,
                    source_url=self._normalize_hh_url(lines[0]),
                    external_vacancy_id=url_match.group("vacancy_id"),
                    source_published_at=details.get("published_at") or None,
                    source_updated_at=details.get("updated_at") or None,
                    raw_text=f"{raw_text}\n{content.strip()}",
                )
            ]
        )

    def _extract_vacancy_page_details(self, lines: list[str]) -> dict[str, str]:
        salary_text = self._first_matching(lines[2:8], lambda value: self._is_salary_line(value) or "доход" in value.casefold())
        experience_text = self._first_matching(lines[2:10], lambda value: value.casefold().startswith("опыт "))
        work_model = self._extract_vacancy_page_work_model(lines)
        company_index = self._vacancy_page_company_index(lines)
        company_name = self._clean_markdown_text(lines[company_index]) if company_index is not None else ""
        location_text = self._extract_vacancy_page_location(lines, company_index=company_index)
        published_at, updated_at = self._extract_vacancy_page_dates(lines)
        return {
            "company_name": company_name,
            "salary_text": salary_text,
            "experience_text": experience_text,
            "work_model": work_model,
            "location_text": location_text,
            "published_at": published_at,
            "updated_at": updated_at,
        }

    def _extract_vacancy_page_work_model(self, lines: list[str]) -> str:
        for line in lines[:20]:
            normalized = line.casefold()
            if "формат работы:" in normalized and ("удал" in normalized or "удалён" in normalized):
                return "Remote"
            if "можно удал" in normalized or "можно удалён" in normalized:
                return "Remote"
            if "формат работы:" in normalized and "на месте" in normalized:
                return "On-site"
        return ""

    def _vacancy_page_company_index(self, lines: list[str]) -> int | None:
        for index, line in enumerate(lines[2:25], start=2):
            normalized = line.casefold()
            if self._is_service_line(line) or self._is_salary_line(line) or normalized.startswith("опыт "):
                continue
            if ":" in line or len(line) > 70:
                continue
            if index + 1 < len(lines) and self._clean_markdown_text(lines[index + 1]) == self._clean_markdown_text(line):
                return index
            if index + 2 < len(lines) and re.fullmatch(r"\d+[,.]\d+", lines[index + 1].strip()):
                return index
        return None

    def _extract_vacancy_page_location(self, lines: list[str], *, company_index: int | None) -> str:
        footer_location = self._extract_footer_work_location(lines)
        if footer_location:
            return footer_location
        if company_index is None:
            return ""
        for line in lines[company_index + 1 : company_index + 10]:
            clean = self._clean_markdown_text(line)
            if not clean or clean == self._clean_markdown_text(lines[company_index]):
                continue
            if self._is_service_line(clean) or re.fullmatch(r"\d+[,.]\d+", clean):
                continue
            if self._looks_like_description(clean):
                break
            if "," in clean or any(marker in clean.casefold() for marker in ("москва", "санкт", "офис", "метро")):
                return clean
        return ""

    def _extract_footer_work_location(self, lines: list[str]) -> str:
        for index, line in enumerate(lines):
            if line.casefold() != "где предстоит работать":
                continue
            for candidate in lines[index + 1 : index + 6]:
                clean = self._clean_markdown_text(candidate)
                if not clean or self._is_service_line(clean):
                    continue
                if "показать на" in clean.casefold() or clean.startswith("©"):
                    break
                return clean
        return ""

    def _extract_vacancy_page_dates(self, lines: list[str]) -> tuple[str, str]:
        for line in lines[-12:]:
            normalized = line.casefold()
            if "вакансия опубликована" in normalized:
                return self._parse_hh_ru_date(line), ""
            if "вакансия обновлена" in normalized:
                return "", self._parse_hh_ru_date(line)
        return "", ""

    def _iter_vacancy_links(self, content: str) -> list[tuple[str, str, str, int, int]]:
        pattern = re.compile(
            r"(?<!!)\[(?P<title>[^\[\]]+?)\]\((?P<url>https?://(?:www\.)?hh\.ru/vacancy/(?P<vacancy_id>\d+)[^)]*)\)",
            re.IGNORECASE | re.DOTALL,
        )
        links: list[tuple[str, str, str, int, int]] = []
        for match in pattern.finditer(content):
            links.append(
                (
                    self._clean_markdown_text(match.group("title")),
                    match.group("url"),
                    match.group("vacancy_id"),
                    match.start(),
                    match.end(),
                )
            )
        return links

    def _extract_card_details(self, segment: str) -> dict[str, str]:
        lines = self._content_lines(segment)
        company_name = ""
        employer_index = None
        for index, line in enumerate(lines):
            match = re.search(r"\[(?P<label>[^\[\]]+?)\]\((?P<url>https?://(?:www\.)?hh\.ru/employer/\d+)[^)]*\)", line)
            if match:
                company_name = self._clean_markdown_text(match.group("label"))
                employer_index = index
                break
        salary_text = self._first_matching(lines, self._is_salary_line)
        experience_text = self._first_matching(lines, lambda value: value.casefold().startswith("опыт "))
        work_model = "Remote" if any("можно удал" in line.casefold() or "можно удалён" in line.casefold() for line in lines) else ""
        location_text = self._extract_location(lines, start_index=(employer_index + 1) if employer_index is not None else 0)
        return {
            "company_name": company_name,
            "salary_text": salary_text,
            "experience_text": experience_text,
            "work_model": work_model,
            "location_text": location_text,
        }

    def _content_lines(self, content: str) -> list[str]:
        result: list[str] = []
        for raw_line in content.replace("\r\n", "\n").replace("\r", "\n").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^#+\s*", "", line).strip()
            line = re.sub(r"^[-*]\s*", "", line).strip()
            if not line or line.startswith("!["):
                continue
            result.append(line)
        return result

    def _extract_location(self, lines: list[str], *, start_index: int) -> str:
        location_parts: list[str] = []
        for line in lines[start_index:]:
            clean = self._clean_markdown_text(line)
            if not clean or HH_EMPLOYER_URL_RE.search(clean):
                continue
            if self._is_service_line(clean) or self._is_salary_line(clean) or clean.casefold().startswith("опыт "):
                continue
            if self._looks_like_description(clean):
                break
            location_parts.append(clean)
            if len(location_parts) >= 2:
                break
        return " · ".join(location_parts)

    def _first_matching(self, lines: list[str], predicate) -> str:
        for line in lines:
            clean = self._clean_markdown_text(line)
            if predicate(clean):
                return clean
        return ""

    def _is_salary_line(self, value: str) -> bool:
        normalized = value.casefold()
        return "₽" in value or "руб" in normalized or "на руки" in normalized

    def _is_service_line(self, value: str) -> bool:
        normalized = value.casefold()
        if normalized in {"•", "отклик без резюме"}:
            return True
        if re.fullmatch(r"\d+(?:[.,]\d+)?", normalized):
            return True
        return any(
            marker in normalized
            for marker in (
                "отзыв",
                "выплаты:",
                "сейчас смотр",
                "активн",
                "компании для вас",
            )
        )

    def _looks_like_description(self, value: str) -> bool:
        if len(value) > 80:
            return True
        return value.endswith("...") or value[:1].islower()

    def _join_location_and_work_model(self, location: str, work_model: str) -> str:
        if not work_model:
            return location
        if not location:
            return work_model
        if "remote" in location.casefold() or "удален" in location.casefold() or "удалён" in location.casefold():
            return location
        return f"{location} · {work_model}"

    def _raw_metadata_prefix(
        self,
        *,
        source_origin: str,
        salary_text: str,
        experience_text: str,
        work_model: str,
        published_at: str = "",
        updated_at: str = "",
    ) -> str:
        lines = [f"source_origin={source_origin}"]
        if salary_text:
            lines.append(f"hh_salary_text={salary_text}")
        if experience_text:
            lines.append(f"hh_experience_text={experience_text}")
        if work_model:
            lines.append(f"hh_work_model={work_model}")
        if published_at:
            lines.append(f"hh_published_at={published_at}")
        if updated_at:
            lines.append(f"hh_updated_at={updated_at}")
        return "\n".join(lines)

    def _parse_hh_ru_date(self, value: str) -> str:
        months = {
            "января": "01",
            "февраля": "02",
            "марта": "03",
            "апреля": "04",
            "мая": "05",
            "июня": "06",
            "июля": "07",
            "августа": "08",
            "сентября": "09",
            "октября": "10",
            "ноября": "11",
            "декабря": "12",
        }
        match = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", value.casefold())
        if not match:
            return ""
        day, month_name, year = match.groups()
        month = months.get(month_name)
        if not month:
            return ""
        return f"{year}-{month}-{int(day):02d}"

    def _clean_markdown_text(self, value: str) -> str:
        clean = value.replace("\u00a0", " ").replace("\u202f", " ").strip()
        clean = re.sub(r"\[(?P<label>[^\[\]]+?)\]\([^)]*\)", r"\g<label>", clean)
        clean = re.sub(r"\*\*(.*?)\*\*", r"\1", clean)
        return re.sub(r"\s+", " ", clean).strip()

    def _normalize_hh_url(self, value: str) -> str:
        parts = urlsplit(value.strip().rstrip(".,;)"))
        match = HH_VACANCY_URL_RE.search(value)
        path = f"/vacancy/{match.group('vacancy_id')}" if match else (parts.path.rstrip("/") or "/")
        return urlunsplit(("https", "hh.ru", path, "", ""))
