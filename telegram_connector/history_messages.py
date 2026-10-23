"""Pure Telegram message conversion and source rendering."""
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def entity_display_name(entity: Any) -> str | None:
    if entity is None:
        return None
    first = getattr(entity, "first_name", None) or ""
    last = getattr(entity, "last_name", None) or ""
    full_name = f"{first} {last}".strip()
    if full_name:
        return full_name
    return optional_text(getattr(entity, "title", None)) or optional_text(getattr(entity, "username", None))


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return str(value)


def safe_to_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def is_image_media_kind(media_kind: str | None) -> bool:
    if not media_kind:
        return False
    lowered = media_kind.lower()
    return lowered == "photo" or lowered.startswith("image/")


def message_text(message: Any) -> str:
    return getattr(message, "message", None) or getattr(message, "text", None) or ""


def parse_message_url(url: str) -> tuple[str, int]:
    raw = url.strip()
    if not raw:
        raise SystemExit("Telegram message URL is required.")
    normalized = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(normalized)
    if parsed.netloc not in {"t.me", "www.t.me"}:
        raise SystemExit(f"Unsupported Telegram URL: {url}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[1].isdigit():
        return f"@{parts[0]}", int(parts[1])
    if len(parts) == 3 and parts[0] == "s" and parts[2].isdigit():
        return f"@{parts[1]}", int(parts[2])
    if len(parts) == 3 and parts[0] == "c" and parts[1].isdigit() and parts[2].isdigit():
        return f"-100{parts[1]}", int(parts[2])
    raise SystemExit(f"Unsupported Telegram message URL format: {url}")


def message_media_kind(message: Any) -> str | None:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "document", None):
        mime_type = getattr(getattr(message, "document", None), "mime_type", None)
        if mime_type:
            return mime_type
        return "document"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "sticker", None):
        return "sticker"
    return None


def extract_message_links(message: Any) -> list[dict[str, str]]:
    text = message_text(message)
    entities = getattr(message, "entities", None) or []
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entity in entities:
        url = optional_text(getattr(entity, "url", None))
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        label = None
        if length > 0:
            text_range = utf16_entity_range_to_python_range(text, offset, length)
            if text_range is None:
                continue
            start, end = text_range
            raw_label = text[start:end]
            label = optional_text(raw_label.splitlines()[0])
        if url is None and label and label.startswith(("http://", "https://")):
            url = label
        if url is None:
            continue
        effective_label = label or url
        item = (effective_label, url)
        if item in seen:
            continue
        seen.add(item)
        links.append({"label": effective_label, "url": url})
    return links


def escape_markdown_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def build_utf16_offset_map(text: str) -> dict[int, int]:
    offsets = {0: 0}
    utf16_units = 0
    for index, char in enumerate(text):
        utf16_units += 2 if ord(char) > 0xFFFF else 1
        offsets[utf16_units] = index + 1
    return offsets


def utf16_entity_range_to_python_range(text: str, offset: int, length: int) -> tuple[int, int] | None:
    if offset < 0 or length <= 0:
        return None
    utf16_offsets = build_utf16_offset_map(text)
    end_offset = offset + length
    if offset not in utf16_offsets or end_offset not in utf16_offsets:
        return None
    return utf16_offsets[offset], utf16_offsets[end_offset]


def render_message_body_with_inline_links(message: Any) -> tuple[str, list[dict[str, str]]]:
    text = message_text(message)
    entities = getattr(message, "entities", None) or []
    link_entities: list[tuple[int, int, str, str]] = []
    fallback_links: list[dict[str, str]] = []
    seen_fallbacks: set[tuple[str, str]] = set()

    for entity in entities:
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        text_range = utf16_entity_range_to_python_range(text, offset, length)
        if text_range is None:
            continue
        start, end = text_range
        raw_label = text[start:end]
        url = optional_text(getattr(entity, "url", None))
        label = optional_text(raw_label)
        if url is None and label and label.startswith(("http://", "https://")):
            url = label
        if url is None:
            continue
        if "\n" in raw_label:
            item = (raw_label.splitlines()[0].strip() or url, url)
            if item not in seen_fallbacks:
                seen_fallbacks.add(item)
                fallback_links.append({"label": item[0], "url": item[1]})
            continue
        link_entities.append((start, end, raw_label, url))

    if not link_entities:
        return text, fallback_links

    rendered_parts: list[str] = []
    cursor = 0
    for start, end, raw_label, url in sorted(link_entities, key=lambda item: (item[0], item[1])):
        if start < cursor:
            item = (raw_label.strip() or url, url)
            if item not in seen_fallbacks:
                seen_fallbacks.add(item)
                fallback_links.append({"label": item[0], "url": item[1]})
            continue
        rendered_parts.append(text[cursor:start])
        label = escape_markdown_link_label(raw_label)
        rendered_parts.append(f"[{label}]({url})")
        cursor = end
    rendered_parts.append(text[cursor:])
    return "".join(rendered_parts), fallback_links


