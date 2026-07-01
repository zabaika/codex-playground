import io
import unittest
from urllib import error

from telegram_shared import bot_api
from telegram_shared.errors import TelegramApiError


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class SharedBotApiTests(unittest.TestCase):
    def test_api_call_posts_json_payload_with_content_type(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=65):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["content_type"] = req.get_header("Content-type")
            captured["payload"] = req.data
            return _FakeResponse(b'{"ok": true, "result": {"message_id": 7}}')

        result = bot_api.api_call(
            "token",
            "sendMessage",
            {"chat_id": 1, "text": "hello"},
            urlopen_func=fake_urlopen,
        )

        self.assertEqual(result, {"message_id": 7})
        self.assertEqual(captured["url"], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["content_type"], "application/json")
        self.assertEqual(captured["payload"], b'{"chat_id": 1, "text": "hello"}')

    def test_api_call_raises_timeout_specific_message(self) -> None:
        def fake_urlopen(req, timeout=65):
            raise TimeoutError("timed out")

        with self.assertRaises(TelegramApiError) as ctx:
            bot_api.api_call("token", "getUpdates", urlopen_func=fake_urlopen)

        self.assertEqual(str(ctx.exception), "Telegram API request timed out while calling getUpdates.")

    def test_api_call_includes_urlerror_reason_details(self) -> None:
        def fake_urlopen(req, timeout=65):
            raise error.URLError("connection reset by peer")

        with self.assertRaises(TelegramApiError) as ctx:
            bot_api.api_call("token", "getUpdates", urlopen_func=fake_urlopen)

        self.assertEqual(
            str(ctx.exception),
            "Telegram API request failed while calling getUpdates: connection reset by peer.",
        )

    def test_api_call_raises_http_status_specific_message(self) -> None:
        def fake_urlopen(req, timeout=65):
            exc = error.HTTPError(req.full_url, 502, "Bad Gateway", hdrs=None, fp=io.BytesIO(b""))
            exc.close()
            raise exc

        with self.assertRaises(TelegramApiError) as ctx:
            bot_api.api_call(
                "token",
                "sendMessage",
                {"chat_id": 1, "text": "hello"},
                urlopen_func=fake_urlopen,
            )

        self.assertEqual(str(ctx.exception), "Telegram API HTTP 502 while calling sendMessage.")

    def test_api_call_raises_telegram_error_description(self) -> None:
        def fake_urlopen(req, timeout=65):
            return _FakeResponse(b'{"ok": false, "description": "chat not found"}')

        with self.assertRaises(TelegramApiError) as ctx:
            bot_api.api_call(
                "token",
                "sendMessage",
                {"chat_id": 1, "text": "hello"},
                urlopen_func=fake_urlopen,
            )

        self.assertEqual(
            str(ctx.exception),
            "Telegram API error while calling sendMessage: chat not found",
        )

    def test_api_call_uses_default_telegram_error_description_when_missing(self) -> None:
        def fake_urlopen(req, timeout=65):
            return _FakeResponse(b'{"ok": false}')

        with self.assertRaises(TelegramApiError) as ctx:
            bot_api.api_call(
                "token",
                "sendMessage",
                {"chat_id": 1, "text": "hello"},
                urlopen_func=fake_urlopen,
            )

        self.assertEqual(
            str(ctx.exception),
            "Telegram API error while calling sendMessage: request failed",
        )

    def test_call_bot_api_with_retry_retries_transient_method_error(self) -> None:
        attempts: list[int] = []
        failures: list[tuple[int, str]] = []
        sleeps: list[float] = []

        def fake_call() -> dict[str, object]:
            attempts.append(len(attempts) + 1)
            if len(attempts) < 3:
                raise TelegramApiError("Telegram API request failed while calling sendMessage: connection reset by peer.")
            return {"ok": True}

        result = bot_api.call_bot_api_with_retry(
            fake_call,
            method="sendMessage",
            attempts=3,
            backoff_seconds=2,
            sleep_func=lambda seconds: sleeps.append(seconds),
            on_failed_attempt=lambda attempt, exc: failures.append((attempt, str(exc))),
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(attempts, [1, 2, 3])
        self.assertEqual([item[0] for item in failures], [1, 2])
        self.assertEqual(sleeps, [2, 4])

    def test_call_bot_api_with_retry_retries_http_5xx_method_error(self) -> None:
        attempts: list[int] = []

        def fake_call() -> dict[str, object]:
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise TelegramApiError("Telegram API HTTP 502 while calling sendMessage.")
            return {"ok": True}

        result = bot_api.call_bot_api_with_retry(
            fake_call,
            method="sendMessage",
            attempts=2,
            backoff_seconds=0,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(attempts, [1, 2])

    def test_call_bot_api_with_retry_does_not_retry_permanent_method_error(self) -> None:
        attempts: list[int] = []

        def fake_call() -> None:
            attempts.append(len(attempts) + 1)
            raise TelegramApiError("Telegram API error while calling sendMessage: chat not found")

        with self.assertRaises(TelegramApiError):
            bot_api.call_bot_api_with_retry(
                fake_call,
                method="sendMessage",
                attempts=3,
                backoff_seconds=2,
                sleep_func=lambda seconds: None,
            )

        self.assertEqual(attempts, [1])

    def test_call_bot_api_with_retry_does_not_retry_http_4xx_method_error(self) -> None:
        attempts: list[int] = []

        def fake_call() -> None:
            attempts.append(len(attempts) + 1)
            raise TelegramApiError("Telegram API HTTP 400 while calling sendMessage.")

        with self.assertRaises(TelegramApiError):
            bot_api.call_bot_api_with_retry(
                fake_call,
                method="sendMessage",
                attempts=3,
                backoff_seconds=0,
            )

        self.assertEqual(attempts, [1])

    def test_split_text_chunks_never_exceeds_telegram_limit(self) -> None:
        chunks = bot_api.split_text_chunks("x" * 5000, 5000)

        self.assertEqual([len(chunk) for chunk in chunks], [4096, 904])

    def test_fetch_updates_extends_http_timeout_beyond_long_poll_timeout(self) -> None:
        captured: dict[str, object] = {}

        def fake_api_call(token, method, payload, *, timeout_seconds=65):
            captured["token"] = token
            captured["method"] = method
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            return []

        result = bot_api.fetch_updates("token", offset=10, timeout=30, api_call_func=fake_api_call)

        self.assertEqual(result, [])
        self.assertEqual(captured["method"], "getUpdates")
        self.assertEqual(captured["payload"], {"timeout": 30, "allowed_updates": ["message", "edited_message"], "offset": 10})
        self.assertEqual(captured["timeout_seconds"], 35)
