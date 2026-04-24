import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "codex_token_monitor.py"
SPEC = importlib.util.spec_from_file_location("codex_token_monitor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CodexTokenMonitorTests(unittest.TestCase):
    def _make_sample(
        self,
        *,
        total_tokens: int = 123,
        input_tokens: int = 100,
        cached_input_tokens: int = 10,
        output_tokens: int = 8,
        reasoning_output_tokens: int = 5,
        delta_total_tokens: int = 23,
        delta_input_tokens: int = 20,
        delta_cached_input_tokens: int = 2,
        delta_output_tokens: int = 1,
        delta_reasoning_output_tokens: int = 0,
        primary_used_percent: float | None = None,
        primary_reset_at: int | None = None,
        primary_window_minutes: int | None = None,
        secondary_used_percent: float | None = None,
        secondary_reset_at: int | None = None,
        secondary_window_minutes: int | None = None,
        plan_type: str | None = "plus",
    ) -> object:
        rate_limits = None
        if (
            primary_used_percent is not None
            or secondary_used_percent is not None
            or plan_type is not None
        ):
            rate_limits = MODULE.RateLimits(
                primary=(
                    MODULE.RateWindow(
                        used_percent=primary_used_percent,
                        resets_at=primary_reset_at,
                        window_minutes=primary_window_minutes,
                    )
                    if primary_used_percent is not None
                    else None
                ),
                secondary=(
                    MODULE.RateWindow(
                        used_percent=secondary_used_percent,
                        resets_at=secondary_reset_at,
                        window_minutes=secondary_window_minutes,
                    )
                    if secondary_used_percent is not None
                    else None
                ),
                plan_type=plan_type,
            )
        return MODULE.TokenSample(
            timestamp=MODULE.datetime(2026, 4, 20, 12, 0, tzinfo=MODULE.timezone.utc),
            total=MODULE.TokenUsage(
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_output_tokens=reasoning_output_tokens,
            ),
            delta=MODULE.TokenUsage(
                total_tokens=delta_total_tokens,
                input_tokens=delta_input_tokens,
                cached_input_tokens=delta_cached_input_tokens,
                output_tokens=delta_output_tokens,
                reasoning_output_tokens=delta_reasoning_output_tokens,
            ),
            rate_limits=rate_limits,
        )

    def _make_state(self, sample: object) -> object:
        state = MODULE.MonitorState(rollout_path=pathlib.Path("/tmp/rollout.jsonl"))
        state.session_id = "session-1"
        state.cwd = "/tmp/project"
        state.thread_name = "Thread"
        state.event_count = 3
        state.last_event_at = sample.timestamp
        state.last_token_sample = sample
        state.token_samples.append(sample)
        return state

    def test_rollout_file_parses_latest_token_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout = pathlib.Path(tmpdir) / "rollout.jsonl"
            lines = [
                {
                    "timestamp": "2026-04-20T12:14:03.930Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-1",
                        "cwd": "/tmp/project",
                    },
                },
                {
                    "timestamp": "2026-04-20T12:14:05.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_name_updated",
                        "thread_name": "Token monitor thread",
                    },
                },
                {
                    "timestamp": "2026-04-20T12:14:06.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": None,
                        "rate_limits": {
                            "primary": {
                                "used_percent": 48.0,
                                "window_minutes": 300,
                                "resets_at": 1776690035,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-04-20T12:14:16.141Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 19979,
                                "cached_input_tokens": 7552,
                                "output_tokens": 517,
                                "reasoning_output_tokens": 240,
                                "total_tokens": 20496,
                            },
                            "last_token_usage": {
                                "input_tokens": 19979,
                                "cached_input_tokens": 7552,
                                "output_tokens": 517,
                                "reasoning_output_tokens": 240,
                                "total_tokens": 20496,
                            },
                        },
                        "rate_limits": {
                            "primary": {
                                "used_percent": 48.0,
                                "window_minutes": 300,
                                "resets_at": 1776690035,
                            },
                            "secondary": {
                                "used_percent": 10.0,
                                "window_minutes": 10080,
                                "resets_at": 1777198320,
                            },
                            "plan_type": "plus",
                        },
                    },
                },
            ]
            rollout.write_text(
                "\n".join(json.dumps(line) for line in lines) + "\n",
                encoding="utf-8",
            )

            follower = MODULE.RolloutFollower(rollout, history_limit=10)
            state = follower.load_initial()

            self.assertEqual(state.session_id, "session-1")
            self.assertEqual(state.cwd, "/tmp/project")
            self.assertEqual(state.thread_name, "Token monitor thread")
            self.assertEqual(state.event_count, 4)
            self.assertEqual(len(state.token_samples), 2)
            self.assertEqual(state.last_token_sample.total.total_tokens, 20496)
            self.assertEqual(state.last_token_sample.delta.total_tokens, 20496)
            self.assertEqual(state.last_token_sample.rate_limits.primary.used_percent, 48.0)

    def test_poll_handles_partial_lines_and_truncate_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout = pathlib.Path(tmpdir) / "rollout.jsonl"
            initial_lines = [
                {
                    "timestamp": "2026-04-20T12:14:03.930Z",
                    "type": "session_meta",
                    "payload": {"id": "session-1", "cwd": "/tmp/project"},
                },
                {
                    "timestamp": "2026-04-20T12:14:16.141Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 100},
                            "last_token_usage": {"total_tokens": 100},
                        },
                    },
                },
            ]
            rollout.write_text(
                "\n".join(json.dumps(line) for line in initial_lines) + "\n",
                encoding="utf-8",
            )

            follower = MODULE.RolloutFollower(rollout, history_limit=10)
            follower.load_initial()

            with rollout.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-04-20T12:14:20.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "thread_name_updated",
                                "thread_name": "Live thread",
                            },
                        }
                    )
                )
            self.assertTrue(follower.poll())
            self.assertIsNone(follower.state.thread_name)
            self.assertEqual(follower.state.event_count, 2)

            with rollout.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n"
                    + json.dumps(
                        {
                            "timestamp": "2026-04-20T12:14:25.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"total_tokens": 150},
                                    "last_token_usage": {"total_tokens": 50},
                                },
                            },
                        }
                    )
                    + "\n"
                )
            self.assertTrue(follower.poll())
            self.assertEqual(follower.state.thread_name, "Live thread")
            self.assertEqual(follower.state.event_count, 4)
            self.assertEqual(follower.state.last_token_sample.total.total_tokens, 150)

            reset_lines = [
                {
                    "timestamp": "2026-04-20T12:15:03.930Z",
                    "type": "session_meta",
                    "payload": {"id": "session-2", "cwd": "/tmp/project"},
                },
                {
                    "timestamp": "2026-04-20T12:15:16.141Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 30},
                            "last_token_usage": {"total_tokens": 30},
                        },
                    },
                },
            ]
            rollout.write_text(
                "\n".join(json.dumps(line) for line in reset_lines) + "\n",
                encoding="utf-8",
            )

            self.assertTrue(follower.poll())
            self.assertEqual(follower.state.session_id, "session-2")
            self.assertEqual(follower.state.event_count, 2)
            self.assertEqual(len(follower.state.token_samples), 1)
            self.assertEqual(follower.state.last_token_sample.total.total_tokens, 30)

    def test_discover_rollout_prefers_matching_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = pathlib.Path(tmpdir)
            sessions_root = codex_home / "sessions" / "2026" / "04" / "20"
            sessions_root.mkdir(parents=True)
            older = sessions_root / "rollout-a.jsonl"
            newer = sessions_root / "rollout-b.jsonl"
            older.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"cwd": "/tmp/project-a"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            newer.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"cwd": "/tmp/project-b"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            older_ts = 1_700_000_000
            newer_ts = older_ts + 10
            os.utime(older, (older_ts, older_ts))
            os.utime(newer, (newer_ts, newer_ts))

            selected = MODULE.discover_rollout(codex_home, pathlib.Path("/tmp/project-a"))

            self.assertEqual(selected, older)

    def test_discover_rollout_falls_back_to_newest_when_no_cwd_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = pathlib.Path(tmpdir)
            sessions_root = codex_home / "sessions" / "2026" / "04" / "20"
            sessions_root.mkdir(parents=True)
            older = sessions_root / "rollout-a.jsonl"
            newer = sessions_root / "rollout-b.jsonl"
            older.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"cwd": "/tmp/project-a"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            newer.write_text("{invalid json}\n", encoding="utf-8")

            older_ts = 1_700_000_000
            newer_ts = older_ts + 10
            os.utime(older, (older_ts, older_ts))
            os.utime(newer, (newer_ts, newer_ts))

            selected = MODULE.discover_rollout(codex_home, pathlib.Path("/tmp/project-z"))

            self.assertEqual(selected, newer)

    def test_read_session_cwd_skips_invalid_json_and_non_session_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout = pathlib.Path(tmpdir) / "rollout.jsonl"
            rollout.write_text(
                "{invalid}\n"
                + json.dumps({"type": "event_msg", "payload": {"type": "noop"}})
                + "\n"
                + json.dumps({"type": "session_meta", "payload": {"cwd": "/tmp/project"}})
                + "\n",
                encoding="utf-8",
            )

            session_cwd = MODULE.read_session_cwd(rollout)

            self.assertEqual(session_cwd, "/tmp/project")

    def test_build_follower_falls_back_to_session_index_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = pathlib.Path(tmpdir)
            sessions_root = codex_home / "sessions" / "2026" / "04" / "24"
            sessions_root.mkdir(parents=True)
            rollout = sessions_root / "rollout-2026-04-24T09-32-20-session-1.jsonl"
            rollout.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-04-24T09:32:20.330Z",
                                "type": "session_meta",
                                "payload": {"id": "session-1", "cwd": "/tmp/project"},
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-04-24T09:32:20.637Z",
                                "type": "event_msg",
                                "payload": {
                                    "type": "token_count",
                                    "info": {
                                        "total_token_usage": {"total_tokens": 123},
                                        "last_token_usage": {"total_tokens": 23},
                                    },
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "session-1",
                        "thread_name": "Fallback thread",
                        "updated_at": "2026-04-24T09:32:21.000Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            args = MODULE.parse_args(
                [
                    "--codex-home",
                    str(codex_home),
                    "--cwd",
                    "/tmp/project",
                    "--once",
                ]
            )

            follower = MODULE.build_follower(args)

            self.assertIsNotNone(follower)
            assert follower is not None
            self.assertEqual(follower.state.thread_name, "Fallback thread")

    def test_build_snapshot_text_brief_contains_expected_blocks(self) -> None:
        sample = self._make_sample(
            primary_used_percent=48.0,
            primary_reset_at=1_776_686_435,
            primary_window_minutes=300,
            secondary_used_percent=10.0,
            secondary_reset_at=1_776_687_635,
            secondary_window_minutes=10080,
        )
        state = self._make_state(sample)

        with mock.patch.object(MODULE.time, "time", return_value=1_776_685_835):
            rendered = MODULE.build_snapshot_text(state, mode="brief")

        self.assertTrue(rendered.splitlines()[0].startswith("age"))
        self.assertIn("age     limits ", rendered)
        self.assertIn("session", rendered)
        self.assertIn("delta", rendered)
        self.assertIn("limits", rendered)
        self.assertIn("day", rendered)
        self.assertIn("week", rendered)
        self.assertIn("Thread", rendered)
        self.assertIn("day 52% left r 10m", rendered)
        self.assertIn("week 90% left r 30m", rendered)
        self.assertNotIn("time", rendered)
        self.assertNotIn("sid", rendered)
        self.assertNotIn("tokens", rendered)
        self.assertNotIn("cache", rendered)
        self.assertNotIn("rsn", rendered)
        for line in rendered.splitlines():
            self.assertLessEqual(len(line), 80)

    def test_build_snapshot_text_full_contains_time(self) -> None:
        sample = self._make_sample(
            primary_used_percent=48.0,
            primary_reset_at=1_776_686_435,
            primary_window_minutes=300,
            secondary_used_percent=10.0,
            secondary_reset_at=1_776_687_635,
            secondary_window_minutes=10080,
        )
        state = self._make_state(sample)

        with mock.patch.object(MODULE.time, "time", return_value=1_776_685_835):
            rendered = MODULE.build_snapshot_text(state, mode="full")

        self.assertTrue(rendered.splitlines()[0].startswith("age"))
        self.assertIn("age     limits ", rendered)
        self.assertIn("time", rendered)
        self.assertIn("sid session-1", rendered)
        self.assertIn("plan plus", rendered)
        self.assertIn("day 48.0% used 52.0% left reset", rendered)
        self.assertIn("week 10.0% used 90.0% left reset", rendered)
        self.assertIn("(300m)", rendered)
        self.assertIn("(10080m)", rendered)

    def test_build_snapshot_text_rolls_expired_rate_window_forward_in_brief(self) -> None:
        sample = self._make_sample(
            primary_used_percent=61.0,
            primary_reset_at=1_776_686_435,
            primary_window_minutes=300,
            secondary_used_percent=14.0,
            secondary_reset_at=1_776_700_835,
            secondary_window_minutes=10080,
        )
        state = self._make_state(sample)

        with mock.patch.object(MODULE.time, "time", return_value=1_776_686_435):
            rendered = MODULE.build_snapshot_text(state, mode="brief")

        self.assertIn("day 100% left r 300m", rendered)
        self.assertIn("week 86% left r 240m", rendered)
        self.assertNotIn("day 39% left r 0m", rendered)

    def test_build_snapshot_text_rolls_expired_rate_window_forward_in_full(self) -> None:
        sample = self._make_sample(
            primary_used_percent=61.0,
            primary_reset_at=1_776_686_435,
            primary_window_minutes=300,
        )
        state = self._make_state(sample)

        with mock.patch.object(MODULE.time, "time", return_value=1_776_686_435):
            rendered = MODULE.build_snapshot_text(state, mode="full")

        self.assertIn("day 0.0% used 100.0% left reset", rendered)
        self.assertIn("(300m)", rendered)

    def test_build_snapshot_text_tty_uses_ansi_colors_for_thresholds(self) -> None:
        sample = self._make_sample(
            primary_used_percent=90.0,
            primary_reset_at=1_776_686_435,
            primary_window_minutes=300,
            secondary_used_percent=65.0,
            secondary_reset_at=1_776_687_635,
            secondary_window_minutes=10080,
        )
        state = self._make_state(sample)
        state.last_event_at = MODULE.datetime.now(MODULE.timezone.utc) - timedelta(seconds=45)

        with mock.patch.object(MODULE.sys.stdout, "isatty", return_value=True), mock.patch.object(
            MODULE.time, "time", return_value=1_776_685_835
        ):
            rendered = MODULE.build_snapshot_text(state, mode="full")

        self.assertIn(MODULE.ANSI_CYAN, rendered)
        self.assertIn(MODULE.ANSI_RED, rendered)
        self.assertIn(MODULE.ANSI_YELLOW, rendered)

    def test_color_threshold_helpers_cover_boundaries(self) -> None:
        self.assertEqual(MODULE._rate_color(59.9), MODULE.ANSI_GREEN)
        self.assertEqual(MODULE._rate_color(60.0), MODULE.ANSI_YELLOW)
        self.assertEqual(MODULE._rate_color(85.0), MODULE.ANSI_RED)
        self.assertEqual(MODULE._lag_color(None), MODULE.ANSI_DIM)
        self.assertEqual(MODULE._lag_color(59.9), MODULE.ANSI_GREEN)
        self.assertEqual(MODULE._lag_color(60.0), MODULE.ANSI_YELLOW)
        self.assertEqual(MODULE._lag_color(300.0), MODULE.ANSI_RED)

    def test_parse_args_defaults_to_brief_and_accepts_full(self) -> None:
        default_args = MODULE.parse_args([])
        full_args = MODULE.parse_args(["--mode", "full", "--once"])

        self.assertEqual(default_args.mode, "brief")
        self.assertFalse(default_args.once)
        self.assertEqual(full_args.mode, "full")
        self.assertTrue(full_args.once)
