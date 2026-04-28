from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ParsedNote:
    path: Path
    title: str
    folder: str
    note_type: str
    tags: list[str]
    aliases: list[str]
    headings: list[str]
    links_out: list[str]
    lead_summary: str
    chunks: list["ChunkRecord"]
    mtime: int
    size_bytes: int
    content_hash: str


@dataclass(slots=True)
class ChunkRecord:
    chunk_index: int
    heading: str
    chunk_role: str
    anchor: str
    text: str
    text_normalized: str
    char_count: int
    token_estimate: int
