"""Shared bridge runtime environment helpers."""

from __future__ import annotations

import os
from typing import Callable


def build_child_env(
    secret_env: dict[str, str],
    *,
    safe_keys: set[str],
    project_root_env_var: str,
) -> dict[str, str]:
    child_env = {key: value for key, value in os.environ.items() if key in safe_keys}
    project_root = os.environ.get(project_root_env_var, "").strip()
    if project_root:
        child_env[project_root_env_var] = project_root
    child_env.update({key: value for key, value in secret_env.items() if value})
    return child_env


def parse_allowed_chat_ids(
    config: dict[str, object],
    *,
    get_config_value: Callable[[dict[str, object], str, str], str],
) -> set[str]:
    raw = get_config_value(config, "bridge", "allowed_chat_ids")
    if not raw:
        default_chat = get_config_value(config, "telegram", "default_chat_id")
        return {default_chat} if default_chat else set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def parse_allowed_user_ids(
    config: dict[str, object],
    *,
    get_config_value: Callable[[dict[str, object], str, str], str],
    resolve_secret_value: Callable[[str, str], str],
) -> set[str]:
    raw = resolve_secret_value(get_config_value(config, "bridge", "allowed_user_ids"), "allowed Telegram user ids")
    return {item.strip() for item in raw.split(",") if item.strip()}


def parse_allowed_usernames(
    config: dict[str, object],
    *,
    get_config_value: Callable[[dict[str, object], str, str], str],
    resolve_secret_value: Callable[[str, str], str],
) -> set[str]:
    raw = resolve_secret_value(get_config_value(config, "bridge", "allowed_usernames"), "allowed Telegram usernames")
    return {item.strip().lower().lstrip("@") for item in raw.split(",") if item.strip()}


def is_user_allowed(
    *,
    user_id: int | None,
    username: str,
    allowed_user_ids: set[str],
    allowed_usernames: set[str],
) -> bool:
    if not allowed_user_ids and not allowed_usernames:
        return True
    normalized_username = username.lower().lstrip("@")
    if allowed_user_ids and str(user_id or "") not in allowed_user_ids:
        return False
    if allowed_usernames and normalized_username not in allowed_usernames:
        return False
    return True
