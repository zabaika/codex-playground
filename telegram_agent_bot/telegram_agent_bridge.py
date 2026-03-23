#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import tomllib
from urllib import error, request


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT") else APP_DIR
BASE_DIR = PROJECT_ROOT
CONFIG_DIR = BASE_DIR / "config"
RUNTIME_LOCAL_FILE = CONFIG_DIR / "runtime.local.toml"
DATA_DIR = BASE_DIR / "data"
OFFSET_FILE = DATA_DIR / "offset.local.json"
INBOX_FILE = DATA_DIR / "inbox.jsonl"
AGENT_DB_FILE = DATA_DIR / "telegram_agent.sqlite3"
WORKER_FILE = APP_DIR / "telegram_agent_worker.py"
OP_REFERENCE_PREFIX = "op://"
_SECRET_CACHE: dict[str, str] = {}
SUPPORTED_BRIDGE_COMMANDS = {"help", "agent", "agent-stats", "reset"}
WORKER_SECRET_ENV_MAP = {
    "OPENAI_API_KEY": ("secrets", "openai_api_key", "OpenAI API key"),
}
SAFE_SUBPROCESS_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
}


@dataclass
class BridgeRuntime:
    bot_token: str
    worker_secret_env: dict[str, str]
    allowed_chat_ids: set[str]
    allowed_user_ids: set[str]
    allowed_usernames: set[str]
    text_chunk_size: int
    agent_stats_row_limit: int
    default_command: str


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


def require_token() -> str:
    config = load_runtime_config()
    token = resolve_bot_token(config)
    if not token:
        raise SystemExit(
            "Missing Telegram bot token. Put it into telegram_agent_bot/config/runtime.local.toml under [secrets].bot_token."
        )
    return token


def resolve_bot_token(config: dict[str, Any]) -> str:
    return resolve_secret_value(
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or get_config_value(config, "secrets", "bot_token"),
        "Telegram bot token",
    )


