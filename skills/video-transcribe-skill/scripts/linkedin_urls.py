"""Shared validation and identity for single LinkedIn recordings."""
import hashlib
import re
from urllib.parse import urlparse


def linkedin_video_id(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"https", "http"} or parsed.netloc.lower() not in {
        "linkedin.com", "www.linkedin.com",
    }:
        return ""
    event = re.fullmatch(r"/events/([\w-]+)(?:/comments)?/?", parsed.path)
    if event:
        match = re.search(r"(\d{19})$", event[1])
        return match[1] if match else event[1]
    if re.fullmatch(r"/learning/[^/?#]+/[^/?#]+/?", parsed.path):
        # Course landing pages are playlists, not single recordings.
        return "learning-" + hashlib.sha256(parsed.path.rstrip("/").encode()).hexdigest()[:16]
    return ""
