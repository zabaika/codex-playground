"""Pure digest HTML, source-link repair and prepared status messages."""
import html
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_shared.formatting import build_message_link


MAIN_TOPICS_DAY_HEADING = "Главные темы дня"


MOST_POPULAR_HEADING = "Наиболее популярное"


OPEN_QUESTIONS_HEADING = "Незакрытые вопросы/продолжения"


QUESTION_ANSWER_LINKS_HEADING = "Связки вопрос-ответ/развитие темы"


POPULAR_LINK_LINE_RE = re.compile(
    r"^(?P<prefix>(?:\d+\.\s+|[-•]\s+)?)"
    r"(?P<link><?https?://\S+>?)"
    r"(?P<sep>\s*-\s+)"
    r"(?P<title>\S.*)$",
    re.IGNORECASE,
)


def is_telegram_message_link(value: str) -> bool:
    normalized = value.strip().strip("<>")
    return bool(re.match(r"^https?://t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+$", normalized, flags=re.IGNORECASE))


def normalize_similarity_tokens(value: str) -> list[str]:
    lowered = value.lower().replace("ё", "е")
    cleaned = re.sub(r"[^0-9a-zа-я]+", " ", lowered, flags=re.IGNORECASE)
    return [token for token in cleaned.split() if len(token) >= 2]


