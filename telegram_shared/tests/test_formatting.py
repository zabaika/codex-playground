import unittest

from telegram_shared.formatting import format_inline_telegram_html
from telegram_shared.formatting import format_telegram_html


class SharedFormattingTests(unittest.TestCase):
    def test_format_telegram_html_escapes_and_normalizes_text(self) -> None:
        self.assertEqual(
            format_telegram_html("a < b\n\n\n- item"),
            "a &lt; b\n\n• item",
        )

    def test_format_telegram_html_formats_headings_and_commands(self) -> None:
        self.assertEqual(
            format_telegram_html("Bot commands:\n/agent test"),
            "<b>Bot commands:</b>\n<code>/agent test</code>",
        )

    def test_format_inline_telegram_html_supports_simple_bold_and_code(self) -> None:
        self.assertEqual(
            format_inline_telegram_html("use `rg` and **README**"),
            "use <code>rg</code> and <b>README</b>",
        )


if __name__ == "__main__":
    unittest.main()
