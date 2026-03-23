import importlib.util
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "telegram_agent_bridge.py"
SPEC = importlib.util.spec_from_file_location("telegram_agent_bridge_module", MODULE_PATH)
telegram_agent_bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telegram_agent_bridge)


class TelegramAgentBridgeTests(unittest.TestCase):
    def test_build_worker_command_for_agent(self) -> None:
        command = telegram_agent_bridge.build_worker_command("/agent найди OCR в проекте")
        self.assertEqual(
            command[1:],
            [str(telegram_agent_bridge.WORKER_FILE), "run", "--prompt", "найди OCR в проекте"],
        )

    def test_build_worker_command_for_reset(self) -> None:
        command = telegram_agent_bridge.build_worker_command("/reset")
        self.assertEqual(command[1:], [str(telegram_agent_bridge.WORKER_FILE), "reset"])

    def test_build_worker_command_rejects_empty_agent_prompt(self) -> None:
        with self.assertRaises(ValueError):
            telegram_agent_bridge.build_worker_command("/agent")

    def test_build_worker_command_uses_local_stats_handler(self) -> None:
        self.assertIsNone(telegram_agent_bridge.build_worker_command("/agent-stats"))

    def test_normalize_bridge_command_text_supports_bare_agent_command(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.normalize_bridge_command_text("agent проверь README"),
            "/agent проверь README",
        )

    def test_normalize_bridge_command_text_supports_bare_reset_command(self) -> None:
        self.assertEqual(telegram_agent_bridge.normalize_bridge_command_text("reset"), "/reset")

    def test_normalize_bridge_command_text_uses_configured_default_command(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.normalize_bridge_command_text(
                "проверь README",
                {"bridge": {"default_command": "agent"}},
            ),
            "/agent проверь README",
        )

    def test_normalize_bridge_command_text_keeps_plain_text_when_default_command_disabled(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.normalize_bridge_command_text(
                "проверь README",
                {"bridge": {"default_command": ""}},
            ),
            "проверь README",
        )

    def test_normalize_bridge_command_text_strips_bot_suffix(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.normalize_bridge_command_text("/agent@vasiliy_the_best_bot test"),
            "/agent test",
        )

    def test_parse_allowed_chat_ids_falls_back_to_default_chat(self) -> None:
        config = {"telegram": {"default_chat_id": "133126275"}}
        self.assertEqual(telegram_agent_bridge.parse_allowed_chat_ids(config), {"133126275"})

    def test_parse_allowed_usernames_normalizes_values(self) -> None:
        config = {"bridge": {"allowed_usernames": "@Andrej, codex_user"}}
        self.assertEqual(telegram_agent_bridge.parse_allowed_usernames(config), {"andrej", "codex_user"})

    def test_parse_allowed_usernames_supports_onepassword_reference(self) -> None:
        original_run = telegram_agent_bridge.subprocess.run
        telegram_agent_bridge._SECRET_CACHE.clear()

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="@Andrej, codex_user\n", stderr="")

        telegram_agent_bridge.subprocess.run = fake_run
        try:
            config = {"bridge": {"allowed_usernames": "op://Personal/telegram-connector/allowed_users"}}
            result = telegram_agent_bridge.parse_allowed_usernames(config)
        finally:
            telegram_agent_bridge.subprocess.run = original_run

        self.assertEqual(result, {"andrej", "codex_user"})

    def test_resolve_bridge_runtime_resolves_secrets_once_into_runtime_bundle(self) -> None:
        original_run = telegram_agent_bridge.subprocess.run
        telegram_agent_bridge._SECRET_CACHE.clear()

        def fake_run(*args, **kwargs):
            reference = args[0][-1]
            values = {
                "op://Personal/telegram-connector/bot_token_vasiliy": "bot-token",
                "op://Personal/telegram-connector/allowed_users": "@zabaev",
                "op://Personal/OPENAI API key/credential": "openai-key",
            }
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=f"{values[reference]}\n", stderr="")

        telegram_agent_bridge.subprocess.run = fake_run
        try:
            runtime = telegram_agent_bridge.resolve_bridge_runtime(
                {
                    "secrets": {
                        "bot_token": "op://Personal/telegram-connector/bot_token_vasiliy",
                        "openai_api_key": "op://Personal/OPENAI API key/credential",
                    },
                    "bridge": {
                        "allowed_chat_ids": "133126275",
                        "allowed_usernames": "op://Personal/telegram-connector/allowed_users",
                        "default_command": "agent",
                        "text_chunk_size": "3900",
                    },
                },
                include_worker_secrets=True,
            )
        finally:
            telegram_agent_bridge.subprocess.run = original_run

        self.assertEqual(runtime.bot_token, "bot-token")
        self.assertEqual(runtime.allowed_chat_ids, {"133126275"})
        self.assertEqual(runtime.allowed_usernames, {"zabaev"})
        self.assertEqual(runtime.worker_secret_env, {"OPENAI_API_KEY": "openai-key"})
        self.assertEqual(runtime.default_command, "agent")
        self.assertEqual(runtime.text_chunk_size, 3900)
        self.assertEqual(runtime.agent_stats_row_limit, 200)

    def test_resolve_text_chunk_size_reads_bridge_config(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.resolve_text_chunk_size({"bridge": {"text_chunk_size": "3900"}}),
            3900,
        )

    def test_resolve_agent_stats_row_limit_reads_bridge_config(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.resolve_agent_stats_row_limit({"bridge": {"agent_stats_row_limit": "150"}}),
            150,
        )

    def test_resolve_default_command_reads_bridge_config(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.resolve_default_command({"bridge": {"default_command": "agent"}}),
            "agent",
        )

    def test_format_telegram_html_escapes_and_normalizes_text(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.format_telegram_html("a < b\n\n\n- item"),
            "a &lt; b\n\n• item",
        )

    def test_format_telegram_html_formats_headings_and_commands(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.format_telegram_html("Bot commands:\n/agent test"),
            "<b>Bot commands:</b>\n<code>/agent test</code>",
        )

    def test_format_inline_telegram_html_supports_simple_bold_and_code(self) -> None:
        self.assertEqual(
            telegram_agent_bridge.format_inline_telegram_html("use `rg` and **README**"),
            "use <code>rg</code> and <b>README</b>",
        )

    def test_redact_update_for_storage_recognizes_agent_command(self) -> None:
        update = {
            "update_id": 4,
            "message": {
                "date": 123,
                "text": "agent проверь README",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        payload = telegram_agent_bridge.redact_update_for_storage(update)
        self.assertEqual(payload["command"], "/agent")
        self.assertEqual(payload["command_text"], "/agent проверь README")

    def test_build_worker_subprocess_env_whitelists_parent_env(self) -> None:
        original_project_root = os.environ.get("TELEGRAM_AGENT_BOT_PROJECT_ROOT")
        os.environ["TELEGRAM_AGENT_BOT_PROJECT_ROOT"] = "/tmp/project-root"
        os.environ["PATH"] = "/usr/bin"
        os.environ["SECRET_NOISE"] = "should_not_leak"
        try:
            env = telegram_agent_bridge.build_worker_subprocess_env({"OPENAI_API_KEY": "key"})
        finally:
            if original_project_root is None:
                os.environ.pop("TELEGRAM_AGENT_BOT_PROJECT_ROOT", None)
            else:
                os.environ["TELEGRAM_AGENT_BOT_PROJECT_ROOT"] = original_project_root
        self.assertEqual(env["OPENAI_API_KEY"], "key")
        self.assertEqual(env["TELEGRAM_AGENT_BOT_PROJECT_ROOT"], "/tmp/project-root")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertNotIn("SECRET_NOISE", env)

    def test_handle_agent_command_passes_chat_context_to_subprocess(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "date": 123,
                "text": "/agent проверь README",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        runtime = telegram_agent_bridge.BridgeRuntime(
            bot_token="bot-token",
            worker_secret_env={"OPENAI_API_KEY": "key"},
            allowed_chat_ids={"42"},
            allowed_user_ids={"7"},
            allowed_usernames=set(),
            text_chunk_size=3900,
            agent_stats_row_limit=200,
            default_command="agent",
        )
        original_run = telegram_agent_bridge.subprocess.run
        original_send = telegram_agent_bridge.send_text_chunks
        captured: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            captured["argv"] = args[0]
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout='{"status":"ok","reply_text":"done"}',
                stderr="",
            )

        telegram_agent_bridge.subprocess.run = fake_run
        telegram_agent_bridge.send_text_chunks = lambda token, chat_id, text, chunk_size=3500: captured.update({"reply": text})
        try:
            telegram_agent_bridge.handle_agent_command(runtime, update)
        finally:
            telegram_agent_bridge.subprocess.run = original_run
            telegram_agent_bridge.send_text_chunks = original_send

        argv = captured["argv"]
        assert isinstance(argv, list)
        self.assertIn("--chat-id", argv)
        self.assertIn("42", argv)
        self.assertIn("--username", argv)
        self.assertIn("alice", argv)
        self.assertEqual(captured["reply"], "done")

    def test_send_text_message_uses_html_parse_mode(self) -> None:
        captured: dict[str, object] = {}
        original_api_call = telegram_agent_bridge.api_call

        def fake_api_call(token: str, method: str, payload: dict[str, object] | None = None) -> object:
            captured["token"] = token
            captured["method"] = method
            captured["payload"] = payload or {}
            return {}

        telegram_agent_bridge.api_call = fake_api_call
        try:
            telegram_agent_bridge.send_text_message("token", 42, "a < b")
        finally:
            telegram_agent_bridge.api_call = original_api_call

        self.assertEqual(captured["method"], "sendMessage")
        payload = captured["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["disable_web_page_preview"], True)
        self.assertEqual(payload["text"], "a &lt; b")

    def test_handle_agent_command_rejects_unknown_user(self) -> None:
        update = {
            "update_id": 2,
            "message": {
                "date": 123,
                "text": "/agent проверь README",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 99, "username": "mallory"},
            },
        }
        runtime = telegram_agent_bridge.BridgeRuntime(
            bot_token="bot-token",
            worker_secret_env={"OPENAI_API_KEY": "key"},
            allowed_chat_ids={"42"},
            allowed_user_ids={"7"},
            allowed_usernames=set(),
            text_chunk_size=3900,
            agent_stats_row_limit=200,
            default_command="agent",
        )
        captured: dict[str, object] = {}
        original_send = telegram_agent_bridge.send_text_message
        try:
            telegram_agent_bridge.send_text_message = lambda token, chat_id, text: captured.update({"chat_id": chat_id, "text": text})
            telegram_agent_bridge.handle_agent_command(runtime, update)
        finally:
            telegram_agent_bridge.send_text_message = original_send
        self.assertEqual(captured["chat_id"], 42)
        self.assertIn("not allowed", str(captured["text"]))

    def test_fetch_agent_usage_summary_reads_global_and_chat_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_db_file = telegram_agent_bridge.AGENT_DB_FILE
            telegram_agent_bridge.AGENT_DB_FILE = Path(tmp_dir) / "telegram_agent.sqlite3"
            conn = sqlite3.connect(telegram_agent_bridge.AGENT_DB_FILE)
            try:
                conn.executescript(
                    """
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
                        output_tokens INTEGER,
                        total_tokens INTEGER,
                        latency_ms INTEGER,
                        status TEXT NOT NULL,
                        error TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ai_usage_log (
                        created_at, feature, stage, channel, model, response_id,
                        prompt_cache_key, request_index, message_count, prompt_text,
                        input_tokens, cached_input_tokens, output_tokens, total_tokens, latency_ms, status
                    )
                    VALUES
                    ('2026-03-23T10:00:00+00:00', 'agent', 'round_1', '42', 'gpt-5.4-mini', 'resp_1',
                     'agent:a', 1, 1, 'p1', 100, 40, 20, 120, 200, 'ok'),
                    ('2026-03-23T10:05:00+00:00', 'agent', 'round_2', '99', 'gpt-5.4-mini', 'resp_2',
                     'agent:b', 2, 1, 'p2', 60, 0, 10, 70, 210, 'error')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            try:
                summary = telegram_agent_bridge.fetch_agent_usage_summary(chat_id="42", row_limit=50)
            finally:
                telegram_agent_bridge.AGENT_DB_FILE = original_db_file
        assert summary is not None
        self.assertEqual(summary["global"]["total_requests"], 2)
        self.assertEqual(summary["global"]["cached_input_tokens"], 40)
        self.assertEqual(summary["chat"]["total_requests"], 1)
        self.assertEqual(summary["chat"]["cached_input_tokens"], 40)
        self.assertEqual(summary["row_limit"], 50)
        self.assertEqual(len(summary["recent_rows"]), 2)

    def test_fetch_agent_usage_summary_respects_recent_window_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_db_file = telegram_agent_bridge.AGENT_DB_FILE
            telegram_agent_bridge.AGENT_DB_FILE = Path(tmp_dir) / "telegram_agent.sqlite3"
            conn = sqlite3.connect(telegram_agent_bridge.AGENT_DB_FILE)
            try:
                conn.executescript(
                    """
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
                        output_tokens INTEGER,
                        total_tokens INTEGER,
                        latency_ms INTEGER,
                        status TEXT NOT NULL,
                        error TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ai_usage_log (
                        created_at, feature, stage, channel, model, response_id,
                        prompt_cache_key, request_index, message_count, prompt_text,
                        input_tokens, cached_input_tokens, output_tokens, total_tokens, latency_ms, status
                    )
                    VALUES
                    ('2026-03-23T10:00:00+00:00', 'agent', 'round_1', '42', 'gpt-5.4-mini', 'resp_1', 'agent:a', 1, 1, 'p1', 100, 50, 20, 120, 200, 'ok'),
                    ('2026-03-23T10:05:00+00:00', 'agent', 'round_2', '42', 'gpt-5.4-mini', 'resp_2', 'agent:a', 2, 1, 'p2', 90, 10, 18, 108, 210, 'ok'),
                    ('2026-03-23T10:10:00+00:00', 'agent', 'round_3', '42', 'gpt-5.4-mini', 'resp_3', 'agent:a', 3, 1, 'p3', 80, 0, 16, 96, 220, 'ok')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            try:
                summary = telegram_agent_bridge.fetch_agent_usage_summary(chat_id="42", row_limit=2)
            finally:
                telegram_agent_bridge.AGENT_DB_FILE = original_db_file
        assert summary is not None
        self.assertEqual(summary["global"]["total_requests"], 2)
        self.assertEqual(summary["global"]["input_tokens"], 170)
        self.assertEqual(summary["global"]["cached_input_tokens"], 10)

    def test_format_agent_usage_summary_includes_cached_share(self) -> None:
        text = telegram_agent_bridge.format_agent_usage_summary(
            {
                "global": {
                    "total_requests": 3,
                    "ok_requests": 2,
                    "error_requests": 1,
                    "cached_requests": 2,
                    "cache_keys": 1,
                    "first_request_at": "2026-03-23T10:00:00+00:00",
                    "last_request_at": "2026-03-23T10:10:00+00:00",
                    "input_tokens": 200,
                    "cached_input_tokens": 50,
                    "output_tokens": 40,
                },
                "chat": {
                    "total_requests": 2,
                    "input_tokens": 80,
                    "cached_input_tokens": 20,
                },
                "recent_rows": [
                    {
                        "stage": "round_2",
                        "status": "ok",
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 15,
                    }
                ],
                "row_limit": 200,
            },
            chat_id="42",
        )
        self.assertIn("cached input tokens: 50", text)
        self.assertIn("cached share of input tokens: 25.0%", text)
        self.assertIn("This chat (42):", text)
        self.assertIn("analysis window: latest 200 requests", text)
        self.assertIn("Latest rounds:", text)
        self.assertIn("round_2: ok, input=100, cached=25 (25.0%), output=15", text)

    def test_handle_agent_command_serves_agent_stats_without_worker(self) -> None:
        update = {
            "update_id": 3,
            "message": {
                "date": 123,
                "text": "/agent-stats",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        runtime = telegram_agent_bridge.BridgeRuntime(
            bot_token="bot-token",
            worker_secret_env={"OPENAI_API_KEY": "key"},
            allowed_chat_ids={"42"},
            allowed_user_ids={"7"},
            allowed_usernames=set(),
            text_chunk_size=3900,
            agent_stats_row_limit=200,
            default_command="agent",
        )
        original_summary = telegram_agent_bridge.fetch_agent_usage_summary
        original_send_chunks = telegram_agent_bridge.send_text_chunks
        original_run = telegram_agent_bridge.subprocess.run
        captured: dict[str, object] = {}

        telegram_agent_bridge.fetch_agent_usage_summary = lambda chat_id, row_limit: {
            "global": {
                "total_requests": 1,
                "ok_requests": 1,
                "error_requests": 0,
                "cached_requests": 1,
                "cache_keys": 1,
                "first_request_at": "2026-03-23T10:00:00+00:00",
                "last_request_at": "2026-03-23T10:00:00+00:00",
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "output_tokens": 15,
            },
            "chat": {"total_requests": 1, "input_tokens": 100, "cached_input_tokens": 20},
            "recent_rows": [
                {"stage": "round_1", "status": "ok", "input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 15}
            ],
            "row_limit": row_limit,
        }
        telegram_agent_bridge.send_text_chunks = (
            lambda token, chat_id, text, chunk_size=3500: captured.update({"chat_id": chat_id, "text": text})
        )

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for /agent-stats")

        telegram_agent_bridge.subprocess.run = fail_run
        try:
            telegram_agent_bridge.handle_agent_command(runtime, update)
        finally:
            telegram_agent_bridge.fetch_agent_usage_summary = original_summary
            telegram_agent_bridge.send_text_chunks = original_send_chunks
            telegram_agent_bridge.subprocess.run = original_run
        self.assertEqual(captured["chat_id"], 42)
        self.assertIn("Agent stats:", str(captured["text"]))


if __name__ == "__main__":
    unittest.main()
