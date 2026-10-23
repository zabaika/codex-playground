"""Digest configuration, prompt bundles and window/profile resolution."""
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process
if __package__:
    from . import history_settings
else:
    import history_settings


DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS = 1200


DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS = 120


DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS = 3


DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS = 1.0


VALID_OPENAI_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


VALID_OPENAI_REASONING_SUMMARIES = frozenset({"auto", "concise", "detailed", "none"})


OPENAI_DIGEST_RETRY_ATTEMPTS = DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS


DEFAULT_PROCESS_CONFIG = common_process.load_process_config()


DEFAULT_DIGEST_SYNC_TOTAL_TIMEOUT_SECONDS = 1800


PROMPT_CACHE_BREAKPOINT_PLACEHOLDER = "{cache_breakpoint_marker}"


DIGEST_PROMPT_REQUIRED_KEYS = (
    "system_instructions",
    "shared_prompt_prefix",
    "cache_breakpoint_marker",
    "single_digest_template",
    "batch_digest_template",
    "final_digest_template",
)


@dataclass
class DigestConfig:
    time: str
    since: str
    until: str
    model: str
    sync_mode: str
    run_total_timeout_seconds: int
    termination_grace_seconds: int
    sync_total_timeout_seconds: int
    messages_per_ai_pass: int
    message_text_max_chars: int
    message_ocr_max_chars: int
    message_block_max_chars: int
    min_messages_for_ai: int
    separator_text: str
    mark_read: bool
    use_ocr: bool
    system_instructions: str
    shared_prompt_prefix: str
    cache_breakpoint_marker: str
    single_prompt_template: str
    batch_prompt_template: str
    final_prompt_template: str
    openai_api_key: str
    openai_reasoning_effort: str
    openai_reasoning_summary: str
    openai_max_output_tokens: int = DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS
    openai_timeout_seconds: int = DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS
    openai_retry_attempts: int = DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS
    openai_retry_backoff_seconds: float = DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS
    store_prompt_text: bool = False


@dataclass
class DigestLimits:
    profile: str
    sync_limit: int
    messages_per_ai_pass: int
    message_text_max_chars: int
    message_ocr_max_chars: int
    message_block_max_chars: int


def parse_bool(value: str, default: bool = False) -> bool:
    raw = value.strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_digest_prompt_file(config: dict[str, Any], *, base_dir: Path | None = None) -> Path:
    raw_path = history_settings.get_config_value(config, "digest_prompts", "file")
    if not raw_path:
        raise SystemExit(
            "Missing digest_prompts.file in runtime config. Point it to the version-controlled digest prompt TOML file."
        )
    prompt_file = Path(raw_path).expanduser()
    if not prompt_file.is_absolute():
        prompt_file = (base_dir or history_settings.RUNTIME_LOCAL_FILE.parent) / prompt_file
    return prompt_file


def load_digest_prompts(config: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, str]:
    prompt_file = resolve_digest_prompt_file(config, base_dir=base_dir)
    if not prompt_file.exists():
        raise SystemExit(f"Digest prompt file not found: {prompt_file}")
    with prompt_file.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"Digest prompt file must contain a TOML object: {prompt_file}")
    missing = [key for key in DIGEST_PROMPT_REQUIRED_KEYS if not history_settings.get_config_value(data, "digest_prompts", key)]
    if missing:
        missing_keys = ", ".join(f"digest_prompts.{key}" for key in missing)
        raise SystemExit(f"Missing {missing_keys} in digest prompt file: {prompt_file}")
    prompts = {
        key: history_settings.get_config_value(data, "digest_prompts", key)
        for key in DIGEST_PROMPT_REQUIRED_KEYS
    }
    for template_key in ("single_digest_template", "batch_digest_template", "final_digest_template"):
        if prompts[template_key].count(PROMPT_CACHE_BREAKPOINT_PLACEHOLDER) != 1:
            raise SystemExit(
                f"{template_key} must contain {PROMPT_CACHE_BREAKPOINT_PLACEHOLDER} exactly once: {prompt_file}"
            )
    return prompts