def message_subject_line(message: Any) -> str:
    for line in message_text(message).splitlines():
        candidate = optional_text(line)
        if candidate:
            return candidate
    return f"message {getattr(message, 'id', 'unknown')}"


def compose_single_message_title(entity: Any, message: Any) -> str:
    channel_name = entity_display_name(entity) or optional_text(getattr(entity, "username", None)) or "Telegram"
    subject = message_subject_line(message)
    if subject.casefold().startswith(channel_name.casefold()):
        return subject
    return f"{channel_name} - {subject}"


def sanitize_note_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned or "telegram-message"


def render_single_message_source(entity: Any, message: Any, source_url: str) -> str:
    channel_name = entity_display_name(entity) or "Unknown channel"
    username = optional_text(getattr(entity, "username", None))
    channel_ref = f"@{username}" if username else str(getattr(entity, "id", "unknown"))
    date_utc = serialize_datetime(getattr(message, "date", None)) or "unknown"
    body, fallback_links = render_message_body_with_inline_links(message)
    body = body.strip() or "(message has no text body)"
    lines = [
        f"# {compose_single_message_title(entity, message)}",
        "",
        f"Source URL: {source_url}",
        f"Source type: telegram_message",
        f"Channel: {channel_name} ({channel_ref})",
        f"Message ID: {getattr(message, 'id', 'unknown')}",
        f"Date UTC: {date_utc}",
        "",
        "## Message Text",
        "",
        body,
    ]
    if fallback_links:
        lines.extend(
            [
                "",
                "## Unresolved Links",
                "",
            ]
        )
        for link in fallback_links:
            lines.append(f"- [{link['label']}]({link['url']})")
    return "\n".join(lines) + "\n"


def minimal_entity_json(entity: Any) -> str:
    payload = {
        "id": getattr(entity, "id", None),
        "username": getattr(entity, "username", None),
        "title": getattr(entity, "title", None) or getattr(entity, "first_name", None),
        "type": getattr(entity, "__class__", type(entity)).__name__,
    }
    return safe_to_json(payload)


def minimal_message_json(message: Any, media_kind: str | None, text: str) -> str:
    payload = {
        "id": getattr(message, "id", None),
        "date_utc": serialize_datetime(getattr(message, "date", None)),
        "edit_date_utc": serialize_datetime(getattr(message, "edit_date", None)),
        "text_length": len(text),
        "has_media": bool(getattr(message, "media", None)),
        "media_kind": media_kind,
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies": getattr(getattr(message, "replies", None), "replies", None),
    }
    reply_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
    if type(reply_id) is int and reply_id > 0:
        payload["reply_to"] = {"reply_to_msg_id": reply_id}
        peer = getattr(message.reply_to, "reply_to_peer_id", None)
        if peer is not None:
            identity = {key: getattr(peer, key) for key in ("channel_id", "chat_id", "user_id")
                        if type(getattr(peer, key, None)) is int}
            payload["reply_to"]["reply_to_peer_id"] = identity or {"type": type(peer).__name__}
    return safe_to_json(payload)


def direct_sender_metadata(message: Any) -> tuple[str | None, str | None]:
    sender = getattr(message, "sender", None)
    username = optional_text(getattr(message, "sender_username", None))
    display_name = optional_text(getattr(message, "sender_display_name", None))
    if sender is not None:
        username = username or optional_text(getattr(sender, "username", None))
        display_name = display_name or entity_display_name(sender)
    return username, display_name
