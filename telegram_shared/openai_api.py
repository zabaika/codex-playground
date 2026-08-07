"""Shared OpenAI Responses API transport helpers."""

from __future__ import annotations

import errno
import json
import random
import socket
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
NON_RETRYABLE_HTTP_REDIRECT_STATUS_CODES = frozenset(range(300, 400))
RETRYABLE_HTTP_CLIENT_STATUS_CODES = frozenset({408})
RATE_LIMIT_HTTP_STATUS_CODES = frozenset({429})
NON_RETRYABLE_HTTP_CLIENT_STATUS_CODES = (
    frozenset(range(400, 500))
    - RETRYABLE_HTTP_CLIENT_STATUS_CODES
    - RATE_LIMIT_HTTP_STATUS_CODES
)
RETRYABLE_HTTP_SERVER_STATUS_CODES = frozenset(
    {
        500,
        502,
        503,
        504,
        520,
        521,
        522,
        523,
        524,
        525,
        530,
    }
)
NON_RETRYABLE_NETWORK_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EBADF,
        errno.EINVAL,
        errno.EISDIR,
        errno.ENOENT,
        errno.ENOSPC,
        errno.ENOTDIR,
        errno.EPERM,
    }
)


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
        cause_type: str | None = None,
        cause_errno: str | None = None,
        cause_message: str | None = None,
        attempts_made: int = 0,
        retry_attempts: int = 0,
        retry_delays_seconds: tuple[float, ...] = (),
        retry_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.cause_type = cause_type
        self.cause_errno = cause_errno
        self.cause_message = cause_message
        self.attempts_made = attempts_made
        self.retry_attempts = retry_attempts
        self.retry_delays_seconds = retry_delays_seconds
        self.retry_exhausted = retry_exhausted

    def diagnostic(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "cause_type": self.cause_type,
            "cause_errno": self.cause_errno,
            "cause_message": self.cause_message,
            "attempts_made": self.attempts_made,
            "retry_attempts": self.retry_attempts,
            "retry_delays_seconds": list(self.retry_delays_seconds),
            "retry_exhausted": self.retry_exhausted,
        }

    def operator_summary(self) -> str:
        if self.status_code is not None:
            details = [f"HTTP {self.status_code}"]
            if self.error_type:
                details.append(self.error_type)
            elif self.error_code:
                details.append(self.error_code)
        elif self.error_type == "network_error":
            details = ["network_error"]
            if self.cause_type:
                cause = self.cause_type
                if self.cause_errno:
                    cause = f"{cause}: {self.cause_errno}"
                details.append(cause)
        else:
            details = [self.error_type or "openai_error"]
        attempts = (
            f"{self.attempts_made}/{self.retry_attempts}"
            if self.retry_attempts
            else str(self.attempts_made)
        )
        if self.retry_exhausted:
            details.append(f"retry exhausted: {attempts}")
        elif self.attempts_made:
            details.append("not retryable" if not self.retryable else f"attempts: {attempts}")
        if len(details) == 1:
            return details[0]
        return f"{details[0]} ({'; '.join(details[1:])})"

    def telemetry_message(self) -> str:
        details: list[str] = []
        if self.cause_type:
            details.append(f"cause_type={self.cause_type}")
        if self.cause_errno:
            details.append(f"cause_errno={self.cause_errno}")
        if self.cause_message:
            details.append(f"cause_message={self.cause_message}")
        if self.attempts_made:
            details.append(f"attempts_made={self.attempts_made}")
        if self.retry_attempts:
            details.append(f"retry_attempts={self.retry_attempts}")
        if self.retry_exhausted:
            details.append("retry_exhausted=true")
        return f"{self} [{', '.join(details)}]" if details else str(self)


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
    if status_code in NON_RETRYABLE_HTTP_REDIRECT_STATUS_CODES:
        return False
    if (
        status_code in RETRYABLE_HTTP_CLIENT_STATUS_CODES
        or status_code in RETRYABLE_HTTP_SERVER_STATUS_CODES
    ):
        return True
    if status_code in NON_RETRYABLE_HTTP_CLIENT_STATUS_CODES:
        return False
    if status_code in RATE_LIMIT_HTTP_STATUS_CODES:
        return (
            error_type not in NON_RETRYABLE_429_ERROR_TYPES
            and error_code not in NON_RETRYABLE_429_CODES
        )
    return False


def oserror_errno_name(exc: OSError) -> str | None:
    value = exc.errno
    if not isinstance(value, int):
        return None
    for name in ("EAI_AGAIN", "EAI_NONAME", "EAI_FAIL"):
        if value == getattr(socket, name, None):
            return name
    return errno.errorcode.get(value, str(value))


def unwrap_url_error(exc: BaseException) -> BaseException:
    if isinstance(exc, error.URLError) and isinstance(exc.reason, OSError):
        return exc.reason
    return exc


def is_retryable_network_error(exc: BaseException) -> bool:
    cause = unwrap_url_error(exc)
    if isinstance(cause, TimeoutError):
        return True
    if isinstance(cause, OSError):
        return cause.errno not in NON_RETRYABLE_NETWORK_ERRNOS
    return isinstance(exc, error.URLError)


def read_network_error(exc: BaseException) -> OpenAIRequestError:
    cause = unwrap_url_error(exc)
    cause_errno = oserror_errno_name(cause) if isinstance(cause, OSError) else None
    return OpenAIRequestError(
        "OpenAI API network request failed.",
        error_type="network_error",
        retryable=is_retryable_network_error(exc),
        cause_type=cause.__class__.__name__,
        cause_errno=cause_errno,
        cause_message=optional_error_text(str(cause)),
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
    retry_delays_seconds: list[float] = []
    for attempt in range(1, retry_attempts + 1):
        started_at = time.perf_counter()
        try:
            with urlopen_func(req, timeout=timeout_seconds) as resp:
                response = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            request_error = read_http_error(exc)
        except (TimeoutError, error.URLError, OSError) as exc:
            request_error = read_network_error(exc)
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

        request_error.attempts_made = attempt
        request_error.retry_attempts = retry_attempts
        request_error.retry_delays_seconds = tuple(retry_delays_seconds)
        last_error = request_error
        if not request_error.retryable or attempt >= retry_attempts:
            request_error.retry_exhausted = request_error.retryable and attempt >= retry_attempts
            break
        retry_delay = retry_delay_seconds(
            request_error,
            attempt=attempt,
            backoff_seconds=retry_backoff_seconds,
            random_func=random_func,
        )
        retry_delays_seconds.append(retry_delay)
        sleep_func(retry_delay)
    if last_error is not None:
        raise last_error
    raise OpenAIRequestError("OpenAI API request failed.")
