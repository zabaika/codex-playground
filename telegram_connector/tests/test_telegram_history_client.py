import importlib.util
import csv
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "telegram_history_client.py"
SPEC = importlib.util.spec_from_file_location("telegram_history_client_module", MODULE_PATH)
telegram_history_client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telegram_history_client)


class TelegramHistoryClientTests(unittest.TestCase):
    def test_init_db_creates_expected_tables(self) -> None:
        conn = sqlite3.connect(":memory:")
        telegram_history_client.init_db(conn)

        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {row[0] for row in rows}
        self.assertTrue({"channels", "messages", "media_assets", "sync_state"}.issubset(names))

    def test_update_sync_state_keeps_channel_specific_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)

        telegram_history_client.update_sync_state(conn, 1, last_tail_message_id=10, last_tail_at="2026-03-16T10:00:00+00:00")
        telegram_history_client.update_sync_state(conn, 2, last_tail_message_id=20, last_tail_at="2026-03-16T11:00:00+00:00")
        conn.commit()

        rows = list(conn.execute("SELECT channel_id, last_tail_message_id FROM sync_state ORDER BY channel_id"))
        self.assertEqual([(row["channel_id"], row["last_tail_message_id"]) for row in rows], [(1, 10), (2, 20)])

    def test_resolve_runtime_uses_local_defaults(self) -> None:
        original_file = telegram_history_client.RUNTIME_LOCAL_FILE
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_file = Path(tmp_dir) / "runtime.local.toml"
            runtime_file.write_text(
                """
[telethon]
user_session_name = "session_user_x"
bot_session_name = "session_bot_x"
api_id = "12345"
phone = "+34123456789"

[auth]
default_mode = "auto"
public_channel_mode = "bot"
private_channel_mode = "user"

[paths]
history_db = "/tmp/history.sqlite3"
media_root = "/tmp/media"
tesseract_binary = "/usr/local/bin/tesseract"

[ocr]
image_prompt = "OCR this"

[secrets]
api_hash = "hash_x"
bot_token = "bot_x"
user_password = "pw_x"
""".strip(),
                encoding="utf-8",
            )
            telegram_history_client.RUNTIME_LOCAL_FILE = runtime_file
            try:
                runtime = telegram_history_client.resolve_runtime()
            finally:
                telegram_history_client.RUNTIME_LOCAL_FILE = original_file

        self.assertEqual(runtime.user_session_name, "session_user_x")
        self.assertEqual(runtime.bot_session_name, "session_bot_x")
        self.assertEqual(str(runtime.db_path), "/tmp/history.sqlite3")
        self.assertEqual(str(runtime.media_root), "/tmp/media")
        self.assertEqual(runtime.api_id, "12345")
        self.assertEqual(runtime.api_hash, "hash_x")
        self.assertEqual(runtime.phone, "+34123456789")
        self.assertEqual(runtime.bot_token, "bot_x")
        self.assertEqual(runtime.user_password, "pw_x")
        self.assertEqual(runtime.tesseract_binary, "/usr/local/bin/tesseract")
        self.assertEqual(runtime.vision_prompt, "OCR this")
        self.assertEqual(runtime.default_auth_mode, "auto")
        self.assertEqual(runtime.public_auth_mode, "bot")
        self.assertEqual(runtime.private_auth_mode, "user")

    def test_project_root_override_points_runtime_paths_to_project_dir(self) -> None:
        original = os.environ.get("TELEGRAM_CONNECTOR_PROJECT_ROOT")
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = tmp_dir
            spec = importlib.util.spec_from_file_location("telegram_history_client_override_module", MODULE_PATH)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
        if original is None:
            os.environ.pop("TELEGRAM_CONNECTOR_PROJECT_ROOT", None)
        else:
            os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = original

        self.assertEqual(module.BASE_DIR, Path(tmp_dir))
        self.assertEqual(module.DATA_DIR, Path(tmp_dir) / "data")
        self.assertEqual(module.DB_FILE, Path(tmp_dir) / "data" / "telegram_history.sqlite3")

    def test_resolve_auth_mode_for_public_channel_prefers_bot(self) -> None:
        runtime = telegram_history_client.RuntimeConfig(
            db_path=Path("/tmp/db.sqlite3"),
            media_root=Path("/tmp/media"),
            user_session_name="user",
            bot_session_name="bot",
            api_id="1",
            api_hash="hash",
            phone="+1",
            bot_token="token",
            user_password="pw",
            tesseract_binary="tesseract",
            vision_prompt="prompt",
            default_auth_mode="auto",
            public_auth_mode="bot",
            private_auth_mode="user",
        )
        self.assertEqual(telegram_history_client.resolve_auth_mode(runtime, "auto", "@vcnews"), "bot")

    def test_resolve_auth_mode_for_private_reference_prefers_user(self) -> None:
        runtime = telegram_history_client.RuntimeConfig(
            db_path=Path("/tmp/db.sqlite3"),
            media_root=Path("/tmp/media"),
            user_session_name="user",
            bot_session_name="bot",
            api_id="1",
            api_hash="hash",
            phone="+1",
            bot_token="token",
            user_password="pw",
            tesseract_binary="tesseract",
            vision_prompt="prompt",
            default_auth_mode="auto",
            public_auth_mode="bot",
            private_auth_mode="user",
        )
        self.assertEqual(
            telegram_history_client.resolve_auth_mode(runtime, "auto", "https://t.me/+invitehash"),
            "user",
        )

    def test_export_channel_csv_by_limit(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)
        now = "2026-03-16T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO channels(channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at)
            VALUES (1, '', 'vcnews', 'vc.ru', 'Channel', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, post_author, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES (1, 1, '', ?, NULL, '', NULL, 'first', NULL, NULL, NULL, 0, NULL, '{}', 'h1', ?),
                   (1, 2, '', ?, NULL, '', NULL, 'second', NULL, NULL, NULL, 1, 'photo', '{}', 'h2', ?)
            """,
            (now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO media_assets(channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size, ocr_status, ocr_text, created_at)
            VALUES (1, 2, 0, 'photo', '/tmp/2.jpg', 'photo', 100, 'done', 'detected text', ?)
            """,
            (now,),
        )
        runtime = telegram_history_client.RuntimeConfig(
            db_path=Path("/tmp/db.sqlite3"),
            media_root=Path("/tmp/media"),
            user_session_name="user",
            bot_session_name="bot",
            api_id="1",
            api_hash="hash",
            phone="+1",
            bot_token="token",
            user_password="pw",
            tesseract_binary="tesseract",
            vision_prompt="prompt",
            default_auth_mode="auto",
            public_auth_mode="bot",
            private_auth_mode="user",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path, row_count = telegram_history_client.export_channel_csv(
                conn,
                runtime,
                channel="@vcnews",
                limit=1,
                since=None,
                until=None,
                output_path=str(Path(tmp_dir) / "out.csv"),
            )
            with out_path.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh, delimiter=";"))
            header_line = out_path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(row_count, 1)
        self.assertEqual(rows[0]["message_id"], "2")
        self.assertEqual(rows[0]["ocr_text"], "detected text")
        self.assertEqual(rows[0]["has_local_media"], "1")
        self.assertNotIn("local_path", rows[0])
        self.assertIn(";", header_line)

    def test_export_channel_csv_since_without_until_includes_newest(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)
        first = "2026-03-15T10:00:00+00:00"
        second = "2026-03-16T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO channels(channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at)
            VALUES (1, '', 'vcnews', 'vc.ru', 'Channel', '{}', ?, ?)
            """,
            (first, second),
        )
        conn.execute(
            """
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, post_author, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES
                (1, 1, '', ?, NULL, '', NULL, 'older', NULL, NULL, NULL, 0, NULL, '{}', 'h1', ?),
                (1, 2, '', ?, NULL, '', NULL, 'newer', NULL, NULL, NULL, 0, NULL, '{}', 'h2', ?)
            """,
            (first, first, second, second),
        )
        runtime = telegram_history_client.RuntimeConfig(
            db_path=Path("/tmp/db.sqlite3"),
            media_root=Path("/tmp/media"),
            user_session_name="user",
            bot_session_name="bot",
            api_id="1",
            api_hash="hash",
            phone="+1",
            bot_token="token",
            user_password="pw",
            tesseract_binary="tesseract",
            vision_prompt="prompt",
            default_auth_mode="user",
            public_auth_mode="bot",
            private_auth_mode="user",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path, row_count = telegram_history_client.export_channel_csv(
                conn,
                runtime,
                channel="@vcnews",
                limit=None,
                since="2026-03-15",
                until=None,
                output_path=str(Path(tmp_dir) / "period.csv"),
            )
            with out_path.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh, delimiter=";"))
        self.assertEqual(row_count, 2)
        self.assertEqual([row["message_id"] for row in rows], ["2", "1"])

    def test_export_csv_parser_accepts_auth_mode_for_bot_bridge_compatibility(self) -> None:
        parser = telegram_history_client.build_parser()
        args = parser.parse_args(["export-csv", "--channel", "@vcnews", "--limit", "5", "--auth-mode", "user"])
        self.assertEqual(args.command, "export-csv")
        self.assertEqual(args.auth_mode, "user")

    def test_media_needs_download_detects_missing_local_file(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)
        now = "2026-03-16T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO channels(channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at)
            VALUES (1, '', 'vcnews', 'vc.ru', 'Channel', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, post_author, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES (1, 2, '', ?, NULL, '', NULL, 'second', NULL, NULL, NULL, 1, 'photo', '{}', 'h2', ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO media_assets(channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size, ocr_status, created_at)
            VALUES (1, 2, 0, 'photo', NULL, 'photo', 100, 'skipped', ?)
            """,
            (now,),
        )
        self.assertTrue(telegram_history_client.media_needs_download(conn, 1, 2))

    def test_minimal_raw_json_does_not_store_full_message_text(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)
        now = "2026-03-16T10:00:00+00:00"

        class Entity:
            id = 1
            username = "vcnews"
            title = "vc.ru"

        class Message:
            id = 2
            message = "super secret message body"
            date = None
            edit_date = None
            sender_id = 5
            post_author = None
            views = 10
            forwards = 1
            replies = None
            media = None
            grouped_id = None

        telegram_history_client.upsert_channel(conn, Entity())
        telegram_history_client.upsert_message(conn, Entity(), Message(), None, None)
        row = conn.execute("SELECT raw_json FROM messages WHERE channel_id = 1 AND message_id = 2").fetchone()
        raw = json.loads(row["raw_json"])
        self.assertEqual(raw["text_length"], len("super secret message body"))
        self.assertNotIn("super secret message body", row["raw_json"])

    def test_iter_pending_ocr_filters_non_image_media(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)
        now = "2026-03-16T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO channels(channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at)
            VALUES (1, '', 'vcnews', 'vc.ru', 'Channel', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, post_author, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES
                (1, 1, '', ?, NULL, '', NULL, 'image', NULL, NULL, NULL, 1, 'photo', '{}', 'h1', ?),
                (1, 2, '', ?, NULL, '', NULL, 'pdf', NULL, NULL, NULL, 1, 'application/pdf', '{}', 'h2', ?)
            """,
            (now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO media_assets(channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size, ocr_status, created_at)
            VALUES
                (1, 1, 0, 'photo', '/tmp/1.jpg', 'photo', 10, 'pending', ?),
                (1, 2, 0, 'application/pdf', '/tmp/2.pdf', 'application/pdf', 10, 'pending', ?)
            """,
            (now, now),
        )
        rows = telegram_history_client.iter_pending_ocr(conn, 10)
        self.assertEqual([(row["message_id"], row["media_kind"]) for row in rows], [(1, "photo")])

    def test_resolve_runtime_reads_onepassword_references(self) -> None:
        original_file = telegram_history_client.RUNTIME_LOCAL_FILE
        original_run = telegram_history_client.subprocess.run
        telegram_history_client._SECRET_CACHE.clear()
        values = {
            "op://Personal/telegram-connector/api_id": "12345",
            "op://Personal/telegram-connector/api_hash": "api_hash_from_op",
            "op://Personal/telegram-connector/bot_token": "bot_token_from_op",
            "op://Personal/telegram-connector/user_password": "user_password_from_op",
            "op://Personal/telegram-connector/phone": "+34111111111",
        }

        def fake_run(*args, **kwargs):
            reference = args[0][-1]
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=f"{values[reference]}\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_file = Path(tmp_dir) / "runtime.local.toml"
            runtime_file.write_text(
                """
[telethon]
user_session_name = "session_user_x"
bot_session_name = "session_bot_x"
api_id = "op://Personal/telegram-connector/api_id"
phone = "op://Personal/telegram-connector/phone"

[auth]
default_mode = "user"
public_channel_mode = "bot"
private_channel_mode = "user"

[paths]
history_db = "/tmp/history.sqlite3"
media_root = "/tmp/media"
tesseract_binary = "/usr/local/bin/tesseract"

[ocr]
image_prompt = "OCR this"

[secrets]
api_hash = "op://Personal/telegram-connector/api_hash"
bot_token = "op://Personal/telegram-connector/bot_token"
user_password = "op://Personal/telegram-connector/user_password"
""".strip(),
                encoding="utf-8",
            )
            telegram_history_client.RUNTIME_LOCAL_FILE = runtime_file
            telegram_history_client.subprocess.run = fake_run
            try:
                runtime = telegram_history_client.resolve_runtime()
            finally:
                telegram_history_client.RUNTIME_LOCAL_FILE = original_file
                telegram_history_client.subprocess.run = original_run

        self.assertEqual(runtime.api_id, "12345")
        self.assertEqual(runtime.api_hash, "api_hash_from_op")
        self.assertEqual(runtime.bot_token, "bot_token_from_op")
        self.assertEqual(runtime.user_password, "user_password_from_op")
        self.assertEqual(runtime.phone, "+34111111111")


if __name__ == "__main__":
    unittest.main()
