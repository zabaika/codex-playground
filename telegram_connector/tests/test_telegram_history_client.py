import importlib.util
import csv
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import types


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
        columns = [row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()]
        self.assertIn("sender_username", columns)
        self.assertIn("sender_display_name", columns)
        self.assertNotIn("post_author", columns)

    def test_update_sync_state_keeps_channel_specific_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)

        telegram_history_client.update_sync_state(conn, 1, last_tail_message_id=10, last_tail_at="2026-03-16T10:00:00+00:00")
        telegram_history_client.update_sync_state(conn, 2, last_tail_message_id=20, last_tail_at="2026-03-16T11:00:00+00:00")
        conn.commit()

        rows = list(conn.execute("SELECT channel_id, last_tail_message_id FROM sync_state ORDER BY channel_id"))
        self.assertEqual([(row["channel_id"], row["last_tail_message_id"]) for row in rows], [(1, 10), (2, 20)])

    def test_init_db_migrates_legacy_post_author_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE channels (
                channel_id INTEGER PRIMARY KEY,
                access_hash TEXT,
                username TEXT,
                title TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                grouped_id TEXT,
                date_utc TEXT NOT NULL,
                edit_date_utc TEXT,
                sender_id TEXT,
                post_author TEXT,
                text TEXT NOT NULL,
                views INTEGER,
                forwards INTEGER,
                replies INTEGER,
                has_media INTEGER NOT NULL DEFAULT 0,
                media_kind TEXT,
                raw_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (channel_id, message_id)
            );
            CREATE TABLE media_assets (
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0,
                media_kind TEXT NOT NULL,
                local_path TEXT,
                mime_type TEXT,
                file_size INTEGER,
                ocr_status TEXT NOT NULL DEFAULT 'pending',
                ocr_text TEXT,
                ocr_error TEXT,
                ocr_processed_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (channel_id, message_id, ordinal)
            );
            CREATE TABLE sync_state (
                channel_id INTEGER PRIMARY KEY,
                last_backfill_message_id INTEGER,
                last_tail_message_id INTEGER,
                last_tail_at TEXT,
                last_live_event_at TEXT,
                last_full_sync_at TEXT,
                last_error TEXT
            );
            INSERT INTO channels(channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at)
            VALUES (1, '', 'vcnews', 'vc.ru', 'Channel', '{}', '2026-03-16T10:00:00+00:00', '2026-03-16T10:00:00+00:00');
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, post_author, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES (1, 2, '', '2026-03-16T10:00:00+00:00', NULL, '', 'old-author', 'hello', NULL, NULL, NULL, 0, NULL, '{}', 'h2', '2026-03-16T10:00:00+00:00');
            INSERT INTO media_assets(channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size, ocr_status, ocr_text, ocr_error, ocr_processed_at, created_at)
            VALUES (1, 2, 0, 'photo', '', '', 10, 'done', '', '', NULL, '2026-03-16T10:00:00+00:00');
            """
        )

        telegram_history_client.init_db(conn)

        columns = [row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()]
        self.assertIn("sender_username", columns)
        self.assertIn("sender_display_name", columns)
        self.assertNotIn("post_author", columns)
        row = conn.execute(
            "SELECT grouped_id, sender_id, sender_username, sender_display_name FROM messages WHERE channel_id = 1 AND message_id = 2"
        ).fetchone()
        self.assertIsNone(row["grouped_id"])
        self.assertIsNone(row["sender_id"])
        self.assertIsNone(row["sender_username"])
        self.assertIsNone(row["sender_display_name"])
        media = conn.execute(
            "SELECT local_path, mime_type, ocr_text, ocr_error FROM media_assets WHERE channel_id = 1 AND message_id = 2 AND ordinal = 0"
        ).fetchone()
        self.assertIsNone(media["local_path"])
        self.assertIsNone(media["mime_type"])
        self.assertIsNone(media["ocr_text"])
        self.assertIsNone(media["ocr_error"])

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

[channels]
default_list = [
  "@vcnews, vc.ru",
  "@another_channel, Another Channel",
]

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
        self.assertEqual(runtime.default_channels, ["@vcnews", "@another_channel"])

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

    def test_parse_channel_list_supports_single_and_multiple_channels(self) -> None:
        self.assertEqual(telegram_history_client.parse_channel_list("@vcnews"), ["@vcnews"])
        self.assertEqual(
            telegram_history_client.parse_channel_list("@vcnews, @another_channel"),
            ["@vcnews", "@another_channel"],
        )

    def test_parse_default_channel_entry_keeps_only_channel_reference(self) -> None:
        self.assertEqual(
            telegram_history_client.parse_default_channel_entry("@vcnews, vc.ru"),
            "@vcnews",
        )

    def test_get_default_channels_supports_named_entries(self) -> None:
        config = {
            "channels": {
                "default_list": [
                    "@vcnews, vc.ru",
                    "@another_channel, Another Channel",
                ]
            }
        }
        self.assertEqual(
            telegram_history_client.get_default_channels(config),
            ["@vcnews", "@another_channel"],
        )

    def test_resolve_channels_argument_uses_default_config_list_when_channel_missing(self) -> None:
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
            default_channels=["@vcnews", "@another_channel"],
        )
        self.assertEqual(
            telegram_history_client.resolve_channels_argument(runtime, None),
            ["@vcnews", "@another_channel"],
        )

    def test_resolve_channels_argument_prefers_explicit_channels(self) -> None:
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
            default_channels=["@default"],
        )
        self.assertEqual(
            telegram_history_client.resolve_channels_argument(runtime, "@explicit,@another"),
            ["@explicit", "@another"],
        )

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
            default_channels=[],
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
            default_channels=[],
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
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, sender_username, sender_display_name, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES (1, 1, NULL, ?, NULL, NULL, NULL, NULL, 'first', NULL, NULL, NULL, 0, NULL, '{}', 'h1', ?),
                   (1, 2, NULL, ?, NULL, NULL, 'vcnews', 'vc.ru', 'second', NULL, NULL, NULL, 1, 'photo', '{}', 'h2', ?)
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
            default_channels=[],
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
        self.assertEqual(rows[0]["sender_username"], "vcnews")
        self.assertEqual(rows[0]["sender_display_name"], "vc.ru")
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
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, sender_username, sender_display_name, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES
                (1, 1, NULL, ?, NULL, NULL, NULL, NULL, 'older', NULL, NULL, NULL, 0, NULL, '{}', 'h1', ?),
                (1, 2, NULL, ?, NULL, NULL, NULL, NULL, 'newer', NULL, NULL, NULL, 0, NULL, '{}', 'h2', ?)
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
            default_channels=[],
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

    def test_sync_parsers_accept_since_until(self) -> None:
        parser = telegram_history_client.build_parser()
        backfill_args = parser.parse_args(["backfill", "--channel", "@vcnews", "--since", "2026-03-15", "--until", "2026-03-16"])
        tail_args = parser.parse_args(["sync", "--mode", "tail", "--channel", "@vcnews", "--since", "2026-03-15"])
        update_args = parser.parse_args(["sync", "--mode", "update", "--channel", "@vcnews", "--until", "2026-03-16"])
        ocr_pending_args = parser.parse_args(["ocr-pending", "--channel", "@vcnews", "--since", "2026-03-15", "--until", "2026-03-16"])
        self.assertEqual(backfill_args.since, "2026-03-15")
        self.assertEqual(backfill_args.until, "2026-03-16")
        self.assertEqual(tail_args.mode, "tail")
        self.assertEqual(tail_args.since, "2026-03-15")
        self.assertIsNone(tail_args.until)
        self.assertEqual(update_args.mode, "update")
        self.assertIsNone(update_args.since)
        self.assertEqual(update_args.until, "2026-03-16")
        self.assertEqual(ocr_pending_args.channel, "@vcnews")
        self.assertEqual(ocr_pending_args.since, "2026-03-15")
        self.assertEqual(ocr_pending_args.until, "2026-03-16")

    def test_parse_until_datetime_uses_end_of_day_for_date_only(self) -> None:
        self.assertEqual(
            telegram_history_client.parse_until_datetime("2026-03-16"),
            "2026-03-16T23:59:59+00:00",
        )

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
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, sender_username, sender_display_name, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES (1, 2, NULL, ?, NULL, NULL, NULL, NULL, 'second', NULL, NULL, NULL, 1, 'photo', '{}', 'h2', ?)
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
            sender_id = None
            views = 10
            forwards = 1
            replies = None
            media = None
            grouped_id = None

        telegram_history_client.upsert_channel(conn, Entity())
        telegram_history_client.upsert_message(conn, Entity(), Message(), None, None, None, None)
        row = conn.execute(
            "SELECT grouped_id, sender_id, sender_username, sender_display_name, raw_json FROM messages WHERE channel_id = 1 AND message_id = 2"
        ).fetchone()
        raw = json.loads(row["raw_json"])
        self.assertIsNone(row["grouped_id"])
        self.assertIsNone(row["sender_id"])
        self.assertIsNone(row["sender_username"])
        self.assertIsNone(row["sender_display_name"])
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
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, sender_username, sender_display_name, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES
                (1, 1, NULL, ?, NULL, NULL, NULL, NULL, 'image', NULL, NULL, NULL, 1, 'photo', '{}', 'h1', ?),
                (1, 2, NULL, ?, NULL, NULL, NULL, NULL, 'pdf', NULL, NULL, NULL, 1, 'application/pdf', '{}', 'h2', ?)
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

    def test_resolve_sender_metadata_uses_channel_entity_without_get_sender_for_channel_posts(self) -> None:
        class Entity:
            id = 1
            username = "vcnews"
            title = "vc.ru"

        class Message:
            post = True
            sender_id = 1

            async def get_sender(self):
                raise AssertionError("get_sender should not be called for channel posts")

        username, display_name = asyncio.run(telegram_history_client.resolve_sender_metadata(Entity(), Message()))
        self.assertEqual(username, "vcnews")
        self.assertEqual(display_name, "vc.ru")

    def test_resolve_sender_metadata_uses_direct_message_fields_without_get_sender(self) -> None:
        class Entity:
            id = 1
            username = "refugecard"
            title = "Refuge"

        class Message:
            post = False
            sender_id = 42
            sender_username = "alice"
            sender_display_name = "Alice"
            sender = None

            async def get_sender(self):
                raise AssertionError("get_sender should not be called when direct sender fields already exist")

        username, display_name = asyncio.run(telegram_history_client.resolve_sender_metadata(Entity(), Message()))
        self.assertEqual(username, "alice")
        self.assertEqual(display_name, "Alice")

    def test_resolve_sender_metadata_falls_back_to_get_sender_when_needed(self) -> None:
        class Sender:
            username = "alice"
            first_name = "Alice"
            last_name = "Jones"

        class Entity:
            id = 1
            username = "refugecard"
            title = "Refuge"

        class Message:
            post = False
            sender_id = 42
            sender = None

            async def get_sender(self):
                return Sender()

        username, display_name = asyncio.run(telegram_history_client.resolve_sender_metadata(Entity(), Message()))
        self.assertEqual(username, "alice")
        self.assertEqual(display_name, "Alice Jones")

    def test_sync_one_channel_mark_read_uses_seen_range_even_when_messages_already_exist(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)

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
            default_channels=[],
        )

        class Entity:
            id = 1
            username = "vcnews"
            title = "vc.ru"
            access_hash = None

        class Message:
            def __init__(self, message_id: int) -> None:
                self.id = message_id
                self.message = f"message {message_id}"
                self.date = None
                self.edit_date = None
                self.sender_id = Entity.id
                self.post = True
                self.views = None
                self.forwards = None
                self.replies = None
                self.media = None
                self.grouped_id = None
                self.sender = None

        existing = Message(10)
        telegram_history_client.upsert_channel(conn, Entity())
        telegram_history_client.upsert_message(conn, Entity(), existing, "vcnews", "vc.ru", None, None)
        conn.commit()

        messages = [Message(10), Message(9)]
        mark_calls: list[tuple[int | None, int | None]] = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get_entity(self, channel):
                return Entity()

            def iter_messages(self, entity, limit):
                async def generator():
                    for item in messages:
                        yield item
                return generator()

            async def send_read_acknowledge(self, entity, max_id=None):
                mark_calls.append((entity.id, max_id))

        args = types.SimpleNamespace(limit=10, auth_mode="user", download_media=False, ocr=False, mark_read=True)
        original_open = telegram_history_client.open_telethon_client
        original_current_read = telegram_history_client.current_read_inbox_max_id
        try:
            async def fake_open(runtime, auth_mode):
                return FakeClient()

            async def fake_current_read(client, entity):
                return 5

            telegram_history_client.open_telethon_client = fake_open
            telegram_history_client.current_read_inbox_max_id = fake_current_read
            result = asyncio.run(telegram_history_client.sync_one_channel(conn, runtime, args, "update", "@vcnews"))
        finally:
            telegram_history_client.open_telethon_client = original_open
            telegram_history_client.current_read_inbox_max_id = original_current_read

        self.assertEqual(result["processed_messages"], 0)
        self.assertTrue(result["marked_read"])
        self.assertEqual(result["current_read_max_id"], 5)
        self.assertEqual(result["marked_read_from"], 6)
        self.assertEqual(result["marked_read_until"], 10)
        self.assertEqual(mark_calls, [(1, 10)])

    def test_sync_one_channel_mark_read_skips_ack_when_range_already_read(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)

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
            default_channels=[],
        )

        class Entity:
            id = 1
            username = "vcnews"
            title = "vc.ru"
            access_hash = None

        class Message:
            def __init__(self, message_id: int) -> None:
                self.id = message_id
                self.message = f"message {message_id}"
                self.date = None
                self.edit_date = None
                self.sender_id = Entity.id
                self.post = True
                self.views = None
                self.forwards = None
                self.replies = None
                self.media = None
                self.grouped_id = None
                self.sender = None

        messages = [Message(10), Message(9)]
        mark_calls: list[tuple[int | None, int | None]] = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get_entity(self, channel):
                return Entity()

            def iter_messages(self, entity, limit):
                async def generator():
                    for item in messages:
                        yield item
                return generator()

            async def send_read_acknowledge(self, entity, max_id=None):
                mark_calls.append((entity.id, max_id))

        args = types.SimpleNamespace(limit=10, auth_mode="user", download_media=False, ocr=False, mark_read=True)
        original_open = telegram_history_client.open_telethon_client
        original_current_read = telegram_history_client.current_read_inbox_max_id
        try:
            async def fake_open(runtime, auth_mode):
                return FakeClient()

            async def fake_current_read(client, entity):
                return 10

            telegram_history_client.open_telethon_client = fake_open
            telegram_history_client.current_read_inbox_max_id = fake_current_read
            result = asyncio.run(telegram_history_client.sync_one_channel(conn, runtime, args, "tail", "@vcnews"))
        finally:
            telegram_history_client.open_telethon_client = original_open
            telegram_history_client.current_read_inbox_max_id = original_current_read

        self.assertEqual(result["processed_messages"], 2)
        self.assertEqual(result["current_read_max_id"], 10)
        self.assertFalse(result["marked_read"])
        self.assertIsNone(result["marked_read_from"])
        self.assertIsNone(result["marked_read_until"])
        self.assertEqual(mark_calls, [])

    def test_sync_one_channel_mark_read_uses_previously_processed_boundary_not_newly_arrived_messages(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)

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
            default_channels=[],
        )

        class Entity:
            id = 1
            username = "vcnews"
            title = "vc.ru"
            access_hash = None

        class Message:
            def __init__(self, message_id: int) -> None:
                self.id = message_id
                self.message = f"message {message_id}"
                self.date = None
                self.edit_date = None
                self.sender_id = Entity.id
                self.post = True
                self.views = None
                self.forwards = None
                self.replies = None
                self.media = None
                self.grouped_id = None
                self.sender = None

        telegram_history_client.upsert_channel(conn, Entity())
        telegram_history_client.upsert_message(conn, Entity(), Message(10), "vcnews", "vc.ru", None, None)
        telegram_history_client.update_sync_state(
            conn,
            Entity.id,
            last_tail_message_id=10,
            last_tail_at="2026-03-17T00:00:00+00:00",
            last_error=None,
        )
        conn.commit()

        messages = [Message(12), Message(11), Message(10)]
        mark_calls: list[tuple[int | None, int | None]] = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get_entity(self, channel):
                return Entity()

            def iter_messages(self, entity, limit):
                async def generator():
                    for item in messages:
                        yield item
                return generator()

            async def send_read_acknowledge(self, entity, max_id=None):
                mark_calls.append((entity.id, max_id))

        args = types.SimpleNamespace(limit=10, auth_mode="user", download_media=False, ocr=False, mark_read=True)
        original_open = telegram_history_client.open_telethon_client
        original_current_read = telegram_history_client.current_read_inbox_max_id
        try:
            async def fake_open(runtime, auth_mode):
                return FakeClient()

            async def fake_current_read(client, entity):
                return 8

            telegram_history_client.open_telethon_client = fake_open
            telegram_history_client.current_read_inbox_max_id = fake_current_read
            result = asyncio.run(telegram_history_client.sync_one_channel(conn, runtime, args, "update", "@vcnews"))
        finally:
            telegram_history_client.open_telethon_client = original_open
            telegram_history_client.current_read_inbox_max_id = original_current_read

        self.assertEqual(result["processed_messages"], 2)
        self.assertTrue(result["marked_read"])
        self.assertEqual(result["current_read_max_id"], 8)
        self.assertEqual(result["marked_read_from"], 9)
        self.assertEqual(result["marked_read_until"], 10)
        self.assertEqual(mark_calls, [(1, 10)])

    def test_sync_one_channel_applies_since_until_filters(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)

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
            default_channels=[],
        )

        class Entity:
            id = 1
            username = "vcnews"
            title = "vc.ru"
            access_hash = None

        class Message:
            def __init__(self, message_id: int, date_value: datetime) -> None:
                self.id = message_id
                self.message = f"message {message_id}"
                self.date = date_value
                self.edit_date = None
                self.sender_id = Entity.id
                self.post = True
                self.views = None
                self.forwards = None
                self.replies = None
                self.media = None
                self.grouped_id = None
                self.sender = None

        messages = [
            Message(12, datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)),
            Message(11, datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)),
            Message(10, datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)),
            Message(9, datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)),
        ]

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get_entity(self, channel):
                return Entity()

            def iter_messages(self, entity, limit):
                async def generator():
                    for item in messages:
                        yield item
                return generator()

        args = types.SimpleNamespace(
            limit=10,
            auth_mode="user",
            download_media=False,
            ocr=False,
            mark_read=False,
            since="2026-03-15",
            until="2026-03-16",
        )
        original_open = telegram_history_client.open_telethon_client
        try:
            async def fake_open(runtime, auth_mode):
                return FakeClient()

            telegram_history_client.open_telethon_client = fake_open
            result = asyncio.run(telegram_history_client.sync_one_channel(conn, runtime, args, "tail", "@vcnews"))
        finally:
            telegram_history_client.open_telethon_client = original_open

        self.assertEqual(result["processed_messages"], 2)
        self.assertEqual(result["since"], "2026-03-15")
        self.assertEqual(result["until"], "2026-03-16")
        stored_ids = [row["message_id"] for row in conn.execute("SELECT message_id FROM messages ORDER BY message_id DESC")]
        self.assertEqual(stored_ids, [11, 10])

    def test_iter_pending_ocr_applies_since_until_filters(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        telegram_history_client.init_db(conn)
        first = "2026-03-15T10:00:00+00:00"
        second = "2026-03-16T10:00:00+00:00"
        third = "2026-03-17T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO channels(channel_id, access_hash, username, title, channel_type, raw_json, first_seen_at, last_seen_at)
            VALUES (1, '', 'vcnews', 'vc.ru', 'Channel', '{}', ?, ?)
            """,
            (first, third),
        )
        conn.execute(
            """
            INSERT INTO messages(channel_id, message_id, grouped_id, date_utc, edit_date_utc, sender_id, sender_username, sender_display_name, text, views, forwards, replies, has_media, media_kind, raw_json, content_hash, imported_at)
            VALUES
                (1, 1, NULL, ?, NULL, NULL, NULL, NULL, 'older', NULL, NULL, NULL, 1, 'photo', '{}', 'h1', ?),
                (1, 2, NULL, ?, NULL, NULL, NULL, NULL, 'mid', NULL, NULL, NULL, 1, 'photo', '{}', 'h2', ?),
                (1, 3, NULL, ?, NULL, NULL, NULL, NULL, 'newer', NULL, NULL, NULL, 1, 'photo', '{}', 'h3', ?)
            """,
            (first, first, second, second, third, third),
        )
        conn.execute(
            """
            INSERT INTO media_assets(channel_id, message_id, ordinal, media_kind, local_path, mime_type, file_size, ocr_status, ocr_text, created_at)
            VALUES
                (1, 1, 0, 'photo', '/tmp/1.jpg', 'photo', 100, 'pending', NULL, ?),
                (1, 2, 0, 'photo', '/tmp/2.jpg', 'photo', 100, 'pending', NULL, ?),
                (1, 3, 0, 'photo', '/tmp/3.jpg', 'photo', 100, 'pending', NULL, ?)
            """,
            (first, second, third),
        )
        rows = telegram_history_client.iter_pending_ocr(
            conn,
            10,
            channel_id=1,
            since="2026-03-15",
            until="2026-03-16",
        )
        self.assertEqual([(row["channel_id"], row["message_id"]) for row in rows], [(1, 1), (1, 2)])

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
