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
