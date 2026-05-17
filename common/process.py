from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import signal
import tomllib


COMMON_ROOT = Path(__file__).resolve().parent
DEFAULT_PROCESS_CONFIG_PATH = COMMON_ROOT / "config" / "process.toml"


@dataclass(frozen=True, slots=True)
class ProcessConfig:
    default_run_total_timeout_seconds: int
    default_termination_grace_seconds: int
    poll_interval_seconds: float
    timeout_exit_code: int
    term_signal: str
    kill_signal: str


def load_process_config(config_path: Path | None = None) -> ProcessConfig:
    path = config_path or DEFAULT_PROCESS_CONFIG_PATH
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    section = raw.get("process")
    if not isinstance(section, dict):
        raise KeyError(f"Missing [process] config section in {path}")

    default_run_total_timeout_seconds = max(1, int(section.get("default_run_total_timeout_seconds", 1800)))
    default_termination_grace_seconds = max(1, int(section.get("default_termination_grace_seconds", 10)))
    poll_interval_seconds = max(0.05, float(section.get("poll_interval_seconds", 1.0)))
    timeout_exit_code = int(section.get("timeout_exit_code", 124))
    term_signal = str(section.get("term_signal", "TERM")).strip().upper()
    kill_signal = str(section.get("kill_signal", "KILL")).strip().upper()

    resolve_signal(term_signal)
    resolve_signal(kill_signal)

    return ProcessConfig(
        default_run_total_timeout_seconds=default_run_total_timeout_seconds,
        default_termination_grace_seconds=default_termination_grace_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout_exit_code=timeout_exit_code,
        term_signal=term_signal,
        kill_signal=kill_signal,
    )


def resolve_signal(name: str) -> signal.Signals:
    normalized = name.strip().upper()
    if not normalized.startswith("SIG"):
        normalized = f"SIG{normalized}"
    try:
        return signal.Signals[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported signal name: {name}") from exc
