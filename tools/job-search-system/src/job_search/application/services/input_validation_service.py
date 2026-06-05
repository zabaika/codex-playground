from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit


class InputValidationService:
    @staticmethod
    def required_string(value: object, field_name: str, *, max_length: int | None = None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required")
        if max_length is not None and len(normalized) > max_length:
            raise ValueError(f"{field_name} must be at most {max_length} characters")
        return normalized

    @staticmethod
    def enum_value(enum_cls: type[Enum], value: str, field_name: str) -> str:
        allowed = {str(item.value) for item in enum_cls}
        if value not in allowed:
            raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed))}")
        return value

    @staticmethod
    def optional_iso_datetime(value: str | None, field_name: str) -> str | None:
        if value in (None, ""):
            return None
        raw = str(value)
        try:
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO 8601 datetime") from exc
        return raw

    @staticmethod
    def optional_http_url(value: object, field_name: str) -> str | None:
        if value in (None, ""):
            return None
        raw = str(value).strip()
        if raw.startswith("www."):
            raw = f"https://{raw}"
        parts = urlsplit(raw)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"{field_name} must be an http(s) URL")
        return raw
