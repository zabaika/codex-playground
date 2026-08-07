import errno
import io
import json
import unittest
from email.message import Message
from urllib import error

from telegram_shared.openai_api import OpenAIRequestError
from telegram_shared.openai_api import is_retryable_http_error
from telegram_shared.openai_api import post_responses


class OpenAIResponsesTransportTests(unittest.TestCase):
    def test_retries_503_with_exponential_backoff(self) -> None:
        attempts = {"count": 0}
        delays: list[float] = []

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id":"resp_1","output_text":"summary"}'

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise error.HTTPError(
                    "https://api.openai.com/v1/responses",
                    503,
                    "overloaded",
                    Message(),
                    io.BytesIO(b'{"error":{"type":"server_error","code":"overloaded"}}'),
                )
            return FakeResponse()

        result = post_responses(
            {"model": "gpt-5.4-mini"},
            "key",
            timeout_seconds=120,
            retry_attempts=3,
            retry_backoff_seconds=2,
            urlopen_func=fake_urlopen,
            sleep_func=delays.append,
            random_func=lambda: 0.5,
        )

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(delays, [3.0])
        self.assertEqual(result.response["id"], "resp_1")

    def test_retries_only_request_timeout_and_eligible_rate_limit_client_errors(self) -> None:
        for status_code in range(400, 500):
            with self.subTest(status_code=status_code):
                expected = status_code in {408, 429}
                self.assertEqual(
                    is_retryable_http_error(status_code, "rate_limit_error", "rate_limit_exceeded"),
                    expected,
                )

        self.assertFalse(
            is_retryable_http_error(429, "insufficient_quota", "rate_limit_exceeded")
        )

    def test_does_not_retry_redirect_statuses(self) -> None:
        for status_code in range(300, 400):
            with self.subTest(status_code=status_code):
                self.assertFalse(is_retryable_http_error(status_code, None, None))

    def test_retries_transient_edge_server_errors(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id":"resp_1","output_text":"summary"}'

        for status_code in (520, 521, 522, 523, 524, 525, 530):
            with self.subTest(status_code=status_code):
                attempts = {"count": 0}
                delays: list[float] = []

                def fake_urlopen(_req: object, timeout: int = 120) -> object:
                    attempts["count"] += 1
                    if attempts["count"] == 1:
                        raise error.HTTPError(
                            "https://api.openai.com/v1/responses",
                            status_code,
                            "edge error",
                            Message(),
                            io.BytesIO(b""),
                        )
                    return FakeResponse()

                result = post_responses(
                    {"model": "gpt-5.4-mini"},
                    "key",
                    timeout_seconds=120,
                    retry_attempts=3,
                    retry_backoff_seconds=1,
                    urlopen_func=fake_urlopen,
                    sleep_func=delays.append,
                    random_func=lambda: 0.5,
                )

                self.assertEqual(result.response["id"], "resp_1")
                self.assertEqual(attempts["count"], 2)
                self.assertEqual(delays, [1.5])

    def test_does_not_retry_nontransient_server_errors(self) -> None:
        for status_code in (501, 526):
            with self.subTest(status_code=status_code):
                attempts = {"count": 0}

                def fake_urlopen(_req: object, timeout: int = 120) -> object:
                    attempts["count"] += 1
                    raise error.HTTPError(
                        "https://api.openai.com/v1/responses",
                        status_code,
                        "permanent server error",
                        Message(),
                        io.BytesIO(b""),
                    )

                with self.assertRaises(OpenAIRequestError) as ctx:
                    post_responses(
                        {"model": "gpt-5.4-mini"},
                        "key",
                        timeout_seconds=120,
                        retry_attempts=3,
                        retry_backoff_seconds=1,
                        urlopen_func=fake_urlopen,
                        sleep_func=lambda seconds: self.fail("nontransient server errors must not be retried"),
                    )

                self.assertEqual(attempts["count"], 1)
                self.assertFalse(ctx.exception.retryable)
                self.assertEqual(ctx.exception.operator_summary(), f"HTTP {status_code} (not retryable)")

    def test_honors_retry_after_for_rate_limit(self) -> None:
        attempts = {"count": 0}
        delays: list[float] = []

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id":"resp_1","output_text":"summary"}'

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            if attempts["count"] == 1:
                headers = Message()
                headers["Retry-After"] = "7"
                raise error.HTTPError(
                    "https://api.openai.com/v1/responses",
                    429,
                    "rate limited",
                    headers,
                    io.BytesIO(b'{"error":{"type":"rate_limit_error","code":"rate_limit_exceeded"}}'),
                )
            return FakeResponse()

        post_responses(
            {"model": "gpt-5.4-mini"},
            "key",
            timeout_seconds=120,
            retry_attempts=3,
            retry_backoff_seconds=1,
            urlopen_func=fake_urlopen,
            sleep_func=delays.append,
        )

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(delays, [7.0])

    def test_retries_network_os_error_and_continues(self) -> None:
        attempts = {"count": 0}
        delays: list[float] = []

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id":"resp_1","output_text":"summary"}'

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError(errno.ENETDOWN, "Network is down")
            return FakeResponse()

        result = post_responses(
            {"model": "gpt-5.4-mini"},
            "key",
            timeout_seconds=120,
            retry_attempts=3,
            retry_backoff_seconds=1,
            urlopen_func=fake_urlopen,
            sleep_func=delays.append,
            random_func=lambda: 0.5,
        )

        self.assertEqual(result.response["id"], "resp_1")
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(delays, [1.5])

    def test_reports_exhausted_network_retries_without_leaking_cause(self) -> None:
        attempts = {"count": 0}
        delays: list[float] = []

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            raise ConnectionResetError(errno.ECONNRESET, "Bearer sk-secret-value-123456789")

        with self.assertRaises(OpenAIRequestError) as ctx:
            post_responses(
                {"model": "gpt-5.4-mini"},
                "key",
                timeout_seconds=120,
                retry_attempts=3,
                retry_backoff_seconds=1,
                urlopen_func=fake_urlopen,
                sleep_func=delays.append,
                random_func=lambda: 0.5,
            )

        self.assertEqual(attempts["count"], 3)
        self.assertEqual(delays, [1.5, 2.5])
        self.assertEqual(ctx.exception.operator_summary(), "network_error (ConnectionResetError: ECONNRESET; retry exhausted: 3/3)")
        self.assertEqual(ctx.exception.diagnostic()["retry_delays_seconds"], [1.5, 2.5])
        self.assertTrue(ctx.exception.diagnostic()["retry_exhausted"])
        self.assertNotIn("sk-secret-value", ctx.exception.telemetry_message())
        self.assertIn("<api_key>", ctx.exception.telemetry_message())

    def test_does_not_retry_permanent_local_os_error(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            raise PermissionError(errno.EACCES, "Permission denied")

        with self.assertRaises(OpenAIRequestError) as ctx:
            post_responses(
                {"model": "gpt-5.4-mini"},
                "key",
                timeout_seconds=120,
                retry_attempts=3,
                retry_backoff_seconds=1,
                urlopen_func=fake_urlopen,
                sleep_func=lambda seconds: self.fail("permanent local errors must not be retried"),
            )

        self.assertEqual(attempts["count"], 1)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.diagnostic()["cause_errno"], "EACCES")

    def test_does_not_retry_permanent_os_error_wrapped_in_url_error(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            raise error.URLError(PermissionError(errno.EACCES, "Permission denied"))

        with self.assertRaises(OpenAIRequestError) as ctx:
            post_responses(
                {"model": "gpt-5.4-mini"},
                "key",
                timeout_seconds=120,
                retry_attempts=3,
                retry_backoff_seconds=1,
                urlopen_func=fake_urlopen,
                sleep_func=lambda seconds: self.fail("permanent local errors must not be retried"),
            )

        self.assertEqual(attempts["count"], 1)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.diagnostic()["cause_type"], "PermissionError")
        self.assertEqual(ctx.exception.diagnostic()["cause_errno"], "EACCES")

    def test_does_not_retry_403_and_redacts_error_message(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(_req: object, timeout: int = 120) -> object:
            attempts["count"] += 1
            headers = Message()
            headers["x-request-id"] = "req_403"
            raise error.HTTPError(
                "https://api.openai.com/v1/responses",
                403,
                "forbidden",
                headers,
                io.BytesIO(
                    b'{"error":{"type":"permission_error","code":"content_policy_violation","message":"Bearer sk-secret-value-123456789"}}'
                ),
            )

        with self.assertRaises(OpenAIRequestError) as ctx:
            post_responses(
                {"model": "gpt-5.4-mini"},
                "key",
                timeout_seconds=120,
                retry_attempts=3,
                retry_backoff_seconds=1,
                urlopen_func=fake_urlopen,
                sleep_func=lambda seconds: self.fail("403 must not be retried"),
            )

        self.assertEqual(attempts["count"], 1)
        self.assertEqual(ctx.exception.error_code, "content_policy_violation")
        self.assertEqual(ctx.exception.request_id, "req_403")
        self.assertNotIn("sk-secret-value", str(ctx.exception))
        self.assertIn("<api_key>", str(ctx.exception))

    def test_does_not_retry_insufficient_quota_by_type_or_code(self) -> None:
        for error_type, error_code in (
            ("insufficient_quota", "rate_limit_exceeded"),
            ("rate_limit_error", "insufficient_quota"),
        ):
            with self.subTest(error_type=error_type, error_code=error_code):
                attempts = {"count": 0}

                def fake_urlopen(_req: object, timeout: int = 120) -> object:
                    attempts["count"] += 1
                    body = json.dumps({"error": {"type": error_type, "code": error_code}}).encode("utf-8")
                    raise error.HTTPError(
                        "https://api.openai.com/v1/responses",
                        429,
                        "credits exhausted",
                        Message(),
                        io.BytesIO(body),
                    )

                with self.assertRaises(OpenAIRequestError) as ctx:
                    post_responses(
                        {"model": "gpt-5.4-mini"},
                        "key",
                        timeout_seconds=120,
                        retry_attempts=3,
                        retry_backoff_seconds=1,
                        urlopen_func=fake_urlopen,
                        sleep_func=lambda seconds: self.fail("quota errors must not be retried"),
                    )

                self.assertEqual(attempts["count"], 1)
                self.assertEqual(ctx.exception.error_type, error_type)
                self.assertEqual(ctx.exception.error_code, error_code)


if __name__ == "__main__":
    unittest.main()
