#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import tomllib
from urllib import error, parse, request


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT") else APP_DIR
BASE_DIR = PROJECT_ROOT
CONFIG_DIR = BASE_DIR / "config"
RUNTIME_LOCAL_FILE = CONFIG_DIR / "runtime.local.toml"
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "agent_sessions.local.json"
DB_FILE = DATA_DIR / "telegram_agent.sqlite3"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_AGENT_MODEL = "gpt-5.4-mini"
DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_WEB_SEARCH_LIMIT = 5
DEFAULT_FETCH_CHAR_LIMIT = 12000
MAX_LOCAL_MATCHES = 50
MAX_FILE_LINES = 400
MAX_DIRECTORY_ENTRIES = 200
OP_REFERENCE_PREFIX = "op://"
_SECRET_CACHE: dict[str, str] = {}

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS ai_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    feature TEXT NOT NULL,
    stage TEXT NOT NULL,
    channel TEXT,
    since TEXT,
    until TEXT,
    model TEXT NOT NULL,
    response_id TEXT,
    prompt_cache_key TEXT,
    prompt_cache_retention TEXT,
    request_index INTEGER,
    message_count INTEGER,
    system_chars INTEGER,
    prompt_chars INTEGER,
    shared_prefix_chars INTEGER,
    shared_prefix_hash TEXT,
    prompt_hash TEXT,
    previous_prompt_hash TEXT,
    previous_response_id TEXT,
    prefix_match_chars_with_previous INTEGER,
    prompt_text TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_created_at ON ai_usage_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_usage_log_cache_key ON ai_usage_log(prompt_cache_key, id DESC);
