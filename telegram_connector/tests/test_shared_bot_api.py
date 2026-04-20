import io
import unittest
from urllib import error

from telegram_shared import bot_api


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
    def test_api_call_raises_timeout_specific_message(self) -> None:
        original_urlopen = bot_api.request.urlopen

        def fake_urlopen(req, timeout=65):
            raise TimeoutError("timed out")

        try:
            bot_api.request.urlopen = fake_urlopen
            with self.assertRaises(SystemExit) as ctx:
                bot_api.api_call("token", "getUpdates")
        finally:
            bot_api.request.urlopen = original_urlopen

        self.assertEqual(str(ctx.exception), "Telegram API request timed out while calling getUpdates.")

    def test_api_call_includes_urlerror_reason_details(self) -> None:
        original_urlopen = bot_api.request.urlopen

        def fake_urlopen(req, timeout=65):
            raise error.URLError("connection reset by peer")

        try:
            bot_api.request.urlopen = fake_urlopen
            with self.assertRaises(SystemExit) as ctx:
                bot_api.api_call("token", "getUpdates")
        finally:
            bot_api.request.urlopen = original_urlopen

        self.assertEqual(
            str(ctx.exception),
            "Telegram API request failed while calling getUpdates: connection reset by peer.",
        )

    def test_api_call_raises_http_status_specific_message(self) -> None:
        original_urlopen = bot_api.request.urlopen

        def fake_urlopen(req, timeout=65):
            exc = error.HTTPError(req.full_url, 502, "Bad Gateway", hdrs=None, fp=io.BytesIO(b""))
            exc.close()
            raise exc

        try:
            bot_api.request.urlopen = fake_urlopen
            with self.assertRaises(SystemExit) as ctx:
                bot_api.api_call("token", "sendMessage", {"chat_id": 1, "text": "hello"})
        finally:
            bot_api.request.urlopen = original_urlopen

        self.assertEqual(str(ctx.exception), "Telegram API HTTP 502 while calling sendMessage.")

    def test_api_call_raises_telegram_error_description(self) -> None:
        original_urlopen = bot_api.request.urlopen

        def fake_urlopen(req, timeout=65):
            return _FakeResponse(b'{"ok": false, "description": "chat not found"}')

        try:
            bot_api.request.urlopen = fake_urlopen
            with self.assertRaises(SystemExit) as ctx:
                bot_api.api_call("token", "sendMessage", {"chat_id": 1, "text": "hello"})
        finally:
            bot_api.request.urlopen = original_urlopen

        self.assertEqual(
            str(ctx.exception),
            "Telegram API error while calling sendMessage: chat not found",
        )
