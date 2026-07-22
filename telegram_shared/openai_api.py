"""Shared OpenAI Responses API transport helpers."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib import error, request

from .errors import TelegramSharedError
from .redaction import redact_sensitive_text


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_ERROR_BODY_BYTES = 8192
MAX_ERROR_MESSAGE_CHARS = 500
NON_RETRYABLE_429_CODES = frozenset(
    {
        "credit_balance_exhausted",
        "insufficient_quota",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    }
)
NON_RETRYABLE_429_ERROR_TYPES = frozenset({"insufficient_quota"})


@dataclass(frozen=True)
class OpenAIResponse:
    response: dict[str, Any]
    latency_ms: int


class OpenAIRequestError(TelegramSharedError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds

    def diagnostic(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
        }


def optional_error_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return redact_sensitive_text(normalized[:MAX_ERROR_MESSAGE_CHARS])


def parse_retry_after_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw_value = value.strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def is_retryable_http_error(
    status_code: int,
    error_type: str | None,
    error_code: str | None,
) -> bool:
    if status_code == 408 or status_code in {500, 502, 503, 504}:
        return True
    if status_code != 429:
        return False
    return (
        error_type not in NON_RETRYABLE_429_ERROR_TYPES
        and error_code not in NON_RETRYABLE_429_CODES
    )


def read_http_error(exc: error.HTTPError) -> OpenAIRequestError:
    body: dict[str, Any] = {}
    try:
        raw_body = exc.read(MAX_ERROR_BODY_BYTES)
        decoded_body = json.loads(raw_body.decode("utf-8"))
        if isinstance(decoded_body, dict):
            candidate = decoded_body.get("error", decoded_body)
            if isinstance(candidate, dict):
                body = candidate
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    headers = exc.headers
    request_id = optional_error_text(headers.get("x-request-id") if headers else None)
    retry_after_seconds = parse_retry_after_seconds(headers.get("Retry-After") if headers else None)
    error_type = optional_error_text(body.get("type"))
    error_code = optional_error_text(body.get("code"))
    error_message = optional_error_text(body.get("message"))
    details = [f"HTTP {exc.code}"]
    if error_type:
        details.append(f"type={error_type}")
    if error_code:
        details.append(f"code={error_code}")
    if request_id:
        details.append(f"request_id={request_id}")
    if error_message:
        details.append(f"message={error_message}")
    return OpenAIRequestError(
        f"OpenAI API request failed ({', '.join(details)}).",
        status_code=exc.code,
        error_type=error_type,
        error_code=error_code,
        request_id=request_id,
        retryable=is_retryable_http_error(exc.code, error_type, error_code),
        retry_after_seconds=retry_after_seconds,
    )


def retry_delay_seconds(
    exc: OpenAIRequestError,
    *,
    attempt: int,
    backoff_seconds: float,
    random_func: Callable[[], float],
) -> float:
    if exc.retry_after_seconds is not None:
        return exc.retry_after_seconds
    return backoff_seconds * (2 ** (attempt - 1)) + (random_func() * backoff_seconds)


def post_responses(
    payload: dict[str, Any],
    api_key: str,
    *,
    timeout_seconds: int,
    retry_attempts: int,
    retry_backoff_seconds: float,
    urlopen_func: Callable[..., Any] = request.urlopen,
    sleep_func: Callable[[float], None] = time.sleep,
    random_func: Callable[[], float] = random.random,
) -> OpenAIResponse:
    req = request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_error: OpenAIRequestError | None = None
    for attempt in range(1, retry_attempts + 1):
        started_at = time.perf_counter()
        try:
            with urlopen_func(req, timeout=timeout_seconds) as resp:
                response = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            request_error = read_http_error(exc)
        except (TimeoutError, error.URLError, OSError) as exc:
            error_text = str(exc).lower()
            request_error = OpenAIRequestError(
                "OpenAI API network request failed.",
                error_type="network_error",
                retryable=(
                    isinstance(exc, (TimeoutError, error.URLError))
                    or "timed out" in error_text
                    or "connection reset" in error_text
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAIRequestError(
                "OpenAI API returned an invalid JSON response.",
                error_type="invalid_response",
            ) from exc
        else:
            if not isinstance(response, dict):
                raise OpenAIRequestError(
                    "OpenAI API returned an invalid JSON response.",
                    error_type="invalid_response",
                )
            return OpenAIResponse(
                response=response,
                latency_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
            )

        last_error = request_error
        if not request_error.retryable or attempt >= retry_attempts:
            break
        sleep_func(
            retry_delay_seconds(
                request_error,
                attempt=attempt,
                backoff_seconds=retry_backoff_seconds,
                random_func=random_func,
            )
        )
    if last_error is not None:
        raise last_error
    raise OpenAIRequestError("OpenAI API request failed.")
