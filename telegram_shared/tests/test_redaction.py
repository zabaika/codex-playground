import unittest

from telegram_shared.redaction import redact_sensitive_text


class SharedRedactionTests(unittest.TestCase):
    def test_redact_sensitive_text_masks_secret_refs_and_keys(self) -> None:
        text = (
            "keychain://telegram-connector/bot_token Authorization: Bearer abcdefghijklmnop "
            "OPENAI_API_KEY=sk-123456789012 /Users/alice/file bot123456:ABCDEF"
        )

        redacted = redact_sensitive_text(text)

        self.assertNotIn("keychain://telegram-connector/bot_token", redacted)
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("sk-123456789012", redacted)
        self.assertNotIn("/Users/alice/file", redacted)
        self.assertNotIn("bot123456:ABCDEF", redacted)
        self.assertIn("<secret_ref>", redacted)
        self.assertIn("OPENAI_API_KEY=<redacted>", redacted)
        self.assertIn("<path>", redacted)
        self.assertIn("<bot_token>", redacted)


if __name__ == "__main__":
    unittest.main()
