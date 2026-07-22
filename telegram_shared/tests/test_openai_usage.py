import sqlite3
import unittest

from telegram_shared.openai_usage import OpenAIUsage
from telegram_shared.openai_usage import build_prompt_cache_info
from telegram_shared.openai_usage import extract_usage
from telegram_shared.openai_usage import log_openai_usage


AI_USAGE_SCHEMA = """
CREATE TABLE ai_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    feature TEXT NOT NULL,
    stage TEXT NOT NULL,
    channel TEXT,
    since TEXT,
    until TEXT,
    model TEXT NOT NULL,
    response_id TEXT,
    prompt_cache_key TEXT,
    prompt_cache_retention TEXT,
    prompt_version_hash TEXT,
    request_index INTEGER,
    message_count INTEGER,
    system_chars INTEGER,
    prompt_chars INTEGER,
    shared_prefix_chars INTEGER,
    shared_prefix_hash TEXT,
    prompt_hash TEXT,
    previous_prompt_hash TEXT,
    previous_response_id TEXT,
    prefix_match_chars_with_previous INTEGER,
    prompt_text TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    cache_write_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    output_chars INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    response_status TEXT,
    incomplete_reason TEXT,
    status TEXT NOT NULL,
    error TEXT
)
"""


class OpenAIUsageLoggingTests(unittest.TestCase):
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(AI_USAGE_SCHEMA)
        return conn

    def cache_info(self, prompt_text: str):
        return build_prompt_cache_info(
            cache_key="digest:test-cache",
            system_instructions="system",
            prompt_text=prompt_text,
            shared_prefix="prefix",
            prompt_version_text="system\nprefix\nsingle-template",
        )

    def test_extract_usage_reads_cache_metrics(self) -> None:
        usage = extract_usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "total_tokens": 125,
                    "input_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 40},
                    "output_tokens_details": {"reasoning_tokens": 15},
                },
                "_latency_ms": 345,
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            output_chars=48,
        )

        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.cached_input_tokens, 60)
        self.assertEqual(usage.cache_write_tokens, 40)
        self.assertEqual(usage.output_tokens, 25)
        self.assertEqual(usage.total_tokens, 125)
        self.assertEqual(usage.latency_ms, 345)
        self.assertEqual(usage.reasoning_tokens, 15)
        self.assertEqual(usage.response_status, "incomplete")
        self.assertEqual(usage.incomplete_reason, "max_output_tokens")
        self.assertEqual(usage.output_chars, 48)

    def test_extract_usage_keeps_missing_cache_write_tokens_unknown(self) -> None:
        usage = extract_usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "total_tokens": 125,
                    "input_tokens_details": {"cached_tokens": 60},
                }
            }
        )

        self.assertIsNone(usage.cache_write_tokens)

    def test_extract_usage_keeps_explicit_zero_cache_write_tokens(self) -> None:
        usage = extract_usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "total_tokens": 125,
                    "input_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 0},
                }
            }
        )

        self.assertEqual(usage.cache_write_tokens, 0)

    def test_log_openai_usage_does_not_store_prompt_text_by_default(self) -> None:
        conn = self.connect()
        try:
            log_openai_usage(
                conn,
                feature="digest",
                created_at="2026-07-03T10:00:00+00:00",
                stage="single",
                channel="@channel",
                since="2026-07-02",
                until="2026-07-02",
                model="gpt-5.4-mini",
                request_index=1,
                message_count=12,
                status="ok",
                cache_info=self.cache_info("prefix\n\nsensitive prompt text"),
                prompt_text="prefix\n\nsensitive prompt text",
                usage=OpenAIUsage(10, 4, 2, 3, 13, 200),
                response_id="resp_1",
            )
            row = conn.execute("SELECT * FROM ai_usage_log WHERE response_id = 'resp_1'").fetchone()
        finally:
            conn.close()

        assert row is not None
        self.assertIsNone(row["prompt_text"])
        self.assertEqual(row["prompt_chars"], len("prefix\n\nsensitive prompt text"))
        self.assertIsNotNone(row["prompt_hash"])
        self.assertIsNotNone(row["prompt_version_hash"])
        self.assertEqual(row["message_count"], 12)
        self.assertEqual(row["cached_input_tokens"], 4)
        self.assertEqual(row["cache_write_tokens"], 2)
        self.assertIsNone(row["reasoning_tokens"])
        self.assertIsNone(row["response_status"])
        self.assertIsNone(row["incomplete_reason"])
        self.assertIsNone(row["output_chars"])

    def test_log_openai_usage_can_store_prompt_text_for_debugging(self) -> None:
        conn = self.connect()
        try:
            log_openai_usage(
                conn,
                feature="digest",
                created_at="2026-07-03T10:00:00+00:00",
                stage="single",
                channel="@channel",
                since="2026-07-02",
                until="2026-07-02",
                model="gpt-5.4-mini",
                request_index=1,
                message_count=12,
                status="ok",
                cache_info=self.cache_info("prefix\n\nfirst"),
                prompt_text="prefix\n\nfirst",
                response_id="resp_1",
                store_prompt_text=True,
            )
            log_openai_usage(
                conn,
                feature="digest",
                created_at="2026-07-03T10:01:00+00:00",
                stage="single",
                channel="@channel",
                since="2026-07-02",
                until="2026-07-02",
                model="gpt-5.4-mini",
                request_index=2,
                message_count=13,
                status="ok",
                cache_info=self.cache_info("prefix\n\nfirst plus more"),
                prompt_text="prefix\n\nfirst plus more",
                response_id="resp_2",
                store_prompt_text=True,
            )
            row = conn.execute("SELECT * FROM ai_usage_log WHERE response_id = 'resp_2'").fetchone()
        finally:
            conn.close()

        assert row is not None
        self.assertEqual(row["prompt_text"], "prefix\n\nfirst plus more")
        self.assertEqual(row["previous_response_id"], "resp_1")
        self.assertIsNotNone(row["previous_prompt_hash"])
        self.assertGreater(row["prefix_match_chars_with_previous"], 0)


if __name__ == "__main__":
    unittest.main()
