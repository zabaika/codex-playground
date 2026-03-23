import importlib.util
import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "telegram_digest.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("telegram_digest_module", MODULE_PATH)
telegram_digest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telegram_digest)


class TelegramDigestTests(unittest.TestCase):
    def test_resolve_digest_config_reads_defaults_and_prompt_templates(self) -> None:
        config = {
            "processing": {
                "model": "test-model",
                "ocr": "false",
            },
            "digest": {
                "time": "09:30",
                "since": "yesterday",
                "until": "today",
                "sync_mode": "tail",
            },
            "digest_prompts": {
                "system_instructions": "system prompt",
                "shared_prompt_prefix": "Shared {channel_name} {since} {until}",
                "batch_digest_template": "Batch={batch_index}; Count={message_count}; Prev={previous_batch_summary}; {message_block}",
                "final_digest_template": "Final={channel_name}; Total={message_count}; Batches={batch_count}; {batch_summary_block}",
            },
            "secrets": {
                "openai_api_key": "op://Personal/item/openai_api_key",
            },
        }

        result = telegram_digest.resolve_digest_config(config)

        self.assertEqual(result.time, "09:30")
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.sync_mode, "tail")
        self.assertEqual(result.ai_batch_size, 0)
        self.assertTrue(result.mark_read)
        self.assertFalse(result.use_ocr)
        self.assertEqual(result.system_instructions, "system prompt")
        self.assertEqual(result.shared_prompt_prefix, "Shared {channel_name} {since} {until}")
        self.assertEqual(result.batch_prompt_template, "Batch={batch_index}; Count={message_count}; Prev={previous_batch_summary}; {message_block}")
        self.assertEqual(result.final_prompt_template, "Final={channel_name}; Total={message_count}; Batches={batch_count}; {batch_summary_block}")
        self.assertEqual(result.openai_api_key, "op://Personal/item/openai_api_key")

    def test_resolve_digest_window_uses_relative_defaults(self) -> None:
        config = telegram_digest.DigestConfig(
            time="08:00",
            since="yesterday",
            until="yesterday",
            model="gpt-5-mini",
            sync_mode="update",
            ai_batch_size=100,
            mark_read=False,
            use_ocr=True,
            system_instructions="system",
            shared_prompt_prefix="shared {channel_name} {since} {until}",
            batch_prompt_template="{channel_name} {message_block}",
            final_prompt_template="{channel_name} {batch_summary_block}",
            openai_api_key="k",
        )

        since, until = telegram_digest.resolve_digest_window(
            config,
            now=datetime(2026, 3, 18, 7, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(since, "2026-03-17")
        self.assertEqual(until, "2026-03-17")

    def test_resolve_relative_date_token_supports_week_and_month_aliases(self) -> None:
        today = datetime(2026, 3, 18, 8, 0, tzinfo=timezone.utc).date()
        self.assertEqual(telegram_digest.resolve_relative_date_token("week", today), "2026-03-11")
        self.assertEqual(telegram_digest.resolve_relative_date_token("month", today), "2026-02-16")
        self.assertEqual(telegram_digest.resolve_relative_date_token("-3d", today), "2026-03-15")

    def test_normalize_digest_window_values_resolves_cli_aliases(self) -> None:
        since, until = telegram_digest.normalize_digest_window_values(
            "yesterday",
            "week",
            now=datetime(2026, 3, 18, 8, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(since, "2026-03-17")
        self.assertEqual(until, "2026-03-11")

    def test_render_message_block_includes_sender_metadata(self) -> None:
        rendered = telegram_digest.render_message_block(
            [
                {
                    "channel_id": 123,
                    "title": "vc.ru",
                    "username": "vcnews",
                    "message_id": 1,
                    "date_utc": "2026-03-17T09:00:00+00:00",
                    "sender_username": "alice",
                    "sender_display_name": "Alice",
                    "forwards": 12,
                    "replies": 34,
                    "text": "hello",
                    "ocr_text": "caption",
                }
            ],
            max_chars=500,
        )

        self.assertEqual(rendered.channel_name, "vc.ru")
        self.assertEqual(rendered.message_count, 1)
        self.assertIn("sender=Alice (@alice)", rendered.message_block)
        self.assertIn("link=https://t.me/vcnews/1", rendered.message_block)
        self.assertIn("forwards=12", rendered.message_block)
        self.assertIn("replies=34", rendered.message_block)
        self.assertIn("ocr=caption", rendered.message_block)

    def test_build_message_link_uses_private_channel_fallback(self) -> None:
        link = telegram_digest.build_message_link({"channel_id": 2428609899, "message_id": 8, "username": None})
        self.assertEqual(link, "https://t.me/c/2428609899/8")

    def test_build_batch_digest_prompt_uses_template(self) -> None:
        prompt = telegram_digest.build_batch_digest_prompt(
            "Shared {channel_name} {since} {until}",
            "Digest {channel_name} {since} {until} {batch_index} {message_count} {previous_batch_summary}\n{message_block}",
            "vc.ru",
            "2026-03-17",
            "2026-03-17",
            2,
            1,
            "id=1\nsender=Alice (@alice)\ntext=hello",
            "prev summary",
        )

        self.assertTrue(prompt.startswith("Shared vc.ru 2026-03-17 2026-03-17"))
        self.assertIn("Digest vc.ru 2026-03-17 2026-03-17 2 1 prev summary", prompt)
        self.assertIn("sender=Alice (@alice)", prompt)

    def test_build_prompt_cache_info_uses_shared_prefix_hash_and_common_key(self) -> None:
        info = telegram_digest.build_prompt_cache_info(
            model="gpt-5.4-mini",
            channel="vc.ru",
            since="2026-03-17",
            until="2026-03-17",
            system_instructions="system",
            shared_prompt_prefix="Shared {channel_name} {since} {until}",
            prompt="Shared vc.ru 2026-03-17 2026-03-17\n\nbody",
        )

        self.assertTrue(info.cache_key.startswith("digest:"))
        self.assertLessEqual(len(info.cache_key), 64)
        self.assertEqual(info.cache_retention, "in_memory")
        self.assertEqual(info.system_chars, len("system"))
        self.assertEqual(info.prompt_chars, len("Shared vc.ru 2026-03-17 2026-03-17\n\nbody"))
        self.assertEqual(info.shared_prefix_chars, len("Shared vc.ru 2026-03-17 2026-03-17"))
        self.assertTrue(info.shared_prefix_hash)

    def test_extract_usage_reads_cached_tokens(self) -> None:
        usage = telegram_digest.extract_usage(
            {
                "usage": {
                    "input_tokens": 7800,
                    "output_tokens": 520,
                    "total_tokens": 8320,
                    "input_tokens_details": {"cached_tokens": 2048},
                }
            },
            latency_ms=321,
        )

        self.assertEqual(usage.input_tokens, 7800)
        self.assertEqual(usage.cached_input_tokens, 2048)
        self.assertEqual(usage.output_tokens, 520)
        self.assertEqual(usage.total_tokens, 8320)
        self.assertEqual(usage.latency_ms, 321)

    def test_allocate_sync_limits_splits_total_across_channels(self) -> None:
        plans = telegram_digest.allocate_sync_limits(["@a", "@b", "@c"], 10)
        self.assertEqual([plan.limit for plan in plans], [4, 3, 3])

    def test_iter_message_batches_uses_overlap(self) -> None:
        messages = [{"message_id": idx} for idx in range(1, 8)]
        batches = list(telegram_digest.iter_message_batches(messages, 3))
        self.assertEqual([[item["message_id"] for item in batch] for batch in batches], [[1, 2, 3], [3, 4, 5], [5, 6, 7]])

    def test_build_parser_accepts_run_overrides(self) -> None:
        parser = telegram_digest.build_parser()

        args = parser.parse_args(["run", "--channel", "@vcnews", "--since", "2026-03-15", "--until", "2026-03-16", "--auth-mode", "bot"])

        self.assertEqual(args.channel, "@vcnews")
        self.assertEqual(args.since, "2026-03-15")
        self.assertEqual(args.until, "2026-03-16")
        self.assertEqual(args.auth_mode, "bot")

    def test_resolve_digest_limits_uses_profile_specific_defaults(self) -> None:
        config = {
            "digest_limits": {
                "day": {"sync_limit": "6100", "ai_batch_size": "111"},
                "week": {"sync_limit": "43000", "ai_batch_size": "181"},
                "month": {"sync_limit": "181000", "ai_batch_size": "241"},
            }
        }

        day = telegram_digest.resolve_digest_limits(config, "2026-03-17", "2026-03-17")
        week = telegram_digest.resolve_digest_limits(config, "2026-03-11", "2026-03-17")
        month = telegram_digest.resolve_digest_limits(config, "2026-02-17", "2026-03-17")

        self.assertEqual((day.profile, day.sync_limit, day.ai_batch_size), ("day", 6100, 111))
        self.assertEqual((week.profile, week.sync_limit, week.ai_batch_size), ("week", 43000, 181))
        self.assertEqual((month.profile, month.sync_limit, month.ai_batch_size), ("month", 181000, 241))

    def test_cmd_cron_line_uses_configured_time(self) -> None:
        original_load = telegram_digest.history_client.load_runtime_config
        telegram_digest.history_client.load_runtime_config = lambda: {
            "processing": {"model": "test-model"},
            "digest": {"time": "07:45"},
            "secrets": {"openai_api_key": "op://Personal/item/openai_api_key"},
        }
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = telegram_digest.cmd_cron_line(object())
        finally:
            telegram_digest.history_client.load_runtime_config = original_load

        output = buffer.getvalue().strip()
        self.assertEqual(exit_code, 0)
        self.assertIn("45 7 * * *", output)
        self.assertIn("telegram_digest.py run", output)
        self.assertIn(">> data/launchd/digest.cron.log 2>&1", output)
        self.assertIn("# telegram_connector_daily_digest", output)

    def test_cmd_run_sends_per_channel_messages_and_final_error_only_when_needed(self) -> None:
        original_resolve_runtime = telegram_digest.history_client.resolve_runtime
        original_load_runtime_config = telegram_digest.history_client.load_runtime_config
        original_connect_db = telegram_digest.history_client.connect_db
        original_resolve_channels_argument = telegram_digest.history_client.resolve_channels_argument
        original_require_openai_api_key = telegram_digest.require_openai_api_key
        original_run_sync = telegram_digest.run_sync
        original_iter_channel_messages = telegram_digest.iter_channel_messages
        original_render_message_block = telegram_digest.render_message_block
        original_summarize_channel_batches = telegram_digest.summarize_channel_batches
        original_require_token = telegram_digest.bridge.require_token
        original_send_text_chunks = telegram_digest.bridge.send_text_chunks
        try:
            telegram_digest.history_client.resolve_runtime = lambda: type("Runtime", (), {"default_auth_mode": "user"})()
            telegram_digest.history_client.load_runtime_config = lambda: {
                "telegram": {"default_chat_id": "1"},
                "processing": {"model": "test-model"},
                "digest_limits": {
                    "day": {"sync_limit": "6100", "ai_batch_size": "111"},
                },
            }
            telegram_digest.history_client.connect_db = lambda runtime: sqlite3.connect(":memory:")
            original_init_db = telegram_digest.history_client.init_db
            telegram_digest.history_client.init_db = lambda conn: conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_usage_log (
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
                    request_index INTEGER,
                    message_count INTEGER,
                    system_chars INTEGER,
                    prompt_chars INTEGER,
                    shared_prefix_chars INTEGER,
                    shared_prefix_hash TEXT,
                    input_tokens INTEGER,
                    cached_input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    latency_ms INTEGER,
                    status TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            telegram_digest.history_client.resolve_channels_argument = lambda runtime, channel: ["@a", "@b"]
            telegram_digest.require_openai_api_key = lambda config: "k"

            async def fake_run_sync(runtime, **kwargs):
                return [{"channel": "@a"}, {"channel": "@b"}]

            telegram_digest.run_sync = fake_run_sync
            telegram_digest.iter_channel_messages = lambda conn, channel, since, until, max_messages=None: iter(
                [{"title": channel, "username": channel.lstrip("@"), "message_id": 1, "date_utc": since, "sender_username": "u", "sender_display_name": "User", "forwards": 0, "replies": 0, "text": "x", "ocr_text": None}]
            )

            def fake_render_message_block(messages, max_chars):
                return telegram_digest.ChannelDigestInput(
                    channel_name="Channel A" if max_chars == 1000 else "unused",
                    message_count=1,
                    message_block="block",
                )

            telegram_digest.render_message_block = fake_render_message_block

            def fake_summarize_channel_batches(conn, **kwargs):
                if kwargs["channel"] == "@b":
                    raise RuntimeError("boom")
                return (1, "summary ok")

            telegram_digest.summarize_channel_batches = fake_summarize_channel_batches
            telegram_digest.bridge.require_token = lambda: "token"
            sent: list[str] = []
            telegram_digest.bridge.send_text_chunks = lambda token, chat_id, message: sent.append(message)

            args = type("Args", (), {"channel": None, "since": None, "until": None, "auth_mode": None})()
            exit_code = telegram_digest.cmd_run(args)
        finally:
            telegram_digest.history_client.resolve_runtime = original_resolve_runtime
            telegram_digest.history_client.load_runtime_config = original_load_runtime_config
            telegram_digest.history_client.connect_db = original_connect_db
            telegram_digest.history_client.init_db = original_init_db
            telegram_digest.history_client.resolve_channels_argument = original_resolve_channels_argument
            telegram_digest.require_openai_api_key = original_require_openai_api_key
            telegram_digest.run_sync = original_run_sync
            telegram_digest.iter_channel_messages = original_iter_channel_messages
            telegram_digest.render_message_block = original_render_message_block
            telegram_digest.summarize_channel_batches = original_summarize_channel_batches
            telegram_digest.bridge.require_token = original_require_token
            telegram_digest.bridge.send_text_chunks = original_send_text_chunks

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(sent), 2)
        self.assertIn("Channel A", sent[0])
        self.assertIn("summary ok", sent[0])
        self.assertIn("Digest completed with errors", sent[1])
        self.assertIn("analysis failed: boom", sent[1])

    def test_extract_response_text_reads_output_text(self) -> None:
        self.assertEqual(
            telegram_digest.extract_response_text({"output_text": "hello"}),
            "hello",
        )


if __name__ == "__main__":
    unittest.main()
    def test_resolve_digest_config_requires_processing_model(self) -> None:
        with self.assertRaises(SystemExit) as context:
            telegram_digest.resolve_digest_config({"processing": {}})

        self.assertIn("Missing processing.model", str(context.exception))
