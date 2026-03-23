#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram_shared import secrets as shared_secrets
from telegram_shared.ai_usage_stats import fetch_ai_usage_summary as shared_fetch_ai_usage_summary
from telegram_shared.ai_usage_stats import format_ai_usage_summary as shared_format_ai_usage_summary
from telegram_shared.bot_api import api_call as shared_api_call
from telegram_shared.bot_api import append_jsonl_record as shared_append_jsonl_record
from telegram_shared.bot_api import extract_chat_id as shared_extract_chat_id
from telegram_shared.bot_api import extract_text as shared_extract_text
from telegram_shared.bot_api import extract_user_id as shared_extract_user_id
from telegram_shared.bot_api import extract_username as shared_extract_username
from telegram_shared.bot_api import fetch_updates as shared_fetch_updates
from telegram_shared.bot_api import load_offset as shared_load_offset
from telegram_shared.bot_api import save_offset as shared_save_offset
from telegram_shared.bot_api import split_text_chunks as shared_split_text_chunks
from telegram_shared.bridge_env import build_child_env as shared_build_child_env
from telegram_shared.bridge_env import is_user_allowed as shared_is_user_allowed
from telegram_shared.bridge_env import parse_allowed_chat_ids as shared_parse_allowed_chat_ids
from telegram_shared.bridge_env import parse_allowed_user_ids as shared_parse_allowed_user_ids
from telegram_shared.bridge_env import parse_allowed_usernames as shared_parse_allowed_usernames
from telegram_shared.command_text import normalize_bridge_command_text as shared_normalize_bridge_command_text
from telegram_shared.config import get_config_value as shared_get_config_value
from telegram_shared.config import load_runtime_config as shared_load_runtime_config
from telegram_shared.formatting import format_inline_telegram_html as shared_format_inline_telegram_html
from telegram_shared.formatting import format_telegram_html as shared_format_telegram_html
from telegram_shared.formatting import format_telegram_line as shared_format_telegram_line
from telegram_shared.redaction import redact_sensitive_text as shared_redact_sensitive_text


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT", "")).expanduser() if os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT") else APP_DIR
BASE_DIR = PROJECT_ROOT
CONFIG_DIR = BASE_DIR / "config"
RUNTIME_LOCAL_FILE = CONFIG_DIR / "runtime.local.toml"
DATA_DIR = BASE_DIR / "data"
OFFSET_FILE = DATA_DIR / "offset.local.json"
INBOX_FILE = DATA_DIR / "inbox.jsonl"
OUTBOX_FILE = DATA_DIR / "outbox.jsonl"
AGENT_DB_FILE = DATA_DIR / "telegram_agent.sqlite3"
WORKER_FILE = APP_DIR / "telegram_agent_worker.py"
OP_REFERENCE_PREFIX = shared_secrets.OP_REFERENCE_PREFIX
_SECRET_CACHE = shared_secrets._SECRET_CACHE
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
SEND_RETRY_DELAYS = (0.5, 1.0)


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
    return shared_load_runtime_config(RUNTIME_LOCAL_FILE)


def get_config_value(config: dict[str, Any], section: str, key: str) -> str:
    return shared_get_config_value(config, section, key)


def resolve_onepassword_secret(reference: str, label: str) -> str:
    return shared_secrets.resolve_onepassword_secret(reference, label)


def resolve_secret_value(raw_value: str, label: str) -> str:
    return shared_secrets.resolve_secret_value(raw_value, label)


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
    return shared_build_child_env(
        secret_env,
        safe_keys=SAFE_SUBPROCESS_ENV_KEYS,
        project_root_env_var="TELEGRAM_AGENT_BOT_PROJECT_ROOT",
    )


def parse_allowed_chat_ids(config: dict[str, Any]) -> set[str]:
    return shared_parse_allowed_chat_ids(config, get_config_value=get_config_value)


def parse_allowed_user_ids(config: dict[str, Any]) -> set[str]:
    return shared_parse_allowed_user_ids(
        config,
        get_config_value=get_config_value,
        resolve_secret_value=resolve_secret_value,
    )


def parse_allowed_usernames(config: dict[str, Any]) -> set[str]:
    return shared_parse_allowed_usernames(
        config,
        get_config_value=get_config_value,
        resolve_secret_value=resolve_secret_value,
    )


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
    active_default_command = default_command or resolve_default_command(config)
    return shared_normalize_bridge_command_text(
        text,
        supported_commands=SUPPORTED_BRIDGE_COMMANDS,
        default_command=active_default_command,
    )


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
    return shared_api_call(token, method, payload)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_offset() -> int | None:
    return shared_load_offset(OFFSET_FILE)


def save_offset(offset: int) -> None:
    shared_save_offset(OFFSET_FILE, offset)


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
    shared_append_jsonl_record(INBOX_FILE, redact_update_for_storage(update))