def resolve_digest_config(config: dict[str, Any]) -> DigestConfig:
    prompts = load_digest_prompts(config)
    shared_ai_section = get_nested_section(config, "digest_ai")
    model = history_settings.get_config_value(config, "processing", "model")
    if not model:
        raise SystemExit(
            "Missing processing.model in runtime config. Put the default AI model into [processing].model."
        )
    raw_min_messages_for_ai = history_settings.get_config_value(config, "digest", "min_messages_for_ai") or "1"
    raw_sync_total_timeout_seconds = (
        history_settings.get_config_value(config, "digest", "sync_total_timeout_seconds")
        or str(DEFAULT_DIGEST_SYNC_TOTAL_TIMEOUT_SECONDS)
    )
    raw_run_total_timeout_seconds = (
        history_settings.get_config_value(config, "digest", "run_total_timeout_seconds")
        or str(DEFAULT_PROCESS_CONFIG.default_run_total_timeout_seconds)
    )
    raw_termination_grace_seconds = (
        history_settings.get_config_value(config, "digest", "termination_grace_seconds")
        or str(DEFAULT_PROCESS_CONFIG.default_termination_grace_seconds)
    )
    raw_openai_max_output_tokens = str(
        shared_ai_section.get("max_output_tokens", DEFAULT_OPENAI_DIGEST_MAX_OUTPUT_TOKENS)
    ).strip()
    openai_reasoning_effort = str(shared_ai_section.get("reasoning_effort", "")).strip().lower()
    openai_reasoning_summary = str(shared_ai_section.get("reasoning_summary", "")).strip().lower()
    raw_openai_timeout_seconds = history_settings.get_config_value(config, "digest", "openai_timeout_seconds") or str(
        shared_ai_section.get("openai_timeout_seconds", DEFAULT_OPENAI_DIGEST_TIMEOUT_SECONDS)
    ).strip()
    raw_openai_retry_attempts = str(
        shared_ai_section.get("openai_retry_attempts", DEFAULT_OPENAI_DIGEST_RETRY_ATTEMPTS)
    ).strip()
    raw_openai_retry_backoff_seconds = str(
        shared_ai_section.get("openai_retry_backoff_seconds", DEFAULT_OPENAI_DIGEST_RETRY_BACKOFF_SECONDS)
    ).strip()
    try:
        min_messages_for_ai = max(0, int(raw_min_messages_for_ai))
        run_total_timeout_seconds = max(1, int(raw_run_total_timeout_seconds))
        termination_grace_seconds = max(1, int(raw_termination_grace_seconds))
        sync_total_timeout_seconds = max(1, int(raw_sync_total_timeout_seconds))
        openai_max_output_tokens = max(1, int(raw_openai_max_output_tokens))
        openai_timeout_seconds = max(1, int(raw_openai_timeout_seconds))
        openai_retry_attempts = min(5, max(1, int(raw_openai_retry_attempts)))
        openai_retry_backoff_seconds = min(60.0, max(0.0, float(raw_openai_retry_backoff_seconds)))
    except ValueError as exc:
        raise SystemExit(
            "Invalid digest.min_messages_for_ai, digest.run_total_timeout_seconds, digest.termination_grace_seconds, "
            "digest.sync_total_timeout_seconds, digest.openai_timeout_seconds, digest_ai.max_output_tokens, digest_ai.openai_timeout_seconds, "
            "digest_ai.openai_retry_attempts, or digest_ai.openai_retry_backoff_seconds in runtime config."
        ) from exc
    if not openai_reasoning_effort:
        raise SystemExit("Missing digest_ai.reasoning_effort in runtime config.")
    if openai_reasoning_effort not in VALID_OPENAI_REASONING_EFFORTS:
        allowed_efforts = ", ".join(sorted(VALID_OPENAI_REASONING_EFFORTS))
        raise SystemExit(
            f"Invalid digest_ai.reasoning_effort in runtime config. Expected one of: {allowed_efforts}."
        )
    if not openai_reasoning_summary:
        raise SystemExit("Missing digest_ai.reasoning_summary in runtime config.")
    if openai_reasoning_summary not in VALID_OPENAI_REASONING_SUMMARIES:
        allowed_summaries = ", ".join(sorted(VALID_OPENAI_REASONING_SUMMARIES))
        raise SystemExit(
            f"Invalid digest_ai.reasoning_summary in runtime config. Expected one of: {allowed_summaries}."
        )
    return DigestConfig(
        time=history_settings.get_config_value(config, "digest", "time") or "08:00",
        since=history_settings.get_config_value(config, "digest", "since") or "yesterday",
        until=history_settings.get_config_value(config, "digest", "until") or "yesterday",
        model=model,
        sync_mode=history_settings.get_config_value(config, "digest", "sync_mode") or "update",
        run_total_timeout_seconds=run_total_timeout_seconds,
        termination_grace_seconds=termination_grace_seconds,
        sync_total_timeout_seconds=sync_total_timeout_seconds,
        messages_per_ai_pass=0,
        message_text_max_chars=0,
        message_ocr_max_chars=0,
        message_block_max_chars=0,
        min_messages_for_ai=min_messages_for_ai,
        separator_text=(history_settings.get_config_value(config, "digest", "separator_text") or "").strip(),
        mark_read=parse_bool(
            history_settings.get_config_value(config, "digest", "mark_read"),
            default=True,
        ),
        use_ocr=parse_bool(
            history_settings.get_config_value(config, "processing", "ocr"),
            default=True,
        ),
        system_instructions=prompts["system_instructions"],
        shared_prompt_prefix=prompts["shared_prompt_prefix"],
        cache_breakpoint_marker=prompts["cache_breakpoint_marker"],
        single_prompt_template=prompts["single_digest_template"],
        batch_prompt_template=prompts["batch_digest_template"],
        final_prompt_template=prompts["final_digest_template"],
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip() or history_settings.get_config_value(config, "secrets", "openai_api_key"),
        openai_reasoning_effort=openai_reasoning_effort,
        openai_reasoning_summary=openai_reasoning_summary,
        openai_max_output_tokens=openai_max_output_tokens,
        openai_timeout_seconds=openai_timeout_seconds,
        openai_retry_attempts=openai_retry_attempts,
        openai_retry_backoff_seconds=openai_retry_backoff_seconds,
        store_prompt_text=parse_bool(str(shared_ai_section.get("store_prompt_text", "")).strip(), default=False),
    )


