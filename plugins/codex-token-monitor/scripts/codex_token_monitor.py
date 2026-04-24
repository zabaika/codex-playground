#!/usr/bin/env python3
"""Realtime Codex token monitor for rollout JSONL sessions."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import textwrap
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_HISTORY_LIMIT = 20
ANSI_CLEAR = "\033[2J\033[H"
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "TokenUsage | None":
        if not payload:
            return None
        return cls(
            input_tokens=int(payload.get("input_tokens", 0)),
            cached_input_tokens=int(payload.get("cached_input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            reasoning_output_tokens=int(payload.get("reasoning_output_tokens", 0)),
            total_tokens=int(payload.get("total_tokens", 0)),
        )


@dataclass
class RateWindow:
    used_percent: float
    window_minutes: int | None
    resets_at: int | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "RateWindow | None":
        if not payload:
            return None
        return cls(
            used_percent=float(payload.get("used_percent", 0.0)),
            window_minutes=_optional_int(payload.get("window_minutes")),
            resets_at=_optional_int(payload.get("resets_at")),
        )


@dataclass
class RateLimits:
    primary: RateWindow | None = None
    secondary: RateWindow | None = None
    plan_type: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "RateLimits | None":
        if not payload:
            return None
        return cls(
            primary=RateWindow.from_payload(_optional_dict(payload.get("primary"))),
            secondary=RateWindow.from_payload(_optional_dict(payload.get("secondary"))),
            plan_type=_optional_str(payload.get("plan_type")),
        )


@dataclass
class TokenSample:
    timestamp: datetime
    total: TokenUsage | None
    delta: TokenUsage | None
    rate_limits: RateLimits | None


@dataclass
class MonitorState:
    rollout_path: Path
    session_id: str | None = None
    cwd: str | None = None
    thread_name: str | None = None
    event_count: int = 0
    last_event_at: datetime | None = None
    last_token_sample: TokenSample | None = None
    token_samples: deque[TokenSample] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_HISTORY_LIMIT)
    )

    def apply_event(self, event: dict[str, Any]) -> None:
        self.event_count += 1
        event_timestamp = _parse_timestamp(event.get("timestamp"))
        if event_timestamp is not None:
            self.last_event_at = event_timestamp

        event_type = event.get("type")
        payload = _optional_dict(event.get("payload")) or {}

        if event_type == "session_meta":
            self.session_id = _optional_str(payload.get("id"))
            self.cwd = _optional_str(payload.get("cwd"))
            return

        if event_type != "event_msg":
            return

        payload_type = payload.get("type")
        if payload_type == "thread_name_updated":
            self.thread_name = _optional_str(payload.get("thread_name"))
            return
        if payload_type != "token_count":
            return

        info = _optional_dict(payload.get("info"))
        total = TokenUsage.from_payload(_optional_dict(info.get("total_token_usage")) if info else None)
        delta = TokenUsage.from_payload(_optional_dict(info.get("last_token_usage")) if info else None)
        rate_limits = RateLimits.from_payload(_optional_dict(payload.get("rate_limits")))
        sample = TokenSample(
            timestamp=event_timestamp or datetime.now(timezone.utc),
            total=total,
            delta=delta,
            rate_limits=rate_limits,
        )
        self.last_token_sample = sample
        self.token_samples.append(sample)


class RolloutFollower:
    def __init__(self, path: Path, history_limit: int) -> None:
        self.path = path
        self.state = MonitorState(
            rollout_path=path,
            token_samples=deque(maxlen=history_limit),
        )
        self._offset = 0
        self._buffer = ""

    def load_initial(self) -> MonitorState:
        if not self.path.exists():
            return self.state
        text = self.path.read_text(encoding="utf-8")
        self._offset = len(text.encode("utf-8"))
        self._consume_text(text, flush=True)
        return self.state

    def poll(self) -> bool:
        if not self.path.exists():
            return False
        stat_result = self.path.stat()
        if stat_result.st_size < self._offset:
            self._offset = 0
            self._buffer = ""
            self.state = MonitorState(
                rollout_path=self.path,
                token_samples=deque(maxlen=self.state.token_samples.maxlen),
            )
        if stat_result.st_size == self._offset:
            return False
        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(self._offset)
            chunk = handle.read()
            self._offset = handle.tell()
        self._consume_text(chunk, flush=False)
        return bool(chunk)

    def _consume_text(self, text: str, flush: bool) -> None:
        if not text and not flush:
            return
        pending = self._buffer + text
        lines = pending.splitlines(keepends=True)
        self._buffer = ""
        for line in lines:
            if line.endswith("\n"):
                self._consume_line(line.rstrip("\n"))
            else:
                self._buffer = line
        if flush and self._buffer:
            self._consume_line(self._buffer)
            self._buffer = ""

    def _consume_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            self.state.apply_event(event)


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _format_int(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def _format_local_time(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_age_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1:
        return "<1s"
    total_seconds = int(seconds)
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _format_reset(epoch_seconds: int | None) -> str:
    if epoch_seconds is None:
        return "-"
    reset_dt = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone()
    delta_seconds = max(0, epoch_seconds - int(time.time()))
    return f"{reset_dt.strftime('%H:%M:%S %Z')} ({_format_age_seconds(delta_seconds)})"


def _format_reset_minutes(epoch_seconds: int | None) -> str:
    if epoch_seconds is None:
        return "-"
    delta_seconds = max(0, epoch_seconds - int(time.time()))
    minutes = delta_seconds // 60
    return f"{minutes}m"


def _colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color}{text}{ANSI_RESET}"


def _rate_color(used_percent: float) -> str:
    if used_percent >= 85.0:
        return ANSI_RED
    if used_percent >= 60.0:
        return ANSI_YELLOW
    return ANSI_GREEN


def _lag_color(seconds: float | None) -> str:
    if seconds is None:
        return ANSI_DIM
    if seconds >= 300.0:
        return ANSI_RED
    if seconds >= 60.0:
        return ANSI_YELLOW
    return ANSI_GREEN


def _effective_rate_window(window: RateWindow | None) -> RateWindow | None:
    """Project expired windows forward instead of rendering stale usage at 0m.

    `rate_limits` only refresh when Codex emits a new `token_count`. After a
    reset passes, the last authoritative sample is stale. Carrying its old
    `used_percent` forward produces misleading output such as `39% left r 0m`.
    For expired windows, roll the reset forward by whole window intervals and
    clear the stale usage until the next authoritative update arrives.
    """

    if window is None:
        return None
    if window.resets_at is None or not window.window_minutes:
        return window

    now_epoch = int(time.time())
    if now_epoch < window.resets_at:
        return window

    step_seconds = window.window_minutes * 60
    if step_seconds <= 0:
        return RateWindow(
            used_percent=0.0,
            window_minutes=window.window_minutes,
            resets_at=window.resets_at,
        )

    cycles = ((now_epoch - window.resets_at) // step_seconds) + 1
    next_reset = window.resets_at + (cycles * step_seconds)
    return RateWindow(
        used_percent=0.0,
        window_minutes=window.window_minutes,
        resets_at=next_reset,
    )


def _render_rate_window(label: str, window: RateWindow | None, use_color: bool) -> str:
    if window is None:
        return f"{label} -"
    remaining = max(0.0, 100.0 - window.used_percent)
    status = (
        f"{label} {window.used_percent:.1f}% used {remaining:.1f}% left "
        f"reset {_format_reset(window.resets_at)} {f'({window.window_minutes}m)' if window.window_minutes else ''}".strip()
    )
    return _colorize(status, _rate_color(window.used_percent), use_color)


def _render_rate_window_brief(label: str, window: RateWindow | None, use_color: bool) -> str:
    if window is None:
        return f"{label} -"
    remaining = max(0.0, 100.0 - window.used_percent)
    status = f"{label} {remaining:.0f}% left r {_format_reset_minutes(window.resets_at)}"
    return _colorize(status, _rate_color(window.used_percent), use_color)


def _safe_stat_mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def compute_throughput(samples: deque[TokenSample]) -> tuple[str, str]:
    if len(samples) < 2:
        return "-", "-"
    first = samples[0]
    last = samples[-1]
    if first.total is None or last.total is None:
        return "-", "-"
    elapsed_seconds = (last.timestamp - first.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return "-", "-"
    total_tokens_per_min = (last.total.total_tokens - first.total.total_tokens) * 60.0 / elapsed_seconds
    updates_per_min = (len(samples) - 1) * 60.0 / elapsed_seconds
    return f"{total_tokens_per_min:.0f} tok/min", f"{updates_per_min:.1f} upd/min"


def _render_limit_snapshot_age(sample: TokenSample | None, now: datetime, use_color: bool) -> str:
    if sample is None or sample.rate_limits is None:
        return "limits -"
    age_seconds = max(0.0, (now - sample.timestamp).total_seconds())
    return _colorize(
        f"limits {_format_age_seconds(age_seconds)}",
        _lag_color(age_seconds),
        use_color,
    )


def build_snapshot_text(state: MonitorState, mode: str = "brief") -> str:
    now = datetime.now(timezone.utc)
    last_sample = state.last_token_sample
    total = last_sample.total if last_sample else None
    delta = last_sample.delta if last_sample else None
    rate_limits = last_sample.rate_limits if last_sample else None
    primary_window = _effective_rate_window(rate_limits.primary) if rate_limits else None
    secondary_window = _effective_rate_window(rate_limits.secondary) if rate_limits else None
    file_mtime = _safe_stat_mtime(state.rollout_path)
    tokens_per_min, updates_per_min = compute_throughput(state.token_samples)
    use_color = sys.stdout.isatty()
    event_lag_seconds = (now - state.last_event_at).total_seconds() if state.last_event_at else None
    file_lag_seconds = (now - file_mtime).total_seconds() if file_mtime else None

    if mode == "full":
        lines = [
            _render_compact_line(
                "age",
                [_render_limit_snapshot_age(last_sample, now, use_color)],
                use_color=use_color,
                width=140,
            ),
            _render_compact_line(
                "session",
                [
                    state.thread_name or "-",
                    f"sid {state.session_id or '-'}",
                    state.rollout_path.name,
                ],
                use_color=use_color,
                width=140,
            ),
            _render_compact_line(
                "tokens",
                [
                    f"in {_format_int(total.input_tokens if total else None)}",
                    f"cache {_format_int(total.cached_input_tokens if total else None)}",
                    f"out {_format_int(total.output_tokens if total else None)}",
                    f"rsn {_format_int(total.reasoning_output_tokens if total else None)}",
                    f"total {_format_int(total.total_tokens if total else None)}",
                ],
                use_color=use_color,
                width=140,
            ),
            _render_compact_line(
                "delta",
                [
                    f"in +{_format_int(delta.input_tokens if delta else None)}",
                    f"cache +{_format_int(delta.cached_input_tokens if delta else None)}",
                    f"out +{_format_int(delta.output_tokens if delta else None)}",
                    f"rsn +{_format_int(delta.reasoning_output_tokens if delta else None)}",
                    f"total +{_format_int(delta.total_tokens if delta else None)}",
                ],
                use_color=use_color,
                width=140,
            ),
            _render_compact_line(
                "limits",
                [
                    f"plan {rate_limits.plan_type if rate_limits and rate_limits.plan_type else '-'}",
                    _render_rate_window("day", primary_window, use_color),
                    _render_rate_window("week", secondary_window, use_color),
                ],
                use_color=use_color,
                width=140,
            ),
            _render_compact_line(
                "time",
                [
                    _colorize(
                        f"event {_format_age_seconds(event_lag_seconds)}",
                        _lag_color(event_lag_seconds),
                        use_color,
                    ),
                    _colorize(
                        f"file {_format_age_seconds(file_lag_seconds)}",
                        _lag_color(file_lag_seconds),
                        use_color,
                    ),
                    f"events {state.event_count}",
                    f"tok_events {len(state.token_samples)}",
                    tokens_per_min,
                    updates_per_min,
                ],
                use_color=use_color,
                width=140,
            ),
        ]
    else:
        lines = [
            _render_compact_line(
                "age",
                [_render_limit_snapshot_age(last_sample, now, use_color)],
                use_color=use_color,
                width=80,
            ),
            _render_compact_line(
                "session",
                [state.thread_name or "-"],
                use_color=use_color,
                width=80,
            ),
            _render_compact_line(
                "delta",
                [
                    f"in +{_format_int(delta.input_tokens if delta else None)}",
                    f"out +{_format_int(delta.output_tokens if delta else None)}",
                    f"total +{_format_int(delta.total_tokens if delta else None)}",
                ],
                use_color=use_color,
                width=80,
            ),
            _render_compact_line(
                "limits",
                [
                    _render_rate_window_brief("day", primary_window, use_color),
                    _render_rate_window_brief("week", secondary_window, use_color),
                ],
                use_color=use_color,
                width=80,
            ),
        ]
    return "\n".join(lines)


def _render_compact_line(label: str, parts: list[str], use_color: bool, width: int) -> str:
    prefix = _colorize(f"{label:<7}", ANSI_CYAN, use_color)
    text = " | ".join(part for part in parts if part)
    available_width = max(20, width - 8)
    wrapped = textwrap.wrap(text, width=available_width, subsequent_indent=" " * 10) or [""]
    rendered = [f"{prefix} {wrapped[0]}"]
    rendered.extend(f"{' ' * 8}{line}" for line in wrapped[1:])
    return "\n".join(rendered)


def discover_rollout(codex_home: Path, selected_cwd: Path | None) -> Path | None:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return None
    candidates = sorted(
        sessions_root.glob("**/rollout-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    if selected_cwd is None:
        return candidates[0]
    matching: list[Path] = []
    for candidate in candidates:
        session_cwd = read_session_cwd(candidate)
        if _cwd_matches(session_cwd, selected_cwd):
            matching.append(candidate)
    if matching:
        return matching[0]
    return candidates[0]


def read_session_cwd(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _ in range(10):
                line = handle.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "session_meta":
                    continue
                payload = _optional_dict(event.get("payload")) or {}
                return _optional_str(payload.get("cwd"))
    except OSError:
        return None
    return None


def _cwd_matches(session_cwd: str | None, selected_cwd: Path) -> bool:
    if not session_cwd:
        return False
    raw_selected = str(selected_cwd)
    resolved_selected = str(selected_cwd.resolve())
    if session_cwd in {raw_selected, resolved_selected}:
        return True
    session_path = Path(session_cwd)
    if not session_path.exists():
        return False
    try:
        return session_path.samefile(selected_cwd)
    except OSError:
        return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Pin a specific rollout JSONL file.")
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Preferred project cwd used to resolve the active rollout file.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Codex home directory. Defaults to $CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval in seconds for follow mode.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help="Number of recent token_count samples kept for throughput calculations.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print one snapshot and exit.",
    )
    parser.add_argument(
        "--mode",
        choices=("brief", "full"),
        default="brief",
        help="Render mode. brief is optimized for constant use; full shows extra detail.",
    )
    return parser.parse_args(argv)


def build_follower(args: argparse.Namespace) -> RolloutFollower | None:
    target = args.file
    if target is None:
        target = discover_rollout(args.codex_home, args.cwd)
    if target is None:
        return None
    follower = RolloutFollower(target, history_limit=args.history_limit)
    follower.load_initial()
    return follower


def run_once(args: argparse.Namespace) -> int:
    follower = build_follower(args)
    if follower is None:
        print("No rollout file found.", file=sys.stderr)
        return 1
    print(build_snapshot_text(follower.state, mode=args.mode))
    return 0


def run_follow(args: argparse.Namespace) -> int:
    stop_requested = False

    def _request_stop(signum: int, frame: Any) -> None:
        del signum, frame
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    follower = build_follower(args)
    if follower is None:
        print("No rollout file found for the selected cwd yet.", file=sys.stderr)
        return 1

    while not stop_requested:
        if args.file is None:
            latest = discover_rollout(args.codex_home, args.cwd)
            if latest is not None and latest != follower.path:
                follower = RolloutFollower(latest, history_limit=args.history_limit)
                follower.load_initial()
        follower.poll()
        if sys.stdout.isatty():
            sys.stdout.write(ANSI_CLEAR)
        sys.stdout.write(build_snapshot_text(follower.state, mode=args.mode))
        sys.stdout.write("\n")
        sys.stdout.flush()
        time.sleep(max(0.1, args.poll_interval))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.once:
        return run_once(args)
    return run_follow(args)


if __name__ == "__main__":
    raise SystemExit(main())
