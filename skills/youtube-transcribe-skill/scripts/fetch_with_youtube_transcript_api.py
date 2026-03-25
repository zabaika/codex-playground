#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import socket
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import YouTubeTranscriptApiException
from youtube_transcript_api.formatters import SRTFormatter


DEFAULT_PRIORITY = ["orig", "ru", "en", "uk"]
TRANSIENT_PREFIX = "TRANSIENT_ERROR:"
NO_SUBTITLES_PREFIX = "NO_SUBTITLES:"
GENERIC_PREFIX = "ERROR:"


def extract_video_id(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise SystemExit("Missing video URL or ID.")
    if re.fullmatch(r"[\w-]{11}", raw):
        return raw
    parsed = urlparse(raw)
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[\w-]{11}", candidate):
            return candidate
    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if re.fullmatch(r"[\w-]{11}", query_id):
        return query_id
    raise SystemExit("Could not extract a valid YouTube video ID from the provided URL.")


def choose_transcript(transcript_list, priority: list[str]):
    all_transcripts = list(transcript_list)
    for wanted in priority:
        if wanted.lower() == "orig":
            for transcript in all_transcripts:
                if "orig" in transcript.language_code.lower():
                    return transcript
            continue
        try:
            return transcript_list.find_transcript([wanted])
        except YouTubeTranscriptApiException:
            continue
    available = ", ".join(sorted({t.language_code for t in all_transcripts})) or "none"
    raise SystemExit(f"None of the preferred subtitle languages are available. Available languages: {available}")


def resolve_output_dir(raw_value: str | None) -> Path:
    if not raw_value or not raw_value.strip():
        return Path.cwd()
    target = Path(raw_value).expanduser()
    if target.is_absolute():
        return target
    return (Path.cwd() / target).resolve()


def sanitize_filename_component(raw_value: str, video_id: str) -> str:
    value = re.sub(r'[\x00-\x1f]+', " ", raw_value).strip()
    value = re.sub(r'[\\/:*?"<>|]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value[:180].strip() or f"YouTube {video_id}"


def classify_exception(exc: Exception) -> tuple[str, str]:
    text = f"{exc.__class__.__name__}: {exc}".strip()
    lowered = text.lower()
    if isinstance(exc, (socket.gaierror, requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return TRANSIENT_PREFIX, "Temporary network or DNS error while contacting YouTube."
    if "failed to resolve" in lowered or "nameresolutionerror" in lowered or "max retries exceeded" in lowered:
        return TRANSIENT_PREFIX, "Temporary network or DNS error while contacting YouTube."
    if "timed out" in lowered or "temporarily unavailable" in lowered:
        return TRANSIENT_PREFIX, "Temporary network timeout while contacting YouTube."
    if "transcriptsdisabled" in lowered or "subtitles are disabled for this video" in lowered:
        return NO_SUBTITLES_PREFIX, "No subtitles are available for this video."
    if "notranscriptfound" in lowered or "no transcripts were found" in lowered:
        return NO_SUBTITLES_PREFIX, "No subtitles are available for this video."
    if isinstance(exc, YouTubeTranscriptApiException):
        return GENERIC_PREFIX, text
    return GENERIC_PREFIX, text


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch YouTube subtitles with youtube-transcript-api.")
    parser.add_argument("--url", required=True, help="YouTube video URL or ID")
    parser.add_argument(
        "--languages",
        nargs="*",
        default=DEFAULT_PRIORITY,
        help='Priority order, e.g. "orig ru en uk"',
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for the generated subtitle file. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional pre-fetched video title used for a more readable output filename.",
    )
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        transcript_list = YouTubeTranscriptApi().list(video_id)
        transcript = choose_transcript(transcript_list, list(args.languages))
        fetched = transcript.fetch()
        safe_title = sanitize_filename_component(args.title, video_id)
        output_path = output_dir / f"{safe_title} [{video_id}].{transcript.language_code}.srt"
        output_path.write_text(SRTFormatter().format_transcript(fetched), encoding="utf-8")
        print(f"Selected subtitle language: {transcript.language_code}", file=sys.stderr)
        print(f"Saved subtitle file: {output_path.resolve()}", file=sys.stderr)
        return 0
    except Exception as exc:
        prefix, message = classify_exception(exc)
        print(f"{prefix} {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
