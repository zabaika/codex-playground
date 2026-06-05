import unittest

from telegram_shared.config import parse_int_range
from telegram_shared.config import resolve_agent_stats_row_limit
from telegram_shared.config import resolve_bridge_text_chunk_size
from telegram_shared.config import resolve_bridge_worker_process_timeout_seconds


class SharedConfigTests(unittest.TestCase):
    def test_parse_int_range_uses_default_for_empty_or_invalid_values(self) -> None:
        self.assertEqual(parse_int_range("", default=3900, min_value=500, max_value=4096), 3900)
        self.assertEqual(parse_int_range("invalid", default=3900, min_value=500, max_value=4096), 3900)

    def test_parse_int_range_clamps_to_bounds(self) -> None:
        self.assertEqual(parse_int_range("100", default=3900, min_value=500, max_value=4096), 500)
        self.assertEqual(parse_int_range("5000", default=3900, min_value=500, max_value=4096), 4096)

    def test_resolve_bridge_text_chunk_size_reads_shared_bridge_config(self) -> None:
        self.assertEqual(resolve_bridge_text_chunk_size({"bridge": {"text_chunk_size": "4000"}}), 4000)
        self.assertEqual(resolve_bridge_text_chunk_size({"bridge": {"text_chunk_size": "5000"}}), 4096)

    def test_resolve_agent_stats_row_limit_reads_shared_bridge_config(self) -> None:
        self.assertEqual(resolve_agent_stats_row_limit({"bridge": {"agent_stats_row_limit": "150"}}), 150)
        self.assertEqual(resolve_agent_stats_row_limit({"bridge": {"agent_stats_row_limit": "5"}}), 20)

    def test_resolve_bridge_worker_process_timeout_seconds_reads_shared_bridge_config(self) -> None:
        self.assertEqual(
            resolve_bridge_worker_process_timeout_seconds({"bridge": {"worker_process_timeout_seconds": "7200"}}),
            7200,
        )
        self.assertEqual(
            resolve_bridge_worker_process_timeout_seconds({"bridge": {"worker_process_timeout_seconds": "10"}}),
            60,
        )


if __name__ == "__main__":
    unittest.main()