def build_outbox_record(
    *,
    chat_id: str | int,
    text: str,
    formatted_text: str,
    status: str,
    attempt: int,
    error: str = "",
    api_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview = sanitize_text_for_storage(redact_sensitive_text(text))
    formatted_preview = sanitize_text_for_storage(redact_sensitive_text(formatted_text))
    payload: dict[str, Any] = {
        "created_at": now_utc(),
        "direction": "out",
        "chat_id": str(chat_id),
        "status": status,
        "attempt": attempt,
        "text_length": len(text),
        "formatted_text_length": len(formatted_text),
        "text_preview": preview[:200],
        "formatted_preview": formatted_preview[:200],
    }
    if error:
        payload["error"] = redact_sensitive_text(error)
    if isinstance(api_result, dict):
        payload["message_id"] = api_result.get("message_id")
    return payload


def append_outbox_record(record: dict[str, Any]) -> None:
    shared_append_jsonl_record(OUTBOX_FILE, record)


def log_bridge_error(message: str) -> None:
    print(redact_sensitive_text(message), file=sys.stderr, flush=True)


def extract_text(update: dict[str, Any]) -> str:
    return shared_extract_text(update)


def extract_chat_id(update: dict[str, Any]) -> int | None:
    return shared_extract_chat_id(update)


def extract_user_id(update: dict[str, Any]) -> int | None:
    return shared_extract_user_id(update)


def extract_username(update: dict[str, Any]) -> str:
    return shared_extract_username(update)


def is_user_allowed(update: dict[str, Any], *, allowed_user_ids: set[str], allowed_usernames: set[str]) -> bool:
    return shared_is_user_allowed(
        user_id=extract_user_id(update),
        username=extract_username(update),
        allowed_user_ids=allowed_user_ids,
        allowed_usernames=allowed_usernames,
    )


def extract_date(update: dict[str, Any]) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    unix_ts = message.get("date")
    if not unix_ts:
        return "-"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def send_text_message(token: str, chat_id: str | int, text: str) -> None:
    formatted_text = format_telegram_html(text)
    payload = {
        "chat_id": str(chat_id),
        "text": formatted_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    max_attempts = 1 + len(SEND_RETRY_DELAYS)
    for attempt in range(1, max_attempts + 1):
        try:
            result = api_call(token, "sendMessage", payload)
        except SystemExit as exc:
            error_text = str(exc)
            append_outbox_record(
                build_outbox_record(
                    chat_id=chat_id,
                    text=text,
                    formatted_text=formatted_text,
                    status="failed",
                    attempt=attempt,
                    error=error_text,
                )
            )
            log_bridge_error(f"sendMessage failed attempt={attempt} chat_id={chat_id}: {error_text}")
            if attempt >= max_attempts:
                raise
            time.sleep(SEND_RETRY_DELAYS[attempt - 1])
            continue
        append_outbox_record(
            build_outbox_record(
                chat_id=chat_id,
                text=text,
                formatted_text=formatted_text,
                status="sent",
                attempt=attempt,
                api_result=result if isinstance(result, dict) else None,
            )
        )
        return


def send_text_chunks(token: str, chat_id: str | int, text: str, chunk_size: int | None = None) -> None:
    active_chunk_size = resolve_text_chunk_size() if chunk_size is None else chunk_size
    for chunk in shared_split_text_chunks(text, active_chunk_size):
        send_text_message(token, chat_id, chunk)


def format_telegram_html(text: str) -> str:
    return shared_format_telegram_html(text)


def format_telegram_line(line: str) -> str:
    return shared_format_telegram_line(line)


def format_inline_telegram_html(text: str) -> str:
    return shared_format_inline_telegram_html(text)


def redact_sensitive_text(text: str) -> str:
    return shared_redact_sensitive_text(text)


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
    summary = shared_fetch_ai_usage_summary(
        AGENT_DB_FILE,
        feature="agent",
        row_limit=row_limit,
        filter_channel=chat_id,
        recent_rows_limit=recent_rounds_limit,
    )
    if summary is None:
        return None
    return {
        "global": summary["global"],
        "chat": summary.get("filtered"),
        "recent_rows": summary["recent_rows"],
        "row_limit": summary["row_limit"],
    }


def format_agent_usage_summary(summary: dict[str, Any], *, chat_id: str) -> str:
    return shared_format_ai_usage_summary(
        {
            "global": summary["global"],
            "filtered": summary.get("chat"),
            "recent_rows": summary.get("recent_rows") or [],
            "row_limit": summary.get("row_limit"),
        },
        title="Agent stats",
        subject_label="This chat",
        subject_value=chat_id,
    )


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
    return shared_fetch_updates(token, offset, timeout, api_call_func=api_call)


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
                    try:
                        send_text_message(runtime.bot_token, chat_id, f"Echo: {extract_text(update)}")
                    except SystemExit as exc:
                        log_bridge_error(f"echo reply failed for update_id={update_id}: {exc}")
            if args.run_commands:
                try:
                    handle_agent_command(runtime, update)
                except SystemExit as exc:
                    log_bridge_error(f"command handling failed for update_id={update_id}: {exc}")
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