def resolve_bridge_secrets(config: dict[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for env_name, (section, key, label) in WORKER_SECRET_ENV_MAP.items():
        raw_value = os.environ.get(env_name, "").strip() or get_config_value(config, section, key)
        value = resolve_secret_value(raw_value, label)
        if value:
            resolved[env_name] = value
    return resolved


def resolve_bridge_runtime(config: dict[str, Any], *, include_worker_secrets: bool) -> BridgeRuntime:
    bot_token = resolve_bot_token(config)
    if not bot_token:
        raise SystemExit(
            "Missing Telegram bot token. Put it into telegram_agent_bot/config/runtime.local.toml under [secrets].bot_token."
        )
    worker_secret_env = resolve_bridge_secrets(config) if include_worker_secrets else {}
    return BridgeRuntime(
        bot_token=bot_token,
        worker_secret_env=worker_secret_env,
        allowed_chat_ids=parse_allowed_chat_ids(config),
        allowed_user_ids=parse_allowed_user_ids(config),
        allowed_usernames=parse_allowed_usernames(config),
        text_chunk_size=resolve_text_chunk_size(config),
        agent_stats_row_limit=resolve_agent_stats_row_limit(config),
        default_command=resolve_default_command(config),
    )


def build_worker_subprocess_env(secret_env: dict[str, str]) -> dict[str, str]:
    child_env = {key: value for key, value in os.environ.items() if key in SAFE_SUBPROCESS_ENV_KEYS}
    project_root = os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT", "").strip()
    if project_root:
        child_env["TELEGRAM_AGENT_BOT_PROJECT_ROOT"] = project_root
    child_env.update({key: value for key, value in secret_env.items() if value})
    return child_env


def parse_allowed_chat_ids(config: dict[str, Any]) -> set[str]:
    raw = get_config_value(config, "bridge", "allowed_chat_ids")
    if not raw:
        default_chat = get_config_value(config, "telegram", "default_chat_id")
        return {default_chat} if default_chat else set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def parse_allowed_user_ids(config: dict[str, Any]) -> set[str]:
    raw = resolve_secret_value(get_config_value(config, "bridge", "allowed_user_ids"), "allowed Telegram user ids")
    return {item.strip() for item in raw.split(",") if item.strip()}


def parse_allowed_usernames(config: dict[str, Any]) -> set[str]:
    raw = resolve_secret_value(get_config_value(config, "bridge", "allowed_usernames"), "allowed Telegram usernames")
    return {item.strip().lower().lstrip("@") for item in raw.split(",") if item.strip()}


def resolve_text_chunk_size(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "text_chunk_size")
    try:
        value = int(raw) if raw else 3900
    except ValueError:
        value = 3900
    return max(500, min(4096, value))


def resolve_agent_stats_row_limit(config: dict[str, Any] | None = None) -> int:
    runtime_config = config if config is not None else load_runtime_config()
    raw = get_config_value(runtime_config, "bridge", "agent_stats_row_limit")
    try:
        value = int(raw) if raw else 200
    except ValueError:
        value = 200
    return max(20, min(2000, value))


def resolve_default_command(config: dict[str, Any] | None = None) -> str:
    runtime_config = config if config is not None else load_runtime_config()
    value = get_config_value(runtime_config, "bridge", "default_command").lower().strip()
    if not value:
        return ""
    if value not in SUPPORTED_BRIDGE_COMMANDS - {"help"}:
        return ""
    return value


def normalize_bridge_command_text(text: str, config: dict[str, Any] | None = None, default_command: str = "") -> str:
    raw = text.strip()
    if not raw:
        return ""
    parts = shlex.split(raw)
    if not parts:
        return ""
    command = parts[0].strip()
    if command.startswith("/"):
        bare = command[1:]
        if "@" in bare:
            bare = bare.split("@", 1)[0]
        parts[0] = f"/{bare.lower()}"
        return " ".join(parts)
    normalized = command.lower()
    if normalized in SUPPORTED_BRIDGE_COMMANDS:
        parts[0] = f"/{normalized}"
        return " ".join(parts)
    active_default_command = default_command or resolve_default_command(config)
    if active_default_command:
        return f"/{active_default_command} {raw}".strip()
    return raw


def sanitize_text_for_storage(text: str) -> str:
    sanitized_chars: list[str] = []
    for char in text:
        if char in {"\n", "\r", "\t"} or ord(char) < 32:
            sanitized_chars.append(" ")
        else:
            sanitized_chars.append(char)
    return re.sub(r"\s+", " ", "".join(sanitized_chars)).strip()


def resolve_worker_path(config: dict[str, Any]) -> Path:
    raw = get_config_value(config, "bridge", "worker_path")
    return Path(raw) if raw else WORKER_FILE


def api_call(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with request.urlopen(req, timeout=65) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise SystemExit(f"Telegram API HTTP {exc.code} while calling {method}.") from exc
    except error.URLError as exc:
        raise SystemExit(f"Telegram API request failed while calling {method}.") from exc
    if not response.get("ok"):
        description = response.get("description") or "request failed"
        raise SystemExit(f"Telegram API error while calling {method}: {description}")
    return response["result"]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_offset() -> int | None:
    if not OFFSET_FILE.exists():
        return None
    try:
        data = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data.get("offset")


def save_offset(offset: int) -> None:
    ensure_data_dir()
    OFFSET_FILE.write_text(json.dumps({"offset": offset}, ensure_ascii=True, indent=2), encoding="utf-8")


def redact_update_for_storage(update: dict[str, Any]) -> dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    text = message.get("text") or message.get("caption") or ""
    redacted: dict[str, Any] = {
        "update_id": update.get("update_id"),
        "kind": "edited_message" if update.get("edited_message") else "message",
        "chat_id": chat.get("id"),
        "chat_type": chat.get("type"),
        "from_id": user.get("id"),
        "from_username": user.get("username"),
        "date": message.get("date"),
        "has_text": bool(text),
        "text_length": len(text),
    }
    normalized = normalize_bridge_command_text(text)
    if normalized.startswith("/"):
        redacted["command"] = normalized.split(maxsplit=1)[0]
        redacted["command_text"] = sanitize_text_for_storage(normalized)
    return redacted


def append_inbox(update: dict[str, Any]) -> None:
    ensure_data_dir()
    with INBOX_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(redact_update_for_storage(update), ensure_ascii=False) + "\n")


def extract_text(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    return (
        message.get("text")
        or message.get("caption")
        or f"<non-text message keys={','.join(sorted(message.keys()))}>"
    )


def extract_chat_id(update: dict[str, Any]) -> int | None:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    return chat.get("id")


def extract_user_id(update: dict[str, Any]) -> int | None:
    message = update.get("message") or update.get("edited_message") or {}
    user = message.get("from") or {}
    return user.get("id")


def extract_username(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    user = message.get("from") or {}
    return user.get("username") or user.get("first_name") or "unknown"


def is_user_allowed(update: dict[str, Any], *, allowed_user_ids: set[str], allowed_usernames: set[str]) -> bool:
    if not allowed_user_ids and not allowed_usernames:
        return True
    user_id = extract_user_id(update)
    username = extract_username(update).lower().lstrip("@")
    if allowed_user_ids and str(user_id or "") not in allowed_user_ids:
        return False
    if allowed_usernames and username not in allowed_usernames:
        return False
    return True


def extract_date(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    unix_ts = message.get("date")
    if not unix_ts:
        return "-"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def send_text_message(token: str, chat_id: str | int, text: str) -> None:
    formatted_text = format_telegram_html(text)
    api_call(
        token,
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": formatted_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def send_text_chunks(token: str, chat_id: str | int, text: str, chunk_size: int | None = None) -> None:
    active_chunk_size = resolve_text_chunk_size() if chunk_size is None else chunk_size
    remaining = text.strip() or "<empty response>"
    if len(remaining) <= min(4096, active_chunk_size):
        send_text_message(token, chat_id, remaining)
        return
    while remaining:
        chunk = remaining[:active_chunk_size]
        split_at = chunk.rfind("\n")
        if split_at > 100:
            chunk = chunk[:split_at]
        send_text_message(token, chat_id, chunk)
        remaining = remaining[len(chunk):].lstrip()


def format_telegram_html(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip() or "<empty response>"
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    lines = [format_telegram_line(line) for line in normalized.split("\n")]
    return "\n".join(lines)


def format_telegram_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if stripped.startswith(("- ", "* ")):
        return f"• {format_inline_telegram_html(stripped[2:].strip())}"
    if re.fullmatch(r"/[a-z0-9_@ -]+", stripped, flags=re.IGNORECASE):
        return f"<code>{html.escape(stripped)}</code>"
    if stripped.endswith(":") and len(stripped) <= 80:
        return f"<b>{html.escape(stripped[:-1])}:</b>"
    return format_inline_telegram_html(stripped)


def format_inline_telegram_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"/Users/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"/home/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"/tmp/[^\s\"']+", "<path>", redacted)
    redacted = re.sub(r"bot\d{6,}:[A-Za-z0-9_-]+", "<bot_token>", redacted)
    return redacted


def build_safe_command_response(command_text: str, completed: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any] | None]:
    status = "ok" if completed.returncode == 0 else f"failed ({completed.returncode})"
    lines = [f"Command: {command_text}", f"Status: {status}"]
    output = (completed.stdout or "").strip()
    error_output = redact_sensitive_text((completed.stderr or "").strip())
    json_output = None
    if output:
        try:
            json_output = json.loads(output)
        except json.JSONDecodeError:
            json_output = None
    if completed.returncode == 0 and isinstance(json_output, dict):
        for key in ["status", "reply_text", "tool_calls", "used_web", "used_local"]:
            if key in json_output and json_output[key] not in {None, ""}:
                lines.append(f"{key}: {json_output[key]}")
    elif error_output:
        lines.append(f"error: {error_output}")
    elif output:
        lines.append(redact_sensitive_text(output))
    return "\n".join(lines), json_output if isinstance(json_output, dict) else None


def command_help_text() -> str:
    return (
        "Bot commands:\n"
        "/help\n"
        "/agent <task>\n"
        "  run the local+web task agent and answer back into Telegram\n"
        "/agent-stats\n"
        "  show local OpenAI usage and prompt-cache summary for this bot\n"
        "/reset\n"
        "  clear saved conversation context for this chat\n"
        "\nNotes:\n"
        "- only allowed chats may run commands\n"
        "- only the configured Telegram user may run commands\n"
        "- when bridge.default_command is set, plain text is treated as that command\n"
        "- agent may inspect only configured local roots and public web pages\n"
        "- bare text command aliases are supported only for help, agent, and reset\n"
        "\nExamples:\n"
        "/agent найди в проекте обработку OCR и коротко объясни архитектуру\n"
        "найди в папке Documents все файлы AGENTS.md\n"
        "/agent проверь последние новости OpenAI за сегодня и дай 5 пунктов с ссылками\n"
        "/agent-stats\n"
        "/reset"
    )


def build_worker_command(text: str) -> list[str] | None:
    parts = shlex.split(text)
    if not parts:
        return None
    command = parts[0].lower()
    base = [sys.executable, str(WORKER_FILE)]
    if command == "/help":
        return []
    if command == "/agent-stats":
        return None
    if command == "/reset":
        if len(parts) != 1:
            raise ValueError("Reset command does not accept extra arguments.")
        return base + ["reset"]
    if command == "/agent":
        if len(parts) < 2:
            raise ValueError("Agent prompt is required. Use /agent <task>.")
        return base + ["run", "--prompt", " ".join(parts[1:])]
    raise ValueError(f"Unsupported command: {parts[0]}")


def fetch_agent_usage_summary(*, chat_id: str, row_limit: int, recent_rounds_limit: int = 3) -> dict[str, Any] | None:
    if not AGENT_DB_FILE.exists():
        return None
    conn = sqlite3.connect(AGENT_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        global_row = conn.execute(
            """
            WITH recent AS (
                SELECT *
                FROM ai_usage_log
                WHERE feature = 'agent'
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT
                COUNT(*) AS total_requests,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_requests,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_requests,
                SUM(CASE WHEN COALESCE(cached_input_tokens, 0) > 0 THEN 1 ELSE 0 END) AS cached_requests,
                COUNT(DISTINCT prompt_cache_key) AS cache_keys,
                MIN(created_at) AS first_request_at,
                MAX(created_at) AS last_request_at,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens
            FROM recent
            """
            ,
            (row_limit,),
        ).fetchone()
        chat_row = conn.execute(
            """
            WITH recent AS (
                SELECT *
                FROM ai_usage_log
                WHERE feature = 'agent'
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT
                COUNT(*) AS total_requests,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_requests,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_requests,
                SUM(CASE WHEN COALESCE(cached_input_tokens, 0) > 0 THEN 1 ELSE 0 END) AS cached_requests,
                MIN(created_at) AS first_request_at,
                MAX(created_at) AS last_request_at,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens
            FROM recent
            WHERE channel = ?
            """,
            (row_limit, chat_id),
        ).fetchone()
        recent_rows = conn.execute(
            """
            WITH recent AS (
                SELECT *
                FROM ai_usage_log
                WHERE feature = 'agent'
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT created_at, stage, channel, status, input_tokens, cached_input_tokens,
                   output_tokens, prompt_cache_key
            FROM recent
            ORDER BY id DESC
            LIMIT ?
            """,
            (row_limit, recent_rounds_limit),
        ).fetchall()
    finally:
        conn.close()
    if global_row is None or int(global_row["total_requests"] or 0) == 0:
        return None
    return {
        "global": dict(global_row),
        "chat": dict(chat_row) if chat_row is not None else None,
        "recent_rows": [dict(row) for row in recent_rows],
        "row_limit": row_limit,
    }


def format_agent_usage_summary(summary: dict[str, Any], *, chat_id: str) -> str:
    global_stats = summary["global"]
    chat_stats = summary.get("chat") or {}
    recent_rows = summary.get("recent_rows") or []
    row_limit = int(summary.get("row_limit") or 0)
    global_input = int(global_stats.get("input_tokens") or 0)
    global_cached = int(global_stats.get("cached_input_tokens") or 0)
    global_saved_pct = round((global_cached / global_input) * 100, 1) if global_input > 0 else 0.0
    lines = [
        "Agent stats:",
        f"- analysis window: latest {row_limit} requests",
        f"- all requests: {int(global_stats.get('total_requests') or 0)}",
        f"- ok: {int(global_stats.get('ok_requests') or 0)}",
        f"- errors: {int(global_stats.get('error_requests') or 0)}",
        f"- requests with cached input: {int(global_stats.get('cached_requests') or 0)}",
        f"- cache keys: {int(global_stats.get('cache_keys') or 0)}",
        f"- input tokens: {global_input}",
        f"- cached input tokens: {global_cached}",
        f"- output tokens: {int(global_stats.get('output_tokens') or 0)}",
        f"- cached share of input tokens: {global_saved_pct}%",
    ]
    first_request_at = str(global_stats.get("first_request_at") or "").strip()
    last_request_at = str(global_stats.get("last_request_at") or "").strip()
    if first_request_at:
        lines.append(f"- first request: {first_request_at}")
    if last_request_at:
        lines.append(f"- last request: {last_request_at}")
    chat_total = int(chat_stats.get("total_requests") or 0)
    if chat_total > 0:
        chat_input = int(chat_stats.get("input_tokens") or 0)
        chat_cached = int(chat_stats.get("cached_input_tokens") or 0)
        chat_saved_pct = round((chat_cached / chat_input) * 100, 1) if chat_input > 0 else 0.0
        lines.extend(
            [
                "",
                f"This chat ({chat_id}):",
                f"- requests: {chat_total}",
                f"- cached input tokens: {chat_cached}",
                f"- cached share of input tokens: {chat_saved_pct}%",
            ]
        )
    if recent_rows:
        lines.append("")
        lines.append("Latest rounds:")
        for row in recent_rows:
            input_tokens = int(row.get("input_tokens") or 0)
            cached_tokens = int(row.get("cached_input_tokens") or 0)
            cached_pct = round((cached_tokens / input_tokens) * 100, 1) if input_tokens > 0 else 0.0
            lines.append(
                f"- {row.get('stage')}: {row.get('status')}, input={input_tokens}, cached={cached_tokens} ({cached_pct}%), output={int(row.get('output_tokens') or 0)}"
            )
    return "\n".join(lines)


def handle_agent_command(runtime: BridgeRuntime, update: dict[str, Any]) -> None:
    chat_id = extract_chat_id(update)
    if chat_id is None:
        return
    if runtime.allowed_chat_ids and str(chat_id) not in runtime.allowed_chat_ids:
        send_text_message(runtime.bot_token, chat_id, f"Chat {chat_id} is not allowed to run bridge commands.")
        return
    if not is_user_allowed(update, allowed_user_ids=runtime.allowed_user_ids, allowed_usernames=runtime.allowed_usernames):
        send_text_message(runtime.bot_token, chat_id, "This Telegram user is not allowed to run bot commands.")
        return
    text = normalize_bridge_command_text(extract_text(update), default_command=runtime.default_command)
    if not text.startswith("/"):
        return
    if text == "/help":
        send_text_message(runtime.bot_token, chat_id, command_help_text())
        return
    if text == "/agent-stats":
        summary = fetch_agent_usage_summary(chat_id=str(chat_id), row_limit=runtime.agent_stats_row_limit)
        if summary is None:
            send_text_message(runtime.bot_token, chat_id, "Agent stats are not available yet. Run at least one /agent request first.")
            return
        send_text_chunks(
            runtime.bot_token,
            chat_id,
            format_agent_usage_summary(summary, chat_id=str(chat_id)),
            chunk_size=runtime.text_chunk_size,
        )
        return
    try:
        argv = build_worker_command(text)
    except ValueError as exc:
        send_text_message(runtime.bot_token, chat_id, f"{exc}\n\n{command_help_text()}")
        return
    if not argv:
        send_text_message(runtime.bot_token, chat_id, command_help_text())
        return
    script_path = Path(argv[1])
    if not script_path.exists():
        send_text_message(runtime.bot_token, chat_id, f"Bridge command target not found: {script_path.name}")
        return
    argv += ["--chat-id", str(chat_id)]
    if len(argv) >= 3 and argv[2] == "run":
        argv += ["--username", extract_username(update)]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(BASE_DIR),
            capture_output=True,
            env=build_worker_subprocess_env(runtime.worker_secret_env),
            text=True,
            timeout=3600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        send_text_message(runtime.bot_token, chat_id, "Command timed out after 3600 seconds.")
        return
    safe_response, json_output = build_safe_command_response(" ".join(argv[2:]), completed)
    if completed.returncode == 0 and isinstance(json_output, dict):
        reply_text = str(json_output.get("reply_text", "")).strip()
        if reply_text:
            send_text_chunks(runtime.bot_token, chat_id, reply_text, chunk_size=runtime.text_chunk_size)
            return
    send_text_chunks(runtime.bot_token, chat_id, safe_response, chunk_size=runtime.text_chunk_size)


def cmd_get_me(args: argparse.Namespace) -> int:
    config = load_runtime_config()
    runtime = resolve_bridge_runtime(config, include_worker_secrets=False)
    result = api_call(runtime.bot_token, "getMe")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    config = load_runtime_config()
    runtime = resolve_bridge_runtime(config, include_worker_secrets=False)
    chat_id = (
        args.chat_id
        or get_config_value(config, "telegram", "default_chat_id")
        or os.environ.get("TELEGRAM_DEFAULT_CHAT_ID", "").strip()
    )
    if not chat_id:
        raise SystemExit(
            "Missing chat id. Pass --chat-id or set [telegram].default_chat_id in telegram_agent_bot/config/runtime.local.toml."
        )
    result = api_call(
        runtime.bot_token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": format_telegram_html(args.text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def fetch_updates(token: str, offset: int | None, timeout: int) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "edited_message"]}
    if offset is not None:
        payload["offset"] = offset
    result = api_call(token, "getUpdates", payload)
    if not isinstance(result, list):
        raise SystemExit(f"Unexpected getUpdates payload: {result}")
    return result


def print_update(update: dict[str, Any], runtime: BridgeRuntime) -> None:
    chat_id = extract_chat_id(update)
    username = extract_username(update)
    text = extract_text(update)
    date = extract_date(update)
    normalized = normalize_bridge_command_text(text, default_command=runtime.default_command)
    command = normalized.split(maxsplit=1)[0] if normalized.startswith("/") else "<message>"
    print(f"[{date}] chat_id={chat_id} from={username} event={command} text_length={len(text)}")


def cmd_listen(args: argparse.Namespace) -> int:
    config = load_runtime_config()
    runtime = resolve_bridge_runtime(config, include_worker_secrets=args.run_commands)
    offset = None if args.from_scratch else load_offset()
    print("Listening for Telegram updates.")
    while True:
        updates = fetch_updates(runtime.bot_token, offset, args.timeout)
        if not updates:
            if args.once:
                return 0
            continue
        for update in updates:
            append_inbox(update)
            print_update(update, runtime)
            update_id = update["update_id"]
            offset = update_id + 1
            save_offset(offset)
            if args.echo:
                chat_id = extract_chat_id(update)
                if chat_id is not None:
                    send_text_message(runtime.bot_token, chat_id, f"Echo: {extract_text(update)}")
            if args.run_commands:
                handle_agent_command(runtime, update)
        if args.once:
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Telegram agent bot bridge using Bot API long polling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_me = subparsers.add_parser("get-me", help="Check bot connectivity and print bot metadata.")
    get_me.set_defaults(func=cmd_get_me)

    send = subparsers.add_parser("send", help="Send a text message to a chat.")
    send.add_argument("--chat-id", help="Telegram chat id. If omitted, TELEGRAM_DEFAULT_CHAT_ID is used.")
    send.add_argument("text", help="Message text to send.")
    send.set_defaults(func=cmd_send)

    listen = subparsers.add_parser("listen", help="Receive incoming updates via long polling.")
    listen.add_argument("--timeout", type=int, default=30, help="Long polling timeout in seconds.")
    listen.add_argument("--once", action="store_true", help="Poll once and exit.")
    listen.add_argument("--echo", action="store_true", help="Echo received text back into the same chat.")
    listen.add_argument(
        "--run-commands",
        action="store_true",
        help="Allow incoming bot commands to control telegram_agent_worker.py for approved chat ids.",
    )
    listen.add_argument(
        "--from-scratch",
        action="store_true",
        help="Ignore saved offset and read updates from the current backlog.",
    )
    listen.set_defaults(func=cmd_listen)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
