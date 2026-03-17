import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "telegram_connector.py"
SPEC = importlib.util.spec_from_file_location("telegram_connector_module", MODULE_PATH)
telegram_connector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telegram_connector)


class TelegramConnectorTests(unittest.TestCase):
    def test_build_history_command_for_backfill_with_media(self) -> None:
        command = telegram_connector.build_history_command("/backfill @vcnews 200 media")
        self.assertEqual(
            command[2:],
            ["backfill", "--channel", "@vcnews", "--limit", "200", "--download-media", "--auth-mode", "user"],
        )

    def test_build_history_command_for_backfill_with_period(self) -> None:
        command = telegram_connector.build_history_command("/backfill @vcnews since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            ["backfill", "--channel", "@vcnews", "--limit", "1000", "--since", "2026-03-15", "--until", "2026-03-16", "--auth-mode", "user"],
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

    def test_normalize_bridge_command_text_strips_bot_suffix(self) -> None:
        self.assertEqual(
            telegram_connector.normalize_bridge_command_text("/update@verter_the_bot 10"),
            "/update 10",
        )

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

    def test_build_history_command_rejects_unknown_command(self) -> None:
        with self.assertRaises(ValueError):
            telegram_connector.build_history_command("/boom")

    def test_build_history_command_for_ocr_pending_with_period_and_channel(self) -> None:
        command = telegram_connector.build_history_command("/ocr @vcnews 50 since=2026-03-15 until=2026-03-16")
        self.assertEqual(
            command[2:],
            ["ocr-pending", "--channel", "@vcnews", "--limit", "50", "--since", "2026-03-15", "--until", "2026-03-16"],
        )

    def test_parse_allowed_chat_ids_falls_back_to_default_chat(self) -> None:
        config = {"telegram": {"default_chat_id": "133126275"}}
        self.assertEqual(telegram_connector.parse_allowed_chat_ids(config), {"133126275"})

    def test_send_text_chunks_splits_long_messages(self) -> None:
        sent_messages: list[str] = []

        def fake_send(token: str, chat_id: str | int, text: str) -> None:
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

    def test_resolve_secret_value_reads_onepassword_reference(self) -> None:
        original_run = telegram_connector.subprocess.run
        telegram_connector._SECRET_CACHE.clear()

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="secret-from-op\n", stderr="")

        telegram_connector.subprocess.run = fake_run
        try:
            value = telegram_connector.resolve_secret_value("op://Private/item/field", "Bot token")
        finally:
            telegram_connector.subprocess.run = original_run

        self.assertEqual(value, "secret-from-op")

    def test_resolve_bridge_secrets_reads_expected_env_bundle(self) -> None:
        original_run = telegram_connector.subprocess.run
        telegram_connector._SECRET_CACHE.clear()
        config = {
            "telethon": {
                "api_id": "op://Personal/item/api_id",
                "phone": "op://Personal/item/phone",
            },
            "secrets": {
                "api_hash": "op://Personal/item/api_hash",
                "bot_token": "op://Personal/item/bot_token",
                "user_password": "op://Personal/item/user_password",
            },
        }
        values = {
            "op://Personal/item/api_id": "1",
            "op://Personal/item/phone": "+34111111111",
            "op://Personal/item/api_hash": "hash",
            "op://Personal/item/bot_token": "token",
            "op://Personal/item/user_password": "pw",
        }

        def fake_run(*args, **kwargs):
            reference = args[0][-1]
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

    def test_project_root_override_points_data_files_to_project_dir(self) -> None:
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

        self.assertEqual(module.BASE_DIR, Path(tmp_dir))
        self.assertEqual(module.DATA_DIR, Path(tmp_dir) / "data")

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
        config = {"bridge": {"allowed_chat_ids": "42"}}
        original_exists = telegram_connector.resolve_history_client_path
        original_run = telegram_connector.subprocess.run
        original_send = telegram_connector.send_text_chunks
        captured: dict[str, object] = {}

        telegram_connector.resolve_history_client_path = lambda config: MODULE_PATH

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout='{"status":"ok"}', stderr="")

        telegram_connector.subprocess.run = fake_run
        telegram_connector.send_text_chunks = lambda token, chat_id, text, chunk_size=3500: None
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


if __name__ == "__main__":
    unittest.main()