"""


@dataclass
class OpenAIUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int


@dataclass
class PromptCacheInfo:
    cache_key: str
    cache_retention: str
    system_chars: int
    prompt_chars: int
    shared_prefix_chars: int
    shared_prefix_hash: str
    prompt_hash: str


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignore_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
        elif tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
        elif tag in {"p", "div", "section", "article", "li"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._chunks))
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", raw)).strip()


def load_runtime_config() -> dict[str, Any]:
    if not RUNTIME_LOCAL_FILE.exists():
        return {}
    with RUNTIME_LOCAL_FILE.open("rb") as fh:
        data = tomllib.load(fh)
    return data if isinstance(data, dict) else {}


def get_config_value(config: dict[str, Any], section: str, key: str) -> str:
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return ""
    value = section_data.get(key, "")
    return str(value).strip()


def resolve_onepassword_secret(reference: str, label: str) -> str:
    cached = _SECRET_CACHE.get(reference)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            f"1Password CLI 'op' is required to resolve {label}. Install 1Password CLI and sign in first."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"Timed out while resolving {label} from 1Password.") from exc
    if completed.returncode != 0:
        raise SystemExit(
            f"Failed to resolve {label} from 1Password. Make sure 'op' is signed in and the secret reference is valid."
        )
    value = completed.stdout.strip()
    _SECRET_CACHE[reference] = value
    return value


def resolve_secret_value(raw_value: str, label: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith(OP_REFERENCE_PREFIX):
        return resolve_onepassword_secret(value, label)
    return value


def parse_int(value: str, default: int, *, min_value: int = 1, max_value: int | None = None) -> int:
    try:
        parsed = int(str(value).strip()) if str(value).strip() else default
    except ValueError:
        parsed = default
    parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def parse_allowed_roots(config: dict[str, Any]) -> list[Path]:
    section = config.get("agent", {})
    raw_value = section.get("allowed_roots", []) if isinstance(section, dict) else []
    values: list[str] = []
    if isinstance(raw_value, list):
        values = [str(item).strip() for item in raw_value if str(item).strip()]
    elif isinstance(raw_value, str):
        values = [item.strip() for item in raw_value.replace(";", "\n").splitlines() if item.strip()]
    roots = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        else:
            path = path.resolve()
        roots.append(path)
    if not roots:
        roots.append(BASE_DIR.resolve())
    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique_roots.append(root)
    return unique_roots


def resolve_runtime() -> dict[str, Any]:
    config = load_runtime_config()
    raw_api_key = os.environ.get("OPENAI_API_KEY", "").strip() or get_config_value(config, "secrets", "openai_api_key")
    api_key = resolve_secret_value(raw_api_key, "OpenAI API key")
    if not api_key:
        raise SystemExit(
            "Missing OpenAI API key. Put it into telegram_agent_bot/config/runtime.local.toml under [secrets].openai_api_key."
        )
    return {
        "model": get_config_value(config, "agent", "model") or DEFAULT_AGENT_MODEL,
        "openai_api_key": api_key,
        "system_instructions": get_config_value(config, "agent_prompts", "system_instructions")
        or (
            "Ты Telegram-агент для локальной рабочей машины. "
            "Отвечай строго на русском языке, используя английский только для терминов, API-названий и имён собственных. "
            "Можно использовать только настроенные локальные папки и публичные веб-страницы. "
            "Если нужны дополнительные доступы к папкам, токены, логины или другие секреты, сначала попроси их в чате и предложи сохранить в 1Password; "
            "не придумывай несуществующие доступы и не обходи ограничения. "
            "Если нужно создать файлы, сначала спроси, куда их положить, и предложи 1-3 разумных пути. "
            "Если не хватает функционала, сначала предложи поискать и установить готовый skill или готовую интеграцию, а не создавать всё с нуля. "
            "Опирайся на результаты инструментов, а если данных недостаточно, говори об этом прямо. "
            "Финальные ответы делай короткими, практичными и пригодными для Telegram."
        ),
        "max_tool_rounds": parse_int(get_config_value(config, "agent", "max_tool_rounds"), DEFAULT_MAX_TOOL_ROUNDS, min_value=1, max_value=16),
        "web_search_limit": parse_int(get_config_value(config, "agent", "web_search_limit"), DEFAULT_WEB_SEARCH_LIMIT, min_value=1, max_value=10),
        "fetch_char_limit": parse_int(get_config_value(config, "agent", "fetch_char_limit"), DEFAULT_FETCH_CHAR_LIMIT, min_value=1000, max_value=30000),
        "prompt_cache_scope": (get_config_value(config, "agent", "prompt_cache_scope") or "global").strip().lower(),
        "allowed_roots": parse_allowed_roots(config),
    }


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def now_utc() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def connect_db() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    ensure_data_dir()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_chat_state(chat_id: str) -> None:
    state = load_state()
    if chat_id in state:
        del state[chat_id]
        save_state(state)


def get_previous_response_id(chat_id: str) -> str:
    state = load_state()
    entry = state.get(chat_id, {})
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("response_id", "")).strip()


def set_previous_response_id(chat_id: str, response_id: str, username: str) -> None:
    state = load_state()
    state[chat_id] = {
        "response_id": response_id,
        "username": username,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)


def path_within_allowed_roots(path: Path, allowed_roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in allowed_roots:
        resolved_root = root.resolve()
        try:
            resolved.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def resolve_user_path(raw_path: str, allowed_roots: list[Path]) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (BASE_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not path_within_allowed_roots(candidate, allowed_roots):
        raise ValueError("Requested path is outside configured agent.allowed_roots.")
    return candidate


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n...[truncated]"


def list_local_files(path: str, allowed_roots: list[Path], limit: int = 50) -> dict[str, Any]:
    target = resolve_user_path(path, allowed_roots)
    if not target.exists():
        raise ValueError(f"Path does not exist: {target}")
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {target}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[: min(MAX_DIRECTORY_ENTRIES, max(1, limit))]:
        entries.append({"name": child.name, "path": str(child), "kind": "dir" if child.is_dir() else "file"})
    return {"path": str(target), "entries": entries}


def read_local_file(path: str, allowed_roots: list[Path], start_line: int = 1, max_lines: int = 120) -> dict[str, Any]:
    target = resolve_user_path(path, allowed_roots)
    if not target.exists():
        raise ValueError(f"File does not exist: {target}")
    if not target.is_file():
        raise ValueError(f"Path is not a file: {target}")
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start_index = max(0, start_line - 1)
    end_index = min(len(lines), start_index + min(MAX_FILE_LINES, max(1, max_lines)))
    selected = [f"{index + 1}: {lines[index]}" for index in range(start_index, end_index)]
    return {
        "path": str(target),
        "start_line": start_index + 1,
        "end_line": end_index,
        "content": "\n".join(selected),
    }


def search_local_files(query: str, allowed_roots: list[Path], root: str = ".", limit: int = 20) -> dict[str, Any]:
    target_root = resolve_user_path(root, allowed_roots)
    result_limit = min(MAX_LOCAL_MATCHES, max(1, limit))
    try:
        completed = subprocess.run(
            [
                "rg",
                "-n",
                "--hidden",
                "--glob",
                "!.git",
                "--glob",
                "!data",
                "--max-count",
                str(result_limit),
                query,
                str(target_root),
            ],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        matches = []
        pattern = query.lower()
        for file_path in target_root.rglob("*"):
            if len(matches) >= result_limit:
                break
            if not file_path.is_file() or ".git" in file_path.parts or "data" in file_path.parts:
                continue
            try:
                for line_number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    if pattern in line.lower():
                        matches.append(f"{file_path}:{line_number}:{line.strip()}")
                        if len(matches) >= result_limit:
                            break
            except OSError:
                continue
        return {"query": query, "root": str(target_root), "matches": matches}
    matches = [line for line in completed.stdout.splitlines() if line.strip()]
    return {"query": query, "root": str(target_root), "matches": matches}


def extract_search_results(html: str, limit: int) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?(?:<a[^>]*class="result__snippet"[^>]*>|<div[^>]*class="result__snippet"[^>]*>)(?P<snippet>.*?)</(?:a|div)>',
        re.S,
    )
    results = []
    for match in pattern.finditer(html):
        href = re.sub(r"\s+", " ", unescape(re.sub(r"<.*?>", "", match.group("href")))).strip()
        title = re.sub(r"\s+", " ", unescape(re.sub(r"<.*?>", "", match.group("title")))).strip()
        snippet = re.sub(r"\s+", " ", unescape(re.sub(r"<.*?>", "", match.group("snippet")))).strip()
        parsed_href = parse.urlparse(href)
        if parsed_href.netloc.endswith("duckduckgo.com") and "uddg=" in parsed_href.query:
            href = parse.parse_qs(parsed_href.query).get("uddg", [href])[0]
        if href and title:
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def web_search(query: str, limit: int) -> dict[str, Any]:
    url = "https://html.duckduckgo.com/html/?" + parse.urlencode({"q": query})
    req = request.Request(url, headers={"User-Agent": "telegram-agent-bot/1.0"})
    try:
        with request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} while searching the web.") from exc
    except error.URLError as exc:
        raise ValueError("Network error while searching the web.") from exc
    return {"query": query, "results": extract_search_results(html, limit)}


def validate_public_http_url(url: str) -> parse.ParseResult:
    parsed_url = parse.urlparse(url)
    if parsed_url.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported.")
    hostname = (parsed_url.hostname or "").strip().strip(".")
    if not hostname:
        raise ValueError("URL host is required.")
    lowered_hostname = hostname.lower()
    if lowered_hostname == "localhost" or lowered_hostname.endswith(".local"):
        raise ValueError("Only public internet URLs are supported.")
    resolved_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        resolved_ips.append(ipaddress.ip_address(lowered_hostname))
    except ValueError:
        try:
            addr_infos = socket.getaddrinfo(hostname, parsed_url.port or 80, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ValueError("Could not resolve URL host.") from exc
        for family, _, _, _, sockaddr in addr_infos:
            if family == socket.AF_INET:
                resolved_ips.append(ipaddress.ip_address(sockaddr[0]))
            elif family == socket.AF_INET6:
                resolved_ips.append(ipaddress.ip_address(sockaddr[0]))
    if not resolved_ips:
        raise ValueError("Could not resolve URL host.")
    if any(not ip.is_global for ip in resolved_ips):
        raise ValueError("Only public internet URLs are supported.")
    return parsed_url


def fetch_url_text(url: str, *, max_chars: int) -> dict[str, Any]:
    parsed_url = validate_public_http_url(url)
    req = request.Request(url, headers={"User-Agent": "telegram-agent-bot/1.0"})
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} while fetching URL.") from exc
    except error.URLError as exc:
        raise ValueError("Network error while fetching URL.") from exc
    if "html" in content_type or "<html" in body.lower():
        parser = HTMLTextExtractor()
        parser.feed(body)
        text = parser.text()
    else:
        text = body
    return {"url": url, "content_type": content_type, "text": truncate_text(text, max_chars)}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "list_local_files",
            "description": "List files and folders under an allowed local directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_DIRECTORY_ENTRIES},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "read_local_file",
            "description": "Read a local text file from an allowed root with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": MAX_FILE_LINES},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search_local_files",
            "description": "Search local files by content inside an allowed root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "root": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LOCAL_MATCHES},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "web_search",
            "description": "Search the public web and return result titles, snippets, and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "fetch_url",
            "description": "Fetch a public web page and return extracted plain text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 30000},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    ]


def api_request(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    req = request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=180) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"OpenAI API HTTP {exc.code} while running telegram agent worker.") from exc
    except error.URLError as exc:
        raise SystemExit("OpenAI API request failed while running telegram agent worker.") from exc
    response["_latency_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
    return response


def extract_usage(response: dict[str, Any]) -> OpenAIUsage:
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    return OpenAIUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        cached_input_tokens=int(input_details.get("cached_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        latency_ms=max(0, int(response.get("_latency_ms") or 0)),
    )


def common_prefix_length(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    idx = 0
    while idx < limit and left[idx] == right[idx]:
        idx += 1
    return idx


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_prompt_cache_key(*, model: str, scope: str, chat_id: str, allowed_roots: list[Path]) -> str:
    if scope not in {"chat", "global"}:
        scope = "global"
    scope_value = chat_id.strip() or "no-chat" if scope == "chat" else "global"
    roots_value = "|".join(str(root) for root in allowed_roots)
    raw = "|".join([model.strip().lower() or "unknown-model", scope, scope_value, roots_value])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"agent:{digest}"


def build_agent_prompt_prefix(username: str, allowed_roots: list[Path]) -> str:
    roots_block = "\n".join(f"- {root}" for root in allowed_roots)
    return (
        "Telegram task context:\n"
        f"Telegram user: {username or 'unknown'}\n"
        f"Allowed local roots:\n{roots_block}"
    )


def build_agent_prompt_text(prompt: str, username: str, allowed_roots: list[Path]) -> str:
    return f"{build_agent_prompt_prefix(username, allowed_roots)}\n\nUser task:\n{prompt.strip()}"


def build_round_log_text(
    *,
    round_index: int,
    prompt: str,
    username: str,
    allowed_roots: list[Path],
    current_input: list[dict[str, Any]],
) -> str:
    if round_index == 1:
        prompt_text = prompt.strip()
        roots_value = "|".join(str(root) for root in allowed_roots)
        return "\n".join(
            [
                "user_prompt",
                f"username_hash={short_hash((username or 'unknown').strip().lower())}",
                f"allowed_roots_count={len(allowed_roots)}",
                f"allowed_roots_hash={short_hash(roots_value)}",
                f"prompt_chars={len(prompt_text)}",
                f"prompt_hash={short_hash(prompt_text)}",
            ]
        )
    lines = [
        "function_call_output",
        f"items={len(current_input)}",
    ]
    for index, item in enumerate(current_input[:8], start=1):
        output = str(item.get("output", "")) if isinstance(item, dict) else ""
        item_type = str(item.get("type", "unknown")) if isinstance(item, dict) else "unknown"
        lines.append(f"item_{index}_type={item_type}")
        lines.append(f"item_{index}_output_chars={len(output)}")
        lines.append(f"item_{index}_output_hash={short_hash(output)}")
    if len(current_input) > 8:
        lines.append(f"truncated_items={len(current_input) - 8}")
    return "\n".join(lines)


def build_round_prompt_text(
    *,
    round_index: int,
    prompt: str,
    username: str,
    allowed_roots: list[Path],
    current_input: list[dict[str, Any]],
) -> tuple[str, str, int]:
    if round_index == 1:
        prompt_text = build_agent_prompt_text(prompt, username, allowed_roots)
        return prompt_text, build_agent_prompt_prefix(username, allowed_roots), 1
    prompt_text = json.dumps(current_input, ensure_ascii=False, sort_keys=True)
    return prompt_text, "function_call_output", len(current_input)


def build_prompt_cache_info(*, model: str, cache_key: str, system_instructions: str, prompt_text: str, shared_prefix: str) -> PromptCacheInfo:
    return PromptCacheInfo(
        cache_key=cache_key,
        cache_retention="in_memory",
        system_chars=len(system_instructions),
        prompt_chars=len(prompt_text),
        shared_prefix_chars=len(shared_prefix),
        shared_prefix_hash=hashlib.sha256(shared_prefix.encode("utf-8")).hexdigest()[:16],
        prompt_hash=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16],
    )


def log_openai_usage(
    conn: sqlite3.Connection,
    *,
    stage: str,
    channel: str,
    model: str,
    request_index: int,
    message_count: int,
    status: str,
    cache_info: PromptCacheInfo,
    prompt_text: str,
    usage: OpenAIUsage | None = None,
    response_id: str | None = None,
    error: str | None = None,
) -> None:
    previous = conn.execute(
        """
        SELECT response_id, prompt_hash, prompt_text
        FROM ai_usage_log
        WHERE feature = 'agent'
          AND prompt_cache_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (cache_info.cache_key,),
    ).fetchone()
    previous_response_id = previous["response_id"] if previous else None
    previous_prompt_hash = previous["prompt_hash"] if previous else None
    prefix_match_chars_with_previous = 0
    if previous and previous["prompt_text"]:
        prefix_match_chars_with_previous = common_prefix_length(previous["prompt_text"], prompt_text)
    conn.execute(
        """
        INSERT INTO ai_usage_log (
            created_at, feature, stage, channel, since, until, model, response_id,
            prompt_cache_key, prompt_cache_retention, request_index, message_count,
            system_chars, prompt_chars, shared_prefix_chars, shared_prefix_hash,
            prompt_hash, previous_prompt_hash, previous_response_id, prefix_match_chars_with_previous,
            prompt_text, input_tokens, cached_input_tokens, output_tokens, total_tokens,
            latency_ms, status, error
        )
        VALUES (?, 'agent', ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_utc(),
            stage,
            channel,
            model,
            response_id,
            cache_info.cache_key,
            cache_info.cache_retention,
            request_index,
            message_count,
            cache_info.system_chars,
            cache_info.prompt_chars,
            cache_info.shared_prefix_chars,
            cache_info.shared_prefix_hash,
            cache_info.prompt_hash,
            previous_prompt_hash,
            previous_response_id,
            prefix_match_chars_with_previous,
            prompt_text,
            usage.input_tokens if usage else None,
            usage.cached_input_tokens if usage else None,
            usage.output_tokens if usage else None,
            usage.total_tokens if usage else None,
            usage.latency_ms if usage else None,
            status,
            optional_text(error),
        ),
    )
    conn.commit()


def extract_output_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    texts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content_item in item.get("content", []):
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return "\n\n".join(texts).strip()


def extract_function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and item.get("name") and item.get("call_id"):
            calls.append(item)
    return calls


def execute_tool(call: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    name = str(call.get("name"))
    raw_arguments = str(call.get("arguments", "")).strip() or "{}"
    arguments = json.loads(raw_arguments)
    allowed_roots = runtime["allowed_roots"]
    if name == "list_local_files":
        return list_local_files(str(arguments["path"]), allowed_roots, limit=int(arguments.get("limit", 50)))
    if name == "read_local_file":
        return read_local_file(
            str(arguments["path"]),
            allowed_roots,
            start_line=int(arguments.get("start_line", 1)),
            max_lines=int(arguments.get("max_lines", 120)),
        )
    if name == "search_local_files":
        return search_local_files(
            str(arguments["query"]),
            allowed_roots,
            root=str(arguments.get("root", ".")),
            limit=int(arguments.get("limit", 20)),
        )
    if name == "web_search":
        return web_search(str(arguments["query"]), min(runtime["web_search_limit"], int(arguments.get("limit", runtime["web_search_limit"]))))
    if name == "fetch_url":
        return fetch_url_text(str(arguments["url"]), max_chars=min(runtime["fetch_char_limit"], int(arguments.get("max_chars", runtime["fetch_char_limit"]))))
    raise ValueError(f"Unsupported tool call: {name}")


def build_agent_input(prompt: str, username: str, allowed_roots: list[Path]) -> list[dict[str, Any]]:
    prompt_text = build_agent_prompt_text(prompt, username, allowed_roots)
    return [{"role": "user", "content": [{"type": "input_text", "text": prompt_text}]}]


def run_agent(prompt: str, chat_id: str, username: str) -> dict[str, Any]:
    runtime = resolve_runtime()
    log_conn = connect_db()
    init_db(log_conn)
    previous_response_id = get_previous_response_id(chat_id) if chat_id else ""
    current_input = build_agent_input(prompt, username, runtime["allowed_roots"])
    response_id_to_continue = previous_response_id or None
    tool_calls_executed = 0
    used_web = False
    used_local = False
    cache_key = build_prompt_cache_key(
        model=runtime["model"],
        scope=runtime["prompt_cache_scope"],
        chat_id=chat_id,
        allowed_roots=runtime["allowed_roots"],
    )
    try:
        for round_index in range(1, runtime["max_tool_rounds"] + 1):
            prompt_text, shared_prefix, message_count = build_round_prompt_text(
                round_index=round_index,
                prompt=prompt,
                username=username,
                allowed_roots=runtime["allowed_roots"],
                current_input=current_input,
            )
            prompt_log_text = build_round_log_text(
                round_index=round_index,
                prompt=prompt,
                username=username,
                allowed_roots=runtime["allowed_roots"],
                current_input=current_input,
            )
            cache_info = build_prompt_cache_info(
                model=runtime["model"],
                cache_key=cache_key,
                system_instructions=runtime["system_instructions"],
                prompt_text=prompt_text,
                shared_prefix=shared_prefix,
            )
            payload: dict[str, Any] = {
                "model": runtime["model"],
                "instructions": runtime["system_instructions"],
                "input": current_input,
                "tools": tool_definitions(),
                "tool_choice": "auto",
                "prompt_cache_key": cache_key,
            }
            if response_id_to_continue:
                payload["previous_response_id"] = response_id_to_continue
            try:
                response = api_request(payload, runtime["openai_api_key"])
            except SystemExit as exc:
                log_openai_usage(
                    log_conn,
                    stage=f"round_{round_index}",
                    channel=chat_id or username or "no-chat",
                    model=runtime["model"],
                    request_index=round_index,
                    message_count=message_count,
                    status="error",
                    cache_info=cache_info,
                    prompt_text=prompt_log_text,
                    error=str(exc),
                )
                raise
            usage = extract_usage(response)
            response_id_to_continue = str(response.get("id", "")).strip() or response_id_to_continue
            log_openai_usage(
                log_conn,
                stage=f"round_{round_index}",
                channel=chat_id or username or "no-chat",
                model=runtime["model"],
                request_index=round_index,
                message_count=message_count,
                status="ok",
                cache_info=cache_info,
                prompt_text=prompt_log_text,
                usage=usage,
                response_id=optional_text(response.get("id")),
            )
            calls = extract_function_calls(response)
            if not calls:
                reply_text = extract_output_text(response)
                if not reply_text:
                    raise SystemExit("OpenAI API returned an empty agent response.")
                if chat_id and response_id_to_continue:
                    set_previous_response_id(chat_id, response_id_to_continue, username)
                return {
                    "status": "ok",
                    "reply_text": reply_text,
                    "tool_calls": tool_calls_executed,
                    "used_web": used_web,
                    "used_local": used_local,
                    "response_id": response_id_to_continue or "",
                    "cached_input_tokens": usage.cached_input_tokens,
                }
            tool_outputs = []
            for call in calls:
                try:
                    result = execute_tool(call, runtime)
                except Exception as exc:
                    result = {"error": str(exc) or exc.__class__.__name__}
                tool_name = str(call.get("name", ""))
                if tool_name in {"web_search", "fetch_url"}:
                    used_web = True
                if tool_name in {"list_local_files", "read_local_file", "search_local_files"}:
                    used_local = True
                tool_calls_executed += 1
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            current_input = tool_outputs
    finally:
        log_conn.close()
    raise SystemExit("Agent exceeded the configured tool round limit.")


def cmd_run(args: argparse.Namespace) -> int:
    result = run_agent(args.prompt, args.chat_id or "", args.username or "")
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.chat_id:
        raise SystemExit("Reset requires --chat-id.")
    reset_chat_state(args.chat_id)
    print(json.dumps({"status": "ok", "reply_text": "Контекст этого чата сброшен."}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Telegram task agent worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the agent for one Telegram prompt.")
    run_parser.add_argument("--chat-id", default="", help="Telegram chat id used for conversation continuity.")
    run_parser.add_argument("--username", default="", help="Telegram username for context only.")
    run_parser.add_argument("--prompt", required=True, help="Task text from Telegram.")
    run_parser.set_defaults(func=cmd_run)

    reset_parser = subparsers.add_parser("reset", help="Reset saved conversation state for one Telegram chat.")
    reset_parser.add_argument("--chat-id", required=True, help="Telegram chat id.")
    reset_parser.set_defaults(func=cmd_reset)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
