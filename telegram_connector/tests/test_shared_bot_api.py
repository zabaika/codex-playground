import unittest
from urllib import error

from telegram_shared import bot_api


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
