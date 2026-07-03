import importlib.util
import io
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "telegram_bridge.py"
SPEC = importlib.util.spec_from_file_location("telegram_bridge_module", MODULE_PATH)
telegram_connector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telegram_connector)


def load_bridge_module_with_env(**env: str):
    spec = importlib.util.spec_from_file_location("telegram_bridge_module_env", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    original_env = {key: os.environ.get(key) for key in env}
    try:
        for key, value in env.items():
            os.environ[key] = value
        spec.loader.exec_module(module)
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return module


class TelegramConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_load_runtime_config = telegram_connector.load_runtime_config
        telegram_connector.load_runtime_config = lambda: {
            "sync": {
                "backfill_limit": "100",
                "tail_limit": "100",
                "update_limit": "100",
            }
        }

    def tearDown(self) -> None:
        telegram_connector.load_runtime_config = self._original_load_runtime_config

    def test_runtime_paths_stay_relative_to_app_dir_when_project_root_env_is_set(self) -> None:
        module = load_bridge_module_with_env(
            TELEGRAM_CONNECTOR_PROJECT_ROOT="/tmp/fake-project-root"
        )

        self.assertEqual(module.BASE_DIR, module.APP_DIR)
        self.assertEqual(module.DATA_DIR, module.APP_DIR / "data")
        self.assertEqual(module.RUNTIME_LOCAL_FILE, module.APP_DIR / "config" / "runtime.local.toml")

    def test_build_history_command_for_backfill_with_media(self) -> None:
        command = telegram_connector.build_history_command("/backfill @vcnews 200 media")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "backfill", "--channel", "@vcnews", "--limit", "200", "--download-media", "--auth-mode", "user"],
        )

    def test_build_history_command_for_backfill_with_period(self) -> None:
        command = telegram_connector.build_history_command("/backfill @vcnews since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "backfill", "--channel", "@vcnews", "--limit", "100", "--since", "2026-03-15", "--until", "2026-03-16", "--auth-mode", "user"],
        )

    def test_build_history_command_for_backfill_with_zero_limit(self) -> None:
        command = telegram_connector.build_history_command("/backfill @vcnews 0")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "backfill", "--channel", "@vcnews", "--limit", "0", "--auth-mode", "user"],
        )

    def test_build_history_command_for_tail_uses_default_limit(self) -> None:
        command = telegram_connector.build_history_command("/tail @vcnews")
        self.assertEqual(command[2:], ["sync", "--mode", "tail", "--channel", "@vcnews", "--limit", "100", "--auth-mode", "user"])

    def test_build_history_command_for_tail_with_explicit_auth_mode(self) -> None:
        command = telegram_connector.build_history_command("/tail @vcnews 100 media ocr bot")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "tail", "--channel", "@vcnews", "--limit", "100", "--download-media", "--ocr", "--auth-mode", "bot"],
        )

    def test_build_history_command_for_tail_with_since_only(self) -> None:
        command = telegram_connector.build_history_command("/tail @vcnews since=2026-03-15")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "tail", "--channel", "@vcnews", "--limit", "100", "--since", "2026-03-15", "--auth-mode", "user"],
        )

    def test_build_history_command_for_ocrhistory(self) -> None:
        command = telegram_connector.build_history_command("/ocrhistory @vcnews 50 user")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "tail", "--channel", "@vcnews", "--limit", "50", "--download-media", "--ocr", "--auth-mode", "user"],
        )

    def test_build_history_command_for_ocrhistory_with_period(self) -> None:
        command = telegram_connector.build_history_command("/ocrhistory @vcnews since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "tail", "--channel", "@vcnews", "--limit", "100", "--download-media", "--ocr", "--since", "2026-03-15", "--until", "2026-03-16", "--auth-mode", "user"],
        )

    def test_build_history_command_for_digest_uses_config_defaults_when_omitted(self) -> None:
        command = telegram_connector.build_history_command("/digest")
        self.assertEqual(command[1:], [str(telegram_connector.DIGEST_FILE), "run", "--auth-mode", "user"])

    def test_build_history_command_for_digest_with_overrides(self) -> None:
        command = telegram_connector.build_history_command("/digest @vcnews since=2026-03-15 until=2026-03-16 bot")
        self.assertEqual(
            command[1:],
            [
                str(telegram_connector.DIGEST_FILE),
                "run",
                "--channel",
                "@vcnews",
                "--since",
                "2026-03-15",
                "--until",
                "2026-03-16",
                "--auth-mode",
                "bot",
            ],
        )

    def test_build_history_command_for_digest_with_spaced_multi_channel_list(self) -> None:
        command = telegram_connector.build_history_command("/digest @vcnews, @refugecard since=2026-03-15")
        self.assertEqual(
            command[1:],
            [
                str(telegram_connector.DIGEST_FILE),
                "run",
                "--channel",
                "@vcnews, @refugecard",
                "--since",
                "2026-03-15",
                "--auth-mode",
                "user",
            ],
        )

    def test_build_history_command_for_digest_with_single_date_token_shortcut(self) -> None:
        command = telegram_connector.build_history_command("/digest -3d")
        self.assertEqual(
            command[1:],
            [
                str(telegram_connector.DIGEST_FILE),
                "run",
                "--since=-3d",
                "--until=-3d",
                "--auth-mode",
                "user",
            ],
        )

    def test_build_history_command_for_exportcsv_supports_negative_day_token(self) -> None:
        command = telegram_connector.build_history_command("/exportcsv @vcnews since=-4d until=-4d")
        self.assertEqual(
            command[2:],
            ["export-csv", "--channel", "@vcnews", "--since=-4d", "--until=-4d", "--auth-mode", "user"],
        )

    def test_resolve_send_message_retry_settings_read_bridge_config(self) -> None:
        config = {
            "bridge": {
                "send_message_retry_attempts": "4",
                "send_message_retry_backoff_seconds": "7",
            }
        }

        self.assertEqual(telegram_connector.resolve_send_message_retry_attempts(config), 4)
        self.assertEqual(telegram_connector.resolve_send_message_retry_backoff_seconds(config), 7)

    def test_resolve_top_models_default_limit_reads_bridge_config(self) -> None:
        self.assertEqual(
            telegram_connector.resolve_top_models_default_limit({"bridge": {"top_models_default_limit": "7"}}),
            7,
        )

    def test_resolve_top_models_retry_attempts_reads_bridge_config(self) -> None:
        self.assertEqual(
            telegram_connector.resolve_top_models_retry_attempts({"bridge": {"top_models_retry_attempts": "4"}}),
            4,
        )

    def test_resolve_export_csv_default_limit_reads_export_config(self) -> None:
        self.assertEqual(
            telegram_connector.resolve_export_csv_default_limit({"export": {"default_limit": "250"}}),
            250,
        )

    def test_resolve_ocr_pending_default_limit_reads_ocr_config(self) -> None:
        self.assertEqual(
            telegram_connector.resolve_ocr_pending_default_limit({"ocr": {"pending_default_limit": "250"}}),
            250,
        )

    def test_resolve_sync_mode_limit_reads_bridge_config(self) -> None:
        self.assertEqual(
            telegram_connector.resolve_sync_mode_limit({"sync": {"backfill_limit": "120"}}, "backfill"),
            "120",
        )

    def test_build_history_command_for_update_defaults_to_user(self) -> None:
        command = telegram_connector.build_history_command("/update @vcnews 25")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "update", "--channel", "@vcnews", "--limit", "25", "--auth-mode", "user"],
        )

    def test_build_history_command_for_update_with_spaced_multi_channel_list(self) -> None:
        command = telegram_connector.build_history_command("/update @vcnews, @refugecard 10")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "update", "--channel", "@vcnews, @refugecard", "--limit", "10", "--auth-mode", "user"],
        )

    def test_build_history_command_for_update_with_mark_read(self) -> None:
        command = telegram_connector.build_history_command("/update @vcnews 10 read")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "update", "--channel", "@vcnews", "--limit", "10", "--mark-read", "--auth-mode", "user"],
        )

    def test_build_history_command_for_update_with_period(self) -> None:
        command = telegram_connector.build_history_command("/update @vcnews 10 since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            ["sync", "--mode", "update", "--channel", "@vcnews", "--limit", "10", "--since", "2026-03-15", "--until", "2026-03-16", "--auth-mode", "user"],
        )

    def test_build_history_command_for_update_without_channel_uses_runtime_defaults(self) -> None:
        command = telegram_connector.build_history_command("/update 25")
        self.assertEqual(command[2:], ["sync", "--mode", "update", "--limit", "25", "--auth-mode", "user"])

    def test_normalize_bridge_command_text_supports_bare_command(self) -> None:
        self.assertEqual(telegram_connector.normalize_bridge_command_text("update 10"), "/update 10")

    def test_normalize_bridge_command_text_supports_bare_digest_command(self) -> None:
        self.assertEqual(telegram_connector.normalize_bridge_command_text("digest"), "/digest")

    def test_normalize_bridge_command_text_supports_agent_stats_command(self) -> None:
        self.assertEqual(telegram_connector.normalize_bridge_command_text("agent-stats"), "/agent-stats")

    def test_normalize_bridge_command_text_supports_top_models_command(self) -> None:
        self.assertEqual(telegram_connector.normalize_bridge_command_text("top-models"), "/top-models")

    def test_parse_top_models_request_supports_debug_flag(self) -> None:
        self.assertEqual(
            telegram_connector.parse_top_models_request("/top-models 3 debug", default_limit=5),
            (3, True),
        )

    def test_normalize_bridge_command_text_strips_bot_suffix(self) -> None:
        self.assertEqual(
            telegram_connector.normalize_bridge_command_text("/update@verter_the_bot 10"),
            "/update 10",
        )

    def test_cmd_listen_continues_after_retryable_timeout(self) -> None:
        original_resolve_bridge_secrets = telegram_connector.resolve_bridge_secrets
        original_require_bot_token_from_secrets = telegram_connector.require_bot_token_from_secrets
        original_load_offset = telegram_connector.load_offset
        original_fetch_updates = telegram_connector.fetch_updates
        try:
            telegram_connector.resolve_bridge_secrets = lambda config: {"TELEGRAM_BOT_TOKEN": "token"}
            telegram_connector.require_bot_token_from_secrets = lambda secret_env: "token"
            telegram_connector.load_offset = lambda: None
            calls = {"count": 0}

            def fake_fetch_updates(token: str, offset: int | None, timeout: int) -> list[dict[str, object]]:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise SystemExit("Telegram API request timed out while calling getUpdates.")
                raise KeyboardInterrupt()

            telegram_connector.fetch_updates = fake_fetch_updates
            args = SimpleNamespace(from_scratch=False, timeout=30, once=False, echo=False, run_commands=False)

            original_sleep = telegram_connector.time.sleep
            telegram_connector.time.sleep = lambda seconds: None
            try:
                with self.assertRaises(KeyboardInterrupt):
                    telegram_connector.cmd_listen(args)
            finally:
                telegram_connector.time.sleep = original_sleep
        finally:
            telegram_connector.resolve_bridge_secrets = original_resolve_bridge_secrets
            telegram_connector.require_bot_token_from_secrets = original_require_bot_token_from_secrets
            telegram_connector.load_offset = original_load_offset
            telegram_connector.fetch_updates = original_fetch_updates

        self.assertEqual(calls["count"], 2)

    def test_build_history_command_for_exportcsv_with_limit(self) -> None:
        command = telegram_connector.build_history_command("/exportcsv @vcnews 100")
        self.assertEqual(
            command[2:],
            ["export-csv", "--channel", "@vcnews", "--limit", "100", "--auth-mode", "user"],
        )

    def test_build_history_command_for_exportcsv_with_spaced_multi_channel_list(self) -> None:
        command = telegram_connector.build_history_command("/exportcsv @vcnews, @refugecard 100")
        self.assertEqual(
            command[2:],
            ["export-csv", "--channel", "@vcnews, @refugecard", "--limit", "100", "--auth-mode", "user"],
        )

    def test_build_history_command_for_exportcsv_with_period(self) -> None:
        command = telegram_connector.build_history_command("/exportcsv @vcnews since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            [
                "export-csv",
                "--channel",
                "@vcnews",
                "--since",
                "2026-03-15",
                "--until",
                "2026-03-16",
                "--auth-mode",
                "user",
            ],
        )

    def test_build_history_command_for_exportcsv_with_since_only(self) -> None:
        command = telegram_connector.build_history_command("/exportcsv @vcnews since=2026-03-15")
        self.assertEqual(
            command[2:],
            ["export-csv", "--channel", "@vcnews", "--since", "2026-03-15", "--auth-mode", "user"],
        )

    def test_build_history_command_for_exportcsv_without_channel_uses_runtime_defaults(self) -> None:
        command = telegram_connector.build_history_command("/exportcsv 100")
        self.assertEqual(command[2:], ["export-csv", "--limit", "100", "--auth-mode", "user"])

    def test_build_export_csv_command_uses_configured_default_limit(self) -> None:
        command = telegram_connector.build_export_csv_command(
            ["/exportcsv", "@vcnews"],
            ["python3", "telegram_history_client.py"],
            {"export": {"default_limit": "250"}},
        )
        self.assertEqual(
            command,
            [
                "python3",
                "telegram_history_client.py",
                "export-csv",
                "--channel",
                "@vcnews",
                "--limit",
                "250",
                "--auth-mode",
                "user",
            ],
        )

    def test_build_history_command_rejects_unknown_command(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/boom")

    def test_build_history_command_rejects_unsupported_digest_argument(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/digest 10")

    def test_build_history_command_rejects_unsupported_sync_argument(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/update @vcnews 10 surprise")

    def test_build_history_command_rejects_unsupported_ocr_argument(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/ocr @vcnews 50 surprise")

    def test_build_history_command_rejects_unsupported_export_argument(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/exportcsv @vcnews surprise")

    def test_build_history_command_rejects_unsupported_ocrhistory_argument(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/ocrhistory @vcnews 50 surprise")

    def test_build_history_command_for_ocr_pending_with_period_and_channel(self) -> None:
        command = telegram_connector.build_history_command("/ocr @vcnews 50 since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            ["ocr-pending", "--channel", "@vcnews", "--limit", "50", "--since", "2026-03-15", "--until", "2026-03-16"],
        )

    def test_send_text_chunks_splits_long_messages(self) -> None:
        sent_messages: list[str] = []

        def fake_send(token: str, chat_id: str | int, text: str, parse_mode: str | None = None) -> None:
            self.assertEqual(token, "token")
            self.assertEqual(chat_id, 42)
            sent_messages.append(text)

        original = telegram_connector.send_text_message
        telegram_connector.send_text_message = fake_send
        try:
            telegram_connector.send_text_chunks("token", 42, "a" * 4000, chunk_size=1000)
        finally:
            telegram_connector.send_text_message = original

        self.assertGreater(len(sent_messages), 1)
        self.assertEqual("".join(sent_messages), "a" * 4000)

    def test_send_text_message_preserves_preformatted_html_when_parse_mode_is_html(self) -> None:
        captured: dict[str, object] = {}
        original_api_call = telegram_connector.api_call

        def fake_api_call(token: str, method: str, payload: dict[str, object]) -> dict[str, object]:
            captured["token"] = token
            captured["method"] = method
            captured["payload"] = payload
            return {"ok": True}

        telegram_connector.api_call = fake_api_call
        try:
            telegram_connector.send_text_message("token", 42, "Bot commands:\n/update 10", parse_mode="HTML")
        finally:
            telegram_connector.api_call = original_api_call

        payload = captured["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(captured["token"], "token")
        self.assertEqual(captured["method"], "sendMessage")
        self.assertEqual(payload["chat_id"], "42")
        self.assertEqual(payload["text"], "Bot commands:\n/update 10")
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])

    def test_send_text_message_retries_transient_send_message_failures(self) -> None:
        original_api_call = telegram_connector.api_call
        calls: list[str] = []

        def fake_api_call(token: str, method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append(method)
            if len(calls) < 3:
                raise SystemExit("Telegram API request failed while calling sendMessage: [Errno 54] Connection reset by peer.")
            return {"ok": True}

        telegram_connector.api_call = fake_api_call
        try:
            telegram_connector.send_text_message(
                "token",
                42,
                "hello",
                retry_attempts=3,
                retry_backoff_seconds=0,
            )
        finally:
            telegram_connector.api_call = original_api_call

        self.assertEqual(calls, ["sendMessage", "sendMessage", "sendMessage"])

    def test_send_text_message_does_not_retry_non_transient_send_message_errors(self) -> None:
        original_api_call = telegram_connector.api_call
        calls: list[str] = []

        def fake_api_call(token: str, method: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append(method)
            raise SystemExit("Telegram API HTTP 400 while calling sendMessage.")

        telegram_connector.api_call = fake_api_call
        try:
            with self.assertRaises(SystemExit):
                telegram_connector.send_text_message(
                    "token",
                    42,
                    "hello",
                    retry_attempts=3,
                    retry_backoff_seconds=0,
                )
        finally:
            telegram_connector.api_call = original_api_call

        self.assertEqual(calls, ["sendMessage"])

    def test_redact_update_for_storage_removes_message_text(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "date": 123,
                "text": "/tail @vcnews 10",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        payload = telegram_connector.redact_update_for_storage(update)
        self.assertEqual(payload["command"], "/tail")
        self.assertEqual(payload["command_text"], "/tail @vcnews 10")
        self.assertEqual(payload["text_length"], len("/tail @vcnews 10"))
        self.assertNotIn("text", payload)

    def test_redact_update_for_storage_recognizes_bare_bridge_command(self) -> None:
        update = {
            "update_id": 2,
            "message": {
                "date": 123,
                "text": "update 10",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        payload = telegram_connector.redact_update_for_storage(update)
        self.assertEqual(payload["command"], "/update")
        self.assertEqual(payload["command_text"], "/update 10")

    def test_redact_update_for_storage_sanitizes_full_command_text(self) -> None:
        update = {
            "update_id": 3,
            "message": {
                "date": 123,
                "text": "/update @vcnews,\n@refugecard\t10\r\n",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        payload = telegram_connector.redact_update_for_storage(update)
        self.assertEqual(payload["command"], "/update")
        self.assertEqual(payload["command_text"], "/update @vcnews, @refugecard 10")

    def test_build_safe_command_response_hides_paths(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3"],
            returncode=0,
            stdout='{"status":"initialized","db_path":"/home/test/private.sqlite3","output_file":"out.csv"}',
            stderr="",
        )
        text, payload = telegram_connector.build_safe_command_response("init-db", completed)
        self.assertIn("status: initialized", text)
        self.assertIn("output_file: out.csv", text)
        self.assertNotIn("db_path", text)
        self.assertNotIn("/home/test/private.sqlite3", text)
        self.assertEqual(payload["output_file"], "out.csv")

    def test_build_safe_command_response_redacts_stderr(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3"],
            returncode=2,
            stdout="",
            stderr="failed at /home/tester/secrets/token.txt with bot123456:FAKE_SECRET",
        )
        text, payload = telegram_connector.build_safe_command_response("tail", completed)
        self.assertIn("Status: failed (2)", text)
        self.assertIn("<path>", text)
        self.assertIn("<bot_token>", text)
        self.assertIsNone(payload)

    def test_resolve_secret_value_reads_keychain_reference(self) -> None:
        original_run = telegram_connector.subprocess.run
        telegram_connector._SECRET_CACHE.clear()

        def fake_run(*args, **kwargs):
            self.assertEqual(
                args[0],
                ["security", "find-generic-password", "-s", "telegram-connector", "-a", "bot_token", "-w"],
            )
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="secret-from-keychain\n", stderr="")

        telegram_connector.subprocess.run = fake_run
        try:
            value = telegram_connector.resolve_secret_value("keychain://telegram-connector/bot_token", "Bot token")
        finally:
            telegram_connector.subprocess.run = original_run

        self.assertEqual(value, "secret-from-keychain")

    def test_resolve_bridge_secrets_reads_expected_env_bundle(self) -> None:
        original_run = telegram_connector.subprocess.run
        telegram_connector._SECRET_CACHE.clear()
        config = {
            "telethon": {
                "api_id": "keychain://telegram-connector/api_id",
                "phone": "keychain://telegram-connector/phone",
            },
            "secrets": {
                "api_hash": "keychain://telegram-connector/api_hash",
                "bot_token": "keychain://telegram-connector/bot_token",
                "user_password": "keychain://telegram-connector/user_password",
            },
        }
        values = {
            "telegram-connector/api_id": "1",
            "telegram-connector/phone": "+34111111111",
            "telegram-connector/api_hash": "hash",
            "telegram-connector/bot_token": "token",
            "telegram-connector/user_password": "pw",
        }

        def fake_run(*args, **kwargs):
            argv = args[0]
            service = argv[argv.index("-s") + 1]
            account = argv[argv.index("-a") + 1]
            reference = f"{service}/{account}"
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=f"{values[reference]}\n", stderr="")

        telegram_connector.subprocess.run = fake_run
        try:
            secret_env = telegram_connector.resolve_bridge_secrets(config)
        finally:
            telegram_connector.subprocess.run = original_run

        self.assertEqual(
            secret_env,
            {
                "TELEGRAM_API_ID": "1",
                "TELEGRAM_API_HASH": "hash",
                "TELEGRAM_PHONE": "+34111111111",
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_USER_PASSWORD": "pw",
            },
        )

    def test_resolve_secret_value_still_supports_legacy_onepassword_reference(self) -> None:
        original_run = telegram_connector.subprocess.run
        telegram_connector._SECRET_CACHE.clear()

        def fake_run(*args, **kwargs):
            self.assertEqual(args[0], ["op", "read", "op://Private/item/field"])
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="secret-from-op\n", stderr="")

        telegram_connector.subprocess.run = fake_run
        try:
            value = telegram_connector.resolve_secret_value("op://Private/item/field", "Bot token")
        finally:
            telegram_connector.subprocess.run = original_run

        self.assertEqual(value, "secret-from-op")

    def test_build_history_client_subprocess_env_whitelists_parent_env(self) -> None:
        original_project_root = os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT")
        os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = "/tmp/project-root"
        os.environ["PATH"] = "/usr/bin"
        os.environ["SECRET_NOISE"] = "should_not_leak"
        try:
            env = telegram_connector.build_history_client_subprocess_env(
                {"TELEGRAM_API_HASH": "hash", "TELEGRAM_BOT_TOKEN": "token"}
            )
        finally:
            if original_project_root is None:
                os.environ.pop("TELEGRAM_CONNECTOR_PROJECT_ROOT", None)
            else:
                os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = original_project_root

        self.assertEqual(env["TELEGRAM_API_HASH"], "hash")
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "token")
        self.assertEqual(env["TELEGRAM_CONNECTOR_PROJECT_ROOT"], "/tmp/project-root")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertNotIn("SECRET_NOISE", env)

    def test_api_call_converts_shared_api_error_to_system_exit(self) -> None:
        original_shared_api_call = telegram_connector.shared_api_call

        def fake_shared_api_call(*args, **kwargs):
            raise telegram_connector.TelegramApiError("Telegram API failed")

        telegram_connector.shared_api_call = fake_shared_api_call
        try:
            with self.assertRaises(SystemExit) as ctx:
                telegram_connector.api_call("token", "getMe")
        finally:
            telegram_connector.shared_api_call = original_shared_api_call

        self.assertEqual(str(ctx.exception), "Telegram API failed")

    def test_build_safe_command_response_any_summarizes_multi_channel_results(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3"],
            returncode=0,
            stdout='[{"channel":"@vcnews","processed_messages":3},{"channel":"@another","processed_messages":5}]',
            stderr="",
        )
        text, payload = telegram_connector.build_safe_command_response_any("update", completed)
        self.assertIn("channel=@vcnews, processed_messages=3", text)
        self.assertIn("channel=@another, processed_messages=5", text)
        self.assertIsInstance(payload, list)

    def test_build_safe_command_response_any_includes_limit_exhaustion_reason(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3"],
            returncode=0,
            stdout='[{"channel":"@a","processed_messages":1},{"channel":"@b","status":"skipped","error":"shared sync_limit budget exhausted before this channel","limit":0}]',
            stderr="",
        )
        text, payload = telegram_connector.build_safe_command_response_any("update", completed)
        self.assertIn("channel=@b, status=skipped, limit=0, error=shared sync_limit budget exhausted before this channel", text)
        self.assertIsInstance(payload, list)

    def test_build_safe_command_response_any_preserves_multi_export_payload(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3"],
            returncode=0,
            stdout='[{"channel":"@vcnews","row_count":3,"output_file":"vcnews.csv"},{"channel":"@another","row_count":4,"output_file":"another.csv"}]',
            stderr="",
        )
        text, payload = telegram_connector.build_safe_command_response_any("export-csv", completed)
        self.assertIn("output_file=vcnews.csv", text)
        self.assertIn("output_file=another.csv", text)
        self.assertEqual(payload[0]["output_file"], "vcnews.csv")

    def test_fetch_digest_usage_summary_uses_bounded_recent_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "history.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
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
                        created_at, feature, stage, channel, since, until, model, response_id, prompt_cache_key,
                        request_index, message_count, prompt_text, input_tokens, cached_input_tokens, output_tokens,
                        total_tokens, latency_ms, status
                    )
                    VALUES
                    ('2026-03-23T10:00:00+00:00', 'digest', 'batch', '@vcnews', '2026-03-22', '2026-03-22', 'gpt-5.4-mini', 'resp_1', 'digest:a', 1, 10, 'p1', 100, 50, 20, 120, 200, 'ok'),
                    ('2026-03-23T10:05:00+00:00', 'digest', 'final', '@vcnews', '2026-03-22', '2026-03-22', 'gpt-5.4-mini', 'resp_2', 'digest:a', 2, 5, 'p2', 90, 10, 18, 108, 210, 'ok'),
                    ('2026-03-23T10:10:00+00:00', 'digest', 'single', '@other', '2026-03-22', '2026-03-22', 'gpt-5.4-mini', 'resp_3', 'digest:b', 3, 5, 'p3', 80, 0, 16, 96, 220, 'ok')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            summary = telegram_connector.fetch_digest_usage_summary({"paths": {"history_db": str(db_path)}}, row_limit=2)
        assert summary is not None
        self.assertEqual(summary["global"]["total_requests"], 2)
        self.assertEqual(summary["global"]["input_tokens"], 170)
        self.assertEqual(summary["global"]["cached_input_tokens"], 10)
        self.assertEqual(summary["global"]["single_requests"], 1)

    def test_format_digest_usage_summary_includes_cached_share(self) -> None:
        text = telegram_connector.format_digest_usage_summary(
            {
                "global": {
                    "total_requests": 3,
                    "ok_requests": 2,
                    "error_requests": 1,
                    "single_requests": 1,
                    "cached_requests": 2,
                    "cache_keys": 1,
                    "first_request_at": "2026-03-23T10:00:00+00:00",
                    "last_request_at": "2026-03-23T10:10:00+00:00",
                    "input_tokens": 200,
                    "cached_input_tokens": 50,
                    "output_tokens": 40,
                },
                "filtered": None,
                "recent_rows": [
                    {
                        "stage": "final",
                        "status": "ok",
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 15,
                    }
                ],
                "row_limit": 200,
            }
        )
        self.assertIn("Digest AI usage:", text)
        self.assertIn("cached input tokens: 50", text)
        self.assertIn("cached share of input tokens: 25.0%", text)
        self.assertIn("single-pass requests: 1 (33.3%)", text)
        self.assertIn("analysis window: latest 200 requests", text)
        self.assertIn("Latest rounds:", text)
        self.assertIn("final: ok, input=100, cached=25 (25.0%), output=15", text)

    def test_project_root_override_does_not_redirect_data_files(self) -> None:
        original = os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT")
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = tmp_dir
            spec = importlib.util.spec_from_file_location("telegram_connector_override_module", MODULE_PATH)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
        if original is None:
            os.environ.pop("TELEGRAM_CONNECTOR_PROJECT_ROOT", None)
        else:
            os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = original

        self.assertEqual(module.BASE_DIR, module.APP_DIR)
        self.assertEqual(module.DATA_DIR, module.APP_DIR / "data")

    def test_handle_history_command_passes_minimal_secret_env_to_subprocess(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "date": 123,
                "text": "/update 10",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {"bridge": {"allowed_chat_ids": "42", "allowed_user_ids": "7", "worker_process_timeout_seconds": "7200"}}
        original_exists = telegram_connector.resolve_history_client_path
        original_run = telegram_connector.subprocess.run
        original_send = telegram_connector.send_text_chunks
        captured: dict[str, object] = {}

        telegram_connector.resolve_history_client_path = lambda config: MODULE_PATH

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout='{"status":"ok"}', stderr="")

        telegram_connector.subprocess.run = fake_run
        telegram_connector.send_text_chunks = lambda token, chat_id, text, chunk_size=3500, parse_mode=None: None
        try:
            telegram_connector.handle_history_command(
                "bot-token",
                config,
                update,
                secret_env={"TELEGRAM_API_HASH": "hash", "TELEGRAM_BOT_TOKEN": "token", "UNUSED": ""},
            )
        finally:
            telegram_connector.resolve_history_client_path = original_exists
            telegram_connector.subprocess.run = original_run
            telegram_connector.send_text_chunks = original_send

        env = captured["env"]
        assert isinstance(env, dict)
        self.assertEqual(env["TELEGRAM_API_HASH"], "hash")
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "token")
        self.assertNotIn("UNUSED", env)
        self.assertEqual(captured["timeout"], 7200)

    def test_handle_history_command_suppresses_success_echo_for_digest(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "date": 123,
                "text": "/digest -4d",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {"bridge": {"allowed_chat_ids": "42", "allowed_user_ids": "7"}}
        original_run = telegram_connector.subprocess.run
        original_send = telegram_connector.send_text_chunks
        sent_messages: list[str] = []

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout='{"status":"sent","auth_mode":"user","sync_mode":"backfill","limit_profile":"day","channels":2,"sync_limit":6000,"since":"2026-03-19","until":"2026-03-19"}',
                stderr="",
            )

        telegram_connector.subprocess.run = fake_run
        telegram_connector.send_text_chunks = lambda token, chat_id, text, chunk_size=None, parse_mode=None: sent_messages.append(text)
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.subprocess.run = original_run
            telegram_connector.send_text_chunks = original_send

        self.assertEqual(sent_messages, [])

    def test_handle_history_command_rejects_unknown_user(self) -> None:
        update = {
            "update_id": 2,
            "message": {
                "date": 123,
                "text": "/update 10",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 99, "username": "mallory"},
            },
        }
        config = {"bridge": {"allowed_chat_ids": "42", "allowed_user_ids": "7", "allowed_usernames": ""}}
        original_send = telegram_connector.send_text_message
        original_run = telegram_connector.subprocess.run
        captured: dict[str, object] = {}

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for a rejected user")

        telegram_connector.send_text_message = lambda token, chat_id, text, parse_mode=None: captured.update(
            {"chat_id": chat_id, "text": text}
        )
        telegram_connector.subprocess.run = fail_run
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.send_text_message = original_send
            telegram_connector.subprocess.run = original_run

        self.assertEqual(captured["chat_id"], 42)
        self.assertIn("not allowed", str(captured["text"]))

    def test_handle_history_command_rejects_missing_chat_allowlist(self) -> None:
        update = {
            "update_id": 3,
            "message": {
                "date": 123,
                "text": "/update 10",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {"bridge": {"allowed_chat_ids": "", "allowed_user_ids": "7", "allowed_usernames": ""}}
        original_send = telegram_connector.send_text_message
        original_run = telegram_connector.subprocess.run
        captured: dict[str, object] = {}

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called without a chat allowlist")

        telegram_connector.send_text_message = lambda token, chat_id, text, parse_mode=None: captured.update(
            {"chat_id": chat_id, "text": text}
        )
        telegram_connector.subprocess.run = fail_run
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.send_text_message = original_send
            telegram_connector.subprocess.run = original_run

        self.assertEqual(captured["chat_id"], 42)
        self.assertIn("allowlist is not configured", str(captured["text"]))

    def test_handle_history_command_rejects_empty_user_allowlist(self) -> None:
        update = {
            "update_id": 3,
            "message": {
                "date": 123,
                "text": "/update 10",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {"bridge": {"allowed_chat_ids": "42"}}
        original_send = telegram_connector.send_text_message
        original_run = telegram_connector.subprocess.run
        captured: dict[str, object] = {}

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called with an empty user allowlist")

        telegram_connector.send_text_message = lambda token, chat_id, text, parse_mode=None: captured.update(
            {"chat_id": chat_id, "text": text}
        )
        telegram_connector.subprocess.run = fail_run
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.send_text_message = original_send
            telegram_connector.subprocess.run = original_run

        self.assertEqual(captured["chat_id"], 42)
        self.assertIn("not allowed", str(captured["text"]))

    def test_handle_history_command_serves_agent_stats_without_subprocess(self) -> None:
        update = {
            "update_id": 3,
            "message": {
                "date": 123,
                "text": "/agent-stats",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {
            "bridge": {
                "allowed_chat_ids": "42",
                "allowed_user_ids": "7",
                "agent_stats_row_limit": "200",
                "text_chunk_size": "3900",
            }
        }
        original_summary = telegram_connector.fetch_digest_usage_summary
        original_send_chunks = telegram_connector.send_text_chunks
        original_run = telegram_connector.subprocess.run
        captured: dict[str, object] = {}

        telegram_connector.fetch_digest_usage_summary = lambda cfg, row_limit: {
            "global": {
                "total_requests": 1,
                "ok_requests": 1,
                "error_requests": 0,
                "single_requests": 1,
                "cached_requests": 1,
                "cache_keys": 1,
                "first_request_at": "2026-03-23T10:00:00+00:00",
                "last_request_at": "2026-03-23T10:00:00+00:00",
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "output_tokens": 15,
            },
            "filtered": None,
            "recent_rows": [
                {"stage": "final", "status": "ok", "input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 15}
            ],
            "row_limit": row_limit,
        }
        telegram_connector.send_text_chunks = (
            lambda token, chat_id, text, chunk_size=3500, parse_mode=None: captured.update(
                {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            )
        )

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for /agent-stats")

        telegram_connector.subprocess.run = fail_run
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.fetch_digest_usage_summary = original_summary
            telegram_connector.send_text_chunks = original_send_chunks
            telegram_connector.subprocess.run = original_run
        self.assertEqual(captured["chat_id"], 42)
        self.assertEqual(captured["parse_mode"], "HTML")
        self.assertIn("<b>Digest AI usage:</b>", str(captured["text"]))
        self.assertIn("single-pass requests: 1 (100.0%)", str(captured["text"]))

    def test_handle_history_command_serves_top_models_without_subprocess(self) -> None:
        update = {
            "update_id": 4,
            "message": {
                "date": 123,
                "text": "/top-models 2",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {
            "bridge": {
                "allowed_chat_ids": "42",
                "allowed_user_ids": "7",
                "text_chunk_size": "3900",
                "top_models_api_url": "https://example.invalid/top-models",
                "top_models_timeout_seconds": "15",
                "top_models_default_limit": "5",
                "top_models_cache_ttl_seconds": "300",
                "top_models_retry_attempts": "3",
            }
        }
        original_fetch = telegram_connector.fetch_top_models_payload
        original_send_chunks = telegram_connector.send_text_chunks
        original_run = telegram_connector.subprocess.run
        captured: dict[str, object] = {}

        telegram_connector.fetch_top_models_payload = lambda **kwargs: {
            "updatedAt": "2026-04-27T03:17:24.821Z",
            "source": "openrouter-models-api",
            "rankingVersion": "2026-04-27.v1",
            "fallback": {"id": "openrouter/free"},
            "models": [
                {
                    "rank": 1,
                    "name": "Model One",
                    "score": 1000,
                    "contextLength": 262144,
                    "maxCompletionTokens": 32768,
                    "supportsTools": True,
                    "supportsStructuredOutputs": True,
                    "supportsReasoning": False,
                    "latencyMs": 1414,
                    "healthStatus": "passed",
                    "reason": "Tools, structured outputs",
                },
                {
                    "rank": 2,
                    "name": "Model Two",
                    "score": 900,
                    "contextLength": 131072,
                    "maxCompletionTokens": 8192,
                    "supportsTools": False,
                    "supportsStructuredOutputs": False,
                    "supportsReasoning": True,
                    "latencyMs": None,
                    "healthStatus": "not_probed",
                    "reason": "Reasoning model",
                },
            ],
        }
        telegram_connector.send_text_chunks = (
            lambda token, chat_id, text, chunk_size=3500, parse_mode=None: captured.update(
                {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            )
        )

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for /top-models")

        telegram_connector.subprocess.run = fail_run
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.fetch_top_models_payload = original_fetch
            telegram_connector.send_text_chunks = original_send_chunks
            telegram_connector.subprocess.run = original_run

        self.assertEqual(captured["chat_id"], 42)
        self.assertEqual(captured["parse_mode"], "HTML")
        self.assertIn("<b>Top free LLM models</b>", str(captured["text"]))
        self.assertIn("<b>1. Model One</b>", str(captured["text"]))
        self.assertIn("<b>2. Model Two</b>", str(captured["text"]))

    def test_resolve_top_models_api_url_rejects_non_http_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "http or https"):
            telegram_connector.resolve_top_models_api_url({"bridge": {"top_models_api_url": "file:///tmp/models.json"}})

    def test_handle_history_command_serves_top_models_debug_without_subprocess(self) -> None:
        update = {
            "update_id": 5,
            "message": {
                "date": 123,
                "text": "/top-models 1 debug",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7, "username": "alice"},
            },
        }
        config = {
            "bridge": {
                "allowed_chat_ids": "42",
                "allowed_user_ids": "7",
                "text_chunk_size": "3900",
                "top_models_api_url": "https://example.invalid/top-models",
                "top_models_timeout_seconds": "15",
                "top_models_default_limit": "5",
                "top_models_cache_ttl_seconds": "300",
                "top_models_retry_attempts": "3",
            }
        }
        original_fetch = telegram_connector.fetch_top_models_payload
        original_send_chunks = telegram_connector.send_text_chunks
        original_run = telegram_connector.subprocess.run
        captured: dict[str, object] = {}

        telegram_connector.fetch_top_models_payload = lambda **kwargs: {
            "updatedAt": "2026-04-27T03:17:24.821Z",
            "source": "openrouter-models-api",
            "rankingVersion": "2026-04-27.v1",
            "fallback": {"id": "openrouter/free"},
            "models": [
                {
                    "rank": 1,
                    "id": "test/model:free",
                    "name": "Model One",
                    "score": 1000,
                    "metadataScore": 600,
                    "healthScore": 400,
                    "latencyScore": 60,
                    "liteEvalScore": 680,
                    "contextLength": 262144,
                    "maxCompletionTokens": 32768,
                    "supportsTools": True,
                    "supportsToolChoice": True,
                    "supportsStructuredOutputs": True,
                    "supportsResponseFormat": True,
                    "supportsReasoning": False,
                    "supportsIncludeReasoning": False,
                    "supportsSeed": True,
                    "supportsStop": True,
                    "latencyMs": 1414,
                    "healthStatus": "passed",
                    "evalSuite": "lite-agent-eval-v1",
                    "evalSummary": {"status": "completed", "passed": 2, "total": 3},
                    "reason": "Tools, structured outputs",
                    "instabilityPenalty": 0,
                }
            ],
        }
        telegram_connector.send_text_chunks = (
            lambda token, chat_id, text, chunk_size=3500, parse_mode=None: captured.update(
                {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            )
        )

        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called for /top-models debug")

        telegram_connector.subprocess.run = fail_run
        try:
            telegram_connector.handle_history_command("bot-token", config, update, secret_env={})
        finally:
            telegram_connector.fetch_top_models_payload = original_fetch
            telegram_connector.send_text_chunks = original_send_chunks
            telegram_connector.subprocess.run = original_run

        self.assertEqual(captured["chat_id"], 42)
        self.assertEqual(captured["parse_mode"], "HTML")
        self.assertIn("<b>1. Model One</b>", str(captured["text"]))
        self.assertIn("id: test/model:free", str(captured["text"]))
        self.assertIn("metadataScore: 600", str(captured["text"]))
        self.assertIn("evalSummary:", str(captured["text"]))


if __name__ == "__main__":
    unittest.main()