def get_nested_section(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}


resolve_relative_date_token = history_settings.resolve_relative_date_token


def resolve_digest_window(config: DigestConfig, *, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    today = current.date()
    since_value = config.since or "yesterday"
    until_value = config.until or "yesterday"
    return resolve_relative_date_token(since_value, today), resolve_relative_date_token(until_value, today)


def normalize_digest_window_values(since: str, until: str, *, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    today = current.date()
    return resolve_relative_date_token(since, today), resolve_relative_date_token(until, today)


def digest_profile_name(since: str, until: str) -> str:
    since_dt = history_settings.parse_filter_datetime_value(since)
    until_dt = history_settings.parse_filter_datetime_value(until, end_of_day=True)
    if since_dt is None or until_dt is None:
        return "day"
    span_days = max(1, int((until_dt.date() - since_dt.date()).days) + 1)
    if span_days <= 1:
        return "day"
    if span_days <= 7:
        return "week"
    return "month"


def resolve_digest_limits(config: dict[str, Any], since: str, until: str) -> DigestLimits:
    profile = digest_profile_name(since, until)
    shared_ai_section = get_nested_section(config, "digest_ai")
    section = get_nested_section(config, "digest_limits", profile)
    raw_sync_limit = str(section.get("sync_limit", "")).strip()
    raw_messages_per_ai_pass = str(shared_ai_section.get("messages_per_ai_pass", "")).strip()
    raw_message_text_max_chars = str(shared_ai_section.get("message_text_max_chars", "")).strip()
    raw_message_ocr_max_chars = str(shared_ai_section.get("message_ocr_max_chars", "")).strip()
    raw_message_block_max_chars = str(shared_ai_section.get("message_block_max_chars", "")).strip()
    if not raw_sync_limit:
        raise SystemExit(f"Missing digest_limits.{profile}.sync_limit in runtime config.")
    if not raw_messages_per_ai_pass:
        raise SystemExit("Missing digest_ai.messages_per_ai_pass in runtime config.")
    if not raw_message_text_max_chars:
        raise SystemExit("Missing digest_ai.message_text_max_chars in runtime config.")
    if not raw_message_ocr_max_chars:
        raise SystemExit("Missing digest_ai.message_ocr_max_chars in runtime config.")
    if not raw_message_block_max_chars:
        raise SystemExit("Missing digest_ai.message_block_max_chars in runtime config.")
    try:
        sync_limit = int(raw_sync_limit)
        messages_per_ai_pass = int(raw_messages_per_ai_pass)
        message_text_max_chars = int(raw_message_text_max_chars)
        message_ocr_max_chars = int(raw_message_ocr_max_chars)
        message_block_max_chars = int(raw_message_block_max_chars)
    except ValueError as exc:
        raise SystemExit(
            "Invalid integer in digest_ai.messages_per_ai_pass, digest_ai.message_text_max_chars, "
            "digest_ai.message_ocr_max_chars, digest_ai.message_block_max_chars, or "
            f"digest_limits.{profile}.sync_limit."
        ) from exc
    return DigestLimits(
        profile=profile,
        sync_limit=max(1, sync_limit),
        messages_per_ai_pass=max(1, messages_per_ai_pass),
        message_text_max_chars=max(0, message_text_max_chars),
        message_ocr_max_chars=max(0, message_ocr_max_chars),
        message_block_max_chars=max(1, message_block_max_chars),
    )


def require_openai_api_key(config: DigestConfig) -> str:
    raw_value = config.openai_api_key.strip()
    if not raw_value:
        raise SystemExit(
            "Missing OpenAI API key. Put it into telegram_connector/config/runtime.local.toml under [secrets].openai_api_key."
        )
    return history_settings.resolve_secret_value(raw_value, "OpenAI API key")
