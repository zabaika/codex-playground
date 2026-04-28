from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .models import ChunkRecord, ParsedNote

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
HEADING_RE = re.compile(r"^(#{2,6})\s+(.*)$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def slugify_heading(value: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    slug = re.sub(r"\s+", "-", slug).strip("-")
    return slug or "section"


def detect_note_type(relative_path: Path) -> str:
    parts = relative_path.parts
    if len(parts) >= 2 and parts[0] == "Ideas" and parts[1] == "Concepts":
        return "concept"
    if parts and parts[0] == "Ideas":
        return "idea"
    if parts and parts[0] == "Job":
        return "job"
    if parts and parts[0] == "Daily notes":
        return "daily"
    return "other"


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    body = text[match.end():]
    data: dict[str, object] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line:
            i += 1
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            values = [] if not inner else [item.strip().strip("\"'") for item in inner.split(",")]
            data[key] = [v for v in values if v]
        elif rest == "":
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                nested = lines[j]
                stripped = nested.strip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip().strip("\"'"))
                    j += 1
                    continue
                break
            if items:
                data[key] = items
                i = j - 1
            else:
                data[key] = ""
        else:
            data[key] = rest.strip("\"'")
        i += 1
    return data, body


def extract_first_paragraph(body: str) -> tuple[str, str]:
    stripped = body.lstrip()
    if not stripped:
        return "", ""
    heading_match = HEADING_RE.search(stripped)
    if heading_match and heading_match.start() > 0:
        preface = stripped[:heading_match.start()].rstrip()
        remainder = stripped[heading_match.start():].lstrip("\n")
    else:
        parts = re.split(r"\n\s*\n", stripped, maxsplit=1)
        preface = parts[0].strip()
        remainder = parts[1] if len(parts) > 1 else ""
    first = preface.strip()
    if first.startswith("#"):
        return "", stripped
    return first, remainder.lstrip("\n")


def split_sections(body: str) -> list[tuple[str, str]]:
    body = body.strip()
    if not body:
        return []
    matches = list(HEADING_RE.finditer(body))
    if not matches:
        return [("", body)]
    sections: list[tuple[str, str]] = []
    preface = body[:matches[0].start()].strip()
    if preface:
        sections.append(("", preface))
    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section_body = body[start:end].strip()
        if section_body:
            sections.append((heading, section_body))
    return sections


def chunk_text(text: str, max_chars: int = 2500, overlap: int = 200) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        if end < len(normalized):
            split_at = normalized.rfind(" ", start, end)
            if split_at > start + max_chars // 2:
                end = split_at
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return chunks


def make_chunks(lead_summary: str, body: str) -> list[ChunkRecord]:
    records: list[ChunkRecord] = []
    if lead_summary:
        normalized = normalize_text(lead_summary)
        records.append(
            ChunkRecord(
                chunk_index=0,
                heading="Суть",
                chunk_role="entry",
                anchor="sut",
                text=lead_summary.strip(),
                text_normalized=normalized,
                char_count=len(lead_summary.strip()),
                token_estimate=estimate_tokens(normalized),
            )
        )
        return records

    fallback = body.strip()
    if fallback:
        normalized = normalize_text(fallback[:1200])
        records.append(
            ChunkRecord(
                chunk_index=0,
                heading="",
                chunk_role="body",
                anchor="chunk-0",
                text=fallback[:1200].strip(),
                text_normalized=normalized,
                char_count=len(fallback[:1200].strip()),
                token_estimate=estimate_tokens(normalized),
            )
        )
    return records


def parse_note(vault_root: Path, note_path: Path) -> ParsedNote:
    raw_text = note_path.read_text(encoding="utf-8")
    stat = note_path.stat()
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    frontmatter, body = parse_frontmatter(raw_text)
    relative_path = note_path.relative_to(vault_root)
    title = str(frontmatter.get("title") or note_path.stem)
    folder = str(relative_path.parent)
    tags = [str(tag) for tag in frontmatter.get("tags", []) or []]
    aliases = [str(alias) for alias in frontmatter.get("aliases", []) or []]
    lead_summary, remainder = extract_first_paragraph(body)
    headings = [match.group(2).strip() for match in HEADING_RE.finditer(body)]
    links_out = sorted({match.group(1).strip() for match in LINK_RE.finditer(body) if match.group(1).strip()})
    chunks = make_chunks(lead_summary, remainder)
    if not chunks:
        normalized = normalize_text(body)
        chunks = [
            ChunkRecord(
                chunk_index=0,
                heading="",
                chunk_role="body",
                anchor="chunk-0",
                text=body.strip(),
                text_normalized=normalized,
                char_count=len(body.strip()),
                token_estimate=estimate_tokens(normalized),
            )
        ]
    return ParsedNote(
        path=relative_path,
        title=title,
        folder=folder,
        note_type=detect_note_type(relative_path),
        tags=tags,
        aliases=aliases,
        headings=headings,
        links_out=links_out,
        lead_summary=lead_summary.strip(),
        chunks=chunks,
        mtime=int(stat.st_mtime),
        size_bytes=stat.st_size,
        content_hash=content_hash,
    )


def note_to_json(parsed: ParsedNote) -> str:
    return json.dumps(
        {
            "path": str(parsed.path),
            "title": parsed.title,
            "lead_summary": parsed.lead_summary,
            "chunks": [chunk.__dict__ for chunk in parsed.chunks],
        },
        ensure_ascii=False,
        indent=2,
    )