def build_popular_link_candidates(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for message in messages:
        link = build_message_link(message)
        message_text = " ".join(
            part.strip()
            for part in (
                str(message.get("text") or ""),
                str(message.get("ocr_text") or ""),
            )
            if part and part.strip()
        )
        candidates.append(
            {
                "message": message,
                "link": link,
                "message_id": int(message.get("message_id") or 0),
                "text_lower": message_text.lower(),
                "token_set": set(normalize_similarity_tokens(message_text)),
            }
        )
    return candidates


def score_popular_link_candidate(
    title_tokens: list[str],
    candidate: dict[str, Any],
    *,
    claimed_message_id: int | None,
) -> float:
    if not title_tokens:
        return 0.0
    message_tokens = candidate["token_set"]
    if not message_tokens:
        return 0.0
    overlap = set(title_tokens) & message_tokens
    if not overlap:
        return 0.0
    score = len(overlap) / len(set(title_tokens))
    message_id = candidate["message_id"]
    if claimed_message_id is not None and message_id:
        if message_id == claimed_message_id:
            score += 0.5
        elif message_id % 1000 == claimed_message_id % 1000:
            score += 0.25
        elif abs(message_id - claimed_message_id) <= 10:
            score += 0.1
    if len(overlap) >= 3:
        score += 0.1
    return score


def repair_popular_links_in_summary(summary: str, messages: list[dict[str, Any]]) -> str:
    if not summary.strip() or MOST_POPULAR_HEADING not in summary or not messages:
        return summary

    candidates = build_popular_link_candidates(messages)
    valid_links = {
        candidate["link"]: candidate["message"]
        for candidate in candidates
        if candidate["link"]
    }

    repaired_lines: list[str] = []
    in_popular_section = False
    for raw_line in summary.splitlines():
        stripped = raw_line.strip()
        if re.match(r"^Наиболее популярное\b", stripped, flags=re.IGNORECASE):
            in_popular_section = True
            repaired_lines.append(raw_line)
            continue
        if in_popular_section and re.match(r"^(?:Незакрытые вопросы|Связки вопрос-ответ|Главные темы)", stripped, flags=re.IGNORECASE):
            in_popular_section = False
        if not in_popular_section:
            repaired_lines.append(raw_line)
            continue

        match = POPULAR_LINK_LINE_RE.match(stripped)
        if not match:
            repaired_lines.append(raw_line)
            continue

        raw_link = match.group("link").strip()
        normalized_link = raw_link.strip("<>")
        if normalized_link in valid_links:
            repaired_lines.append(raw_line)
            continue

        best_message: dict[str, Any] | None = None
        if not is_telegram_message_link(normalized_link):
            exact_url_matches = [candidate["message"] for candidate in candidates if normalized_link.lower() in candidate["text_lower"]]
            if exact_url_matches:
                best_message = exact_url_matches[0]

        if best_message is None:
            title = match.group("title").strip()
            title_tokens = normalize_similarity_tokens(title)
            claimed_message_id: int | None = None
            claimed_match = re.search(r"/(\d+)$", normalized_link)
            if claimed_match:
                try:
                    claimed_message_id = int(claimed_match.group(1))
                except ValueError:
                    claimed_message_id = None

            best_score = 0.0
            for candidate in candidates:
                if not candidate["link"]:
                    continue
                score = score_popular_link_candidate(title_tokens, candidate, claimed_message_id=claimed_message_id)
                if score > best_score:
                    best_score = score
                    best_message = candidate["message"]
            if best_score < 0.34:
                best_message = None

        repaired_link = build_message_link(best_message) if best_message is not None else None
        if not repaired_link:
            repaired_lines.append(raw_line)
            continue
        prefix = match.group("prefix") or ""
        repaired_lines.append(f"{prefix}{repaired_link}{match.group('sep')}{match.group('title').strip()}")
    return "\n".join(repaired_lines)


def build_digest_message(
    channel_summaries: list[tuple[str, int, str]],
    *,
    since: str,
    until: str,
) -> str:
    header = f"Утренний дайджест\nПериод UTC: {since} .. {until}"
    if not channel_summaries:
        return f"{header}\n\nНовых сообщений для анализа нет."
    sections = [header]
    for channel_name, count, summary in channel_summaries:
        sections.append(f"{channel_name} ({count} сообщений)\n{summary}")
    return "\n\n".join(sections)


def format_digest_summary_for_telegram(summary: str) -> str:
    # The delivery uses HTML; normalize common model-generated Markdown first.
    summary = re.sub(r"\[[^\]\n]*\]\((https?://[^\s)]+)\)", r"\1", summary)
    summary = re.sub(r"(?m)^\s*#{1,6}\s+", "", summary)
    summary = re.sub(r"(\*\*|__)([^\n]+?)\1", r"\2", summary)
    lines = [line.strip() for line in summary.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    compact_lines = [line for line in lines if line]
    if not compact_lines:
        return ""
    formatted: list[str] = []
    current_section = ""
    previous_line_kind = ""

    def escape_line(value: str) -> str:
        escaped = html.escape(value, quote=False)
        escaped = escaped.replace("&lt;https://", "https://").replace("&lt;http://", "http://")
        escaped = escaped.replace("&gt;", "")
        return escaped

    def format_main_topics_line(value: str) -> str:
        match = re.match(r"^((?:[-•]\s+|\d+\.\s+)?)([^:]{2,200}):(?!//)(.*)$", value)
        if not match:
            return escape_line(value)
        marker, lead, tail = match.groups()
        if re.search(r"https?://", lead):
            return escape_line(value)
        return f"{escape_line(marker)}<b>{escape_line(lead)}:</b>\n{escape_line(tail).lstrip()}"

    def normalize_lead_line(value: str) -> str:
        normalized = re.sub(r"^(?:Главная тема дня|Главные темы дня|Главные темы)\s*[:\-—]\s*", "", value, flags=re.IGNORECASE).strip()
        if normalized != value.strip():
            return normalized or value.strip()
        return value.strip()

    def extract_lead_heading(value: str) -> str:
        if re.match(r"^(?:Главная тема дня|Главные темы дня|Главные темы)\s*[:\-—]\s*", value, flags=re.IGNORECASE):
            return MAIN_TOPICS_DAY_HEADING
        return MAIN_TOPICS_DAY_HEADING

    def is_popular_link_line(value: str) -> bool:
        return bool(POPULAR_LINK_LINE_RE.match(value))

    def resolve_heading(value: str) -> tuple[str, str] | None:
        value = re.sub(r"^(\*\*|__)(.+?)\1(?=\s*(?:$|[:\-—]))", r"\2", value)
        heading_patterns = (
            (MAIN_TOPICS_DAY_HEADING, r"^(?:Главная тема дня|Главные темы дня|Главные темы)\b"),
            (MOST_POPULAR_HEADING, r"^Наиболее популярное\b"),
            (OPEN_QUESTIONS_HEADING, r"^Незакрытые вопросы"),
            (QUESTION_ANSWER_LINKS_HEADING, r"^Связки вопрос-ответ"),
        )
        for canonical_heading, pattern in heading_patterns:
            match = re.match(pattern, value, flags=re.IGNORECASE)
            if not match:
                continue
            body_match = re.match(rf"^{pattern[1:]}\s*[:\-—]\s*(.*)$", value, flags=re.IGNORECASE)
            body = body_match.group(1).strip() if body_match else ""
            return canonical_heading, body
        return None

    first_line = compact_lines[0]
    if (
        len(compact_lines) > 1
        and not (first_line.startswith("- ") or first_line.startswith("• ") or first_line.startswith("<http"))
    ):
        current_section = "main_topics"
        formatted.extend(
            [
                f"<b>{escape_line(extract_lead_heading(first_line))}</b>",
                escape_line(normalize_lead_line(first_line)),
                "",
            ]
        )
        compact_lines = compact_lines[1:]
        previous_line_kind = "text"

    for line in compact_lines:
        heading = resolve_heading(line)
        is_heading = heading is not None
        is_list_item = line.startswith("- ") or line.startswith("• ") or is_popular_link_line(line)

        if is_heading:
            heading_line, body_line = heading
            if heading_line == MAIN_TOPICS_DAY_HEADING:
                current_section = "main_topics"
            elif heading_line == MOST_POPULAR_HEADING:
                current_section = "popular"
            else:
                current_section = "regular"
            if formatted and formatted[-1] != "":
                formatted.append("")
            formatted.append(f"<b>{escape_line(heading_line)}</b>")
            if body_line:
                if current_section == "main_topics":
                    formatted.append(format_main_topics_line(body_line))
                else:
                    formatted.append(escape_line(body_line))
                previous_line_kind = "text"
            else:
                previous_line_kind = "heading"
            continue

        if (
            current_section == "main_topics"
            and previous_line_kind in {"text", "list"}
            and re.fullmatch(r"<?https?://[^\s<>]+>?", line)
            and formatted
            and formatted[-1]
        ):
            formatted[-1] += " " + escape_line(line)
            continue

        if is_list_item:
            is_dense_section = current_section == "popular"
            if (
                formatted
                and not is_dense_section
                and formatted[-1] != ""
                and previous_line_kind in {"text", "list"}
            ):
                formatted.append("")
            if current_section == "main_topics":
                formatted.append(format_main_topics_line(line))
            else:
                formatted.append(escape_line(line))
            previous_line_kind = "list"
            continue

        if formatted and formatted[-1] != "":
            formatted.append("")
        if current_section == "main_topics":
            formatted.append(format_main_topics_line(line))
        else:
            formatted.append(escape_line(line))
        previous_line_kind = "text"

    while formatted and formatted[-1] == "":
        formatted.pop()
    return "\n".join(formatted)


def build_channel_digest_message(
    channel_name: str,
    *,
    since: str,
    until: str,
    message_count: int,
    summary: str,
    char_limit_reached: bool = False,
    output_token_limit_reached: bool = False,
    output_token_limit: int | None = None,
    sync_limit_reached: bool = False,
    separator_text: str = "",
) -> str:
    formatted_summary = format_digest_summary_for_telegram(summary)
    header = "\n\n".join(
        [
            f"<b>{html.escape(channel_name, quote=False)}</b>",
            "\n".join(
                [
                    f"Период UTC: {html.escape(since, quote=False)} .. {html.escape(until, quote=False)}",
                    f"Сообщений в анализе: {message_count}",
                ]
            ),
        ]
    )
    if not formatted_summary:
        body = header
    else:
        body = f"{header}\n\n{formatted_summary}"
    warnings: list[str] = []
    if char_limit_reached:
        warnings.append(
            "<i>Предупреждение: по этому чату был достигнут лимит "
            "message_block_max_chars, поэтому часть локального контекста была разбита на дополнительные AI-батчи.</i>"
        )
    if sync_limit_reached:
        warnings.append(
            "<i>Предупреждение: по этому чату был достигнут sync_limit; "
            "загрузка периода ограничена, результат может быть неполным.</i>"
        )
    if output_token_limit_reached and output_token_limit is not None:
        warnings.append(f"ответ ИИ достиг лимита выходных токенов ({output_token_limit})")
    if warnings:
        body = f"{body}\n\n" + "\n\n".join(warnings)
    if separator_text:
        escaped_separator = html.escape(separator_text, quote=False)
        return f"{body}\n\n{escaped_separator}\n{escaped_separator}"
    return body


def build_channel_digest_skip_message(
    channel_name: str,
    *,
    since: str,
    until: str,
    message_count: int,
    min_messages_for_ai: int,
    sync_limit_reached: bool = False,
    separator_text: str = "",
) -> str:
    return build_channel_digest_message(
        channel_name,
        since=since,
        until=until,
        message_count=message_count,
        summary=(
            f"Сообщений меньше порога для AI-обработки ({min_messages_for_ai}). "
            "Сообщения загружены, но digest отправлен без анализа."
        ),
        sync_limit_reached=sync_limit_reached,
        separator_text=separator_text,
    )


def build_digest_error_message(*, since: str, until: str, errors: list[str], separator_text: str = "") -> str:
    header = f"Digest completed with errors\nПериод UTC: {since} .. {until}"
    body = "\n\n".join([header, *errors])
    if separator_text:
        escaped_separator = html.escape(separator_text, quote=False)
        return f"{body}\n\n{escaped_separator}\n{escaped_separator}"
    return body


def format_digest_channel_reference(channel: str, channel_name: str) -> str:
    normalized_channel = channel.strip()
    normalized_channel_name = channel_name.strip()
    if normalized_channel_name and normalized_channel_name != normalized_channel:
        return f"{normalized_channel_name} ({normalized_channel})"
    return normalized_channel_name or normalized_channel


def format_digest_delivery_error(channel_name: str, exc: BaseException) -> str:
    return f"{channel_name}: delivery failed: {str(exc) or exc.__class__.__name__}"
