import io
import json
import unittest
from email.message import Message
from urllib import error

from telegram_shared.openai_api import OpenAIRequestError
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
