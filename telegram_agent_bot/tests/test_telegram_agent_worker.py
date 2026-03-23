import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "telegram_agent_worker.py"
SPEC = importlib.util.spec_from_file_location("telegram_agent_worker_module", MODULE_PATH)
telegram_agent_worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telegram_agent_worker)


class TelegramAgentWorkerTests(unittest.TestCase):
    def test_parse_allowed_roots_defaults_to_project_root(self) -> None:
        roots = telegram_agent_worker.parse_allowed_roots({})
        self.assertEqual(roots, [telegram_agent_worker.BASE_DIR.resolve()])

    def test_parse_allowed_roots_supports_relative_and_absolute_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = {"agent": {"allowed_roots": ["tests", tmp_dir]}}
            roots = telegram_agent_worker.parse_allowed_roots(config)
        self.assertIn((telegram_agent_worker.BASE_DIR / "tests").resolve(), roots)
        self.assertIn(Path(tmp_dir).resolve(), roots)

    def test_resolve_user_path_rejects_paths_outside_allowed_roots(self) -> None:
        allowed_roots = [(telegram_agent_worker.BASE_DIR / "tests").resolve()]
        with self.assertRaises(ValueError):
            telegram_agent_worker.resolve_user_path("../README.md", allowed_roots)

    def test_list_local_files_reads_allowed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            result = telegram_agent_worker.list_local_files(str(root), [root], limit=10)
        self.assertEqual(result["path"], str(root.resolve()))
        self.assertEqual(result["entries"][0]["name"], "a.txt")

    def test_read_local_file_returns_numbered_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            file_path = root / "note.txt"
            file_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            result = telegram_agent_worker.read_local_file(str(file_path), [root], start_line=2, max_lines=2)
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertEqual(result["content"], "2: two\n3: three")

    def test_state_round_trip_and_reset(self) -> None:
        original_state_file = telegram_agent_worker.STATE_FILE
        with tempfile.TemporaryDirectory() as tmp_dir:
            telegram_agent_worker.STATE_FILE = Path(tmp_dir) / "agent_sessions.local.json"
            telegram_agent_worker.set_previous_response_id("42", "resp_123", "alice")
            self.assertEqual(telegram_agent_worker.get_previous_response_id("42"), "resp_123")
            telegram_agent_worker.reset_chat_state("42")
            self.assertEqual(telegram_agent_worker.get_previous_response_id("42"), "")
        telegram_agent_worker.STATE_FILE = original_state_file

    def test_extract_output_text_reads_message_content(self) -> None:
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "line 1"},
                        {"type": "output_text", "text": "line 2"},
                    ],
                }
            ]
        }
        self.assertEqual(telegram_agent_worker.extract_output_text(response), "line 1\n\nline 2")

    def test_execute_tool_rejects_outside_path(self) -> None:
        runtime = {
            "model": "gpt-5.4-mini",
            "openai_api_key": "key",
            "system_instructions": "test",
            "max_tool_rounds": 4,
            "web_search_limit": 5,
            "fetch_char_limit": 12000,
            "prompt_cache_scope": "global",
            "allowed_roots": [(telegram_agent_worker.BASE_DIR / "tests").resolve()],
        }
        with self.assertRaises(ValueError):
            telegram_agent_worker.execute_tool(
                {
                    "name": "read_local_file",
                    "call_id": "call_1",
                    "arguments": json.dumps({"path": "/etc/passwd"}),
                },
                runtime,
            )

    def test_build_prompt_cache_key_global_is_stable_for_same_roots(self) -> None:
        roots = [Path("/tmp/a"), Path("/tmp/b")]
        left = telegram_agent_worker.build_prompt_cache_key(
            model="gpt-5.4-mini",
            scope="global",
            chat_id="111",
            allowed_roots=roots,
        )
        right = telegram_agent_worker.build_prompt_cache_key(
            model="gpt-5.4-mini",
            scope="global",
            chat_id="222",
            allowed_roots=roots,
        )
        self.assertEqual(left, right)
        self.assertTrue(left.startswith("agent:"))

    def test_build_prompt_cache_key_chat_depends_on_chat_id(self) -> None:
        roots = [Path("/tmp/a")]
        left = telegram_agent_worker.build_prompt_cache_key(
            model="gpt-5.4-mini",
            scope="chat",
            chat_id="111",
            allowed_roots=roots,
        )
        right = telegram_agent_worker.build_prompt_cache_key(
            model="gpt-5.4-mini",
            scope="chat",
            chat_id="222",
            allowed_roots=roots,
        )
        self.assertNotEqual(left, right)

    def test_build_round_prompt_text_uses_stable_sorted_json_after_tool_calls(self) -> None:
        prompt_text, shared_prefix, message_count = telegram_agent_worker.build_round_prompt_text(
            round_index=2,
            prompt="ignored",
            username="alice",
            allowed_roots=[Path("/tmp")],
            current_input=[{"call_id": "1", "type": "function_call_output", "output": "{\"b\":1,\"a\":2}"}],
        )
        self.assertEqual(shared_prefix, "function_call_output")
        self.assertEqual(message_count, 1)
        self.assertEqual(
            prompt_text,
            '[{"call_id": "1", "output": "{\\"b\\":1,\\"a\\":2}", "type": "function_call_output"}]',
        )

    def test_build_round_log_text_does_not_store_raw_prompt_or_output(self) -> None:
        prompt_log_text = telegram_agent_worker.build_round_log_text(
            round_index=1,
            prompt="secret prompt contents",
            username="alice",
            allowed_roots=[Path("/tmp/a"), Path("/tmp/b")],
            current_input=[],
        )
        self.assertNotIn("secret prompt contents", prompt_log_text)
        self.assertIn("prompt_hash=", prompt_log_text)
        tool_log_text = telegram_agent_worker.build_round_log_text(
            round_index=2,
            prompt="ignored",
            username="alice",
            allowed_roots=[Path("/tmp")],
            current_input=[{"type": "function_call_output", "output": "{\"secret\":\"value\"}"}],
        )
        self.assertNotIn("\"secret\":\"value\"", tool_log_text)
        self.assertIn("item_1_output_hash=", tool_log_text)

    def test_extract_usage_reads_cached_tokens(self) -> None:
        usage = telegram_agent_worker.extract_usage(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "total_tokens": 125,
                    "input_tokens_details": {"cached_tokens": 60},
                },
                "_latency_ms": 345,
            }
        )
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.cached_input_tokens, 60)
        self.assertEqual(usage.output_tokens, 25)
        self.assertEqual(usage.total_tokens, 125)
        self.assertEqual(usage.latency_ms, 345)

    def test_log_openai_usage_writes_previous_prompt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_db_file = telegram_agent_worker.DB_FILE
            telegram_agent_worker.DB_FILE = Path(tmp_dir) / "telegram_agent.sqlite3"
            conn = telegram_agent_worker.connect_db()
            try:
                telegram_agent_worker.init_db(conn)
                cache_info_1 = telegram_agent_worker.build_prompt_cache_info(
                    model="gpt-5.4-mini",
                    cache_key="agent:test-cache",
                    system_instructions="sys",
                    prompt_text="prefix\n\nfirst",
                    shared_prefix="prefix",
                )
                telegram_agent_worker.log_openai_usage(
                    conn,
                    stage="round_1",
                    channel="1337",
                    model="gpt-5.4-mini",
                    request_index=1,
                    message_count=1,
                    status="ok",
                    cache_info=cache_info_1,
                    prompt_text="prefix\n\nfirst",
                    usage=telegram_agent_worker.OpenAIUsage(10, 4, 3, 13, 200),
                    response_id="resp_1",
                )
                cache_info_2 = telegram_agent_worker.build_prompt_cache_info(
                    model="gpt-5.4-mini",
                    cache_key="agent:test-cache",
                    system_instructions="sys",
                    prompt_text="prefix\n\nfirst plus more",
                    shared_prefix="prefix",
                )
                telegram_agent_worker.log_openai_usage(
                    conn,
                    stage="round_2",
                    channel="1337",
                    model="gpt-5.4-mini",
                    request_index=2,
                    message_count=1,
                    status="ok",
                    cache_info=cache_info_2,
                    prompt_text="prefix\n\nfirst plus more",
                    usage=telegram_agent_worker.OpenAIUsage(12, 8, 5, 17, 250),
                    response_id="resp_2",
                )
                row = conn.execute(
                    """
                    SELECT previous_response_id, previous_prompt_hash, prefix_match_chars_with_previous,
                           cached_input_tokens, prompt_cache_key, feature
                    FROM ai_usage_log
                    WHERE response_id = 'resp_2'
                    """
                ).fetchone()
            finally:
                conn.close()
                telegram_agent_worker.DB_FILE = original_db_file
        assert row is not None
        self.assertEqual(row["previous_response_id"], "resp_1")
        self.assertIsNotNone(row["previous_prompt_hash"])
        self.assertGreater(row["prefix_match_chars_with_previous"], 0)
        self.assertEqual(row["cached_input_tokens"], 8)
        self.assertEqual(row["prompt_cache_key"], "agent:test-cache")
        self.assertEqual(row["feature"], "agent")

    def test_validate_public_http_url_rejects_localhost(self) -> None:
        with self.assertRaises(ValueError):
            telegram_agent_worker.validate_public_http_url("http://127.0.0.1:8000/test")

    def test_validate_public_http_url_accepts_public_ip(self) -> None:
        parsed = telegram_agent_worker.validate_public_http_url("https://1.1.1.1/test")
        self.assertEqual(parsed.hostname, "1.1.1.1")


if __name__ == "__main__":
    unittest.main()
