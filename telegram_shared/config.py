from pathlib import Path
from typing import Any
import tomllib


def load_runtime_config(runtime_file: Path) -> dict[str, Any]:
    if not runtime_file.exists():
        return {}
    with runtime_file.open("rb") as fh:
        data = tomllib.load(fh)
    return data if isinstance(data, dict) else {}


def get_config_value(config: dict[str, Any], section: str, key: str) -> str:
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return ""
    value = section_data.get(key, "")
    return str(value).strip()


def parse_int_range(raw_value: str, *, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(raw_value) if raw_value else default
    except ValueError:
        value = default
    return max(min_value, min(max_value, value))


DEFAULT_BRIDGE_TEXT_CHUNK_SIZE = 3900
MIN_BRIDGE_TEXT_CHUNK_SIZE = 500
MAX_BRIDGE_TEXT_CHUNK_SIZE = 4096
DEFAULT_AGENT_STATS_ROW_LIMIT = 200
MIN_AGENT_STATS_ROW_LIMIT = 20
MAX_AGENT_STATS_ROW_LIMIT = 2000
DEFAULT_BRIDGE_WORKER_PROCESS_TIMEOUT_SECONDS = 3600
MIN_BRIDGE_WORKER_PROCESS_TIMEOUT_SECONDS = 60
MAX_BRIDGE_WORKER_PROCESS_TIMEOUT_SECONDS = 14400


def resolve_bridge_text_chunk_size(config: dict[str, Any]) -> int:
    return parse_int_range(
        get_config_value(config, "bridge", "text_chunk_size"),
        default=DEFAULT_BRIDGE_TEXT_CHUNK_SIZE,
        min_value=MIN_BRIDGE_TEXT_CHUNK_SIZE,
        max_value=MAX_BRIDGE_TEXT_CHUNK_SIZE,
    )


def resolve_agent_stats_row_limit(config: dict[str, Any]) -> int:
    return parse_int_range(
        get_config_value(config, "bridge", "agent_stats_row_limit"),
        default=DEFAULT_AGENT_STATS_ROW_LIMIT,
        min_value=MIN_AGENT_STATS_ROW_LIMIT,
        max_value=MAX_AGENT_STATS_ROW_LIMIT,
    )


def resolve_bridge_worker_process_timeout_seconds(config: dict[str, Any]) -> int:
    return parse_int_range(
        get_config_value(config, "bridge", "worker_process_timeout_seconds"),
        default=DEFAULT_BRIDGE_WORKER_PROCESS_TIMEOUT_SECONDS,
        min_value=MIN_BRIDGE_WORKER_PROCESS_TIMEOUT_SECONDS,
        max_value=MAX_BRIDGE_WORKER_PROCESS_TIMEOUT_SECONDS,
    )
