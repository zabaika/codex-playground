from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import sqlite as common_sqlite


class SqliteHelperTests(unittest.TestCase):
    def test_load_sqlite_config_from_default_bundle(self) -> None:
        config = common_sqlite.load_sqlite_config()

        self.assertEqual(config.busy_timeout_ms, 5000)
        self.assertEqual(config.journal_mode, "WAL")
        self.assertEqual(config.synchronous, "NORMAL")
        self.assertTrue(config.foreign_keys)
        self.assertTrue(config.autocommit)

    def test_connect_sqlite_applies_runtime_pragmas(self) -> None:
        conn = common_sqlite.connect_sqlite(":memory:")
        try:
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(foreign_keys, 1)
        self.assertEqual(busy_timeout, 5000)

    def test_write_tx_rolls_back_on_error(self) -> None:
        conn = common_sqlite.connect_sqlite(":memory:")
        try:
            conn.execute("CREATE TABLE sample(value INTEGER NOT NULL)")
            with self.assertRaises(RuntimeError):
                with common_sqlite.write_tx(conn):
                    conn.execute("INSERT INTO sample(value) VALUES (1)")
                    raise RuntimeError("boom")
            count = conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
