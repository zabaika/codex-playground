from __future__ import annotations

from dataclasses import dataclass
import ctypes
from functools import lru_cache
from pathlib import Path
import signal
import sys
import time
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
    sleep_interruption_threshold_seconds: float = 5.0
    sleep_interruption_exit_code: int = 125


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
    sleep_threshold = float(section.get("sleep_interruption_threshold_seconds", 5.0))
    sleep_exit_code = int(section.get("sleep_interruption_exit_code", 125))
    if sleep_threshold <= 0 or sleep_exit_code in {0, timeout_exit_code}:
        raise ValueError("Sleep interruption requires a positive threshold and a distinct nonzero exit code.")

    resolve_signal(term_signal)
    resolve_signal(kill_signal)

    return ProcessConfig(
        default_run_total_timeout_seconds=default_run_total_timeout_seconds,
        default_termination_grace_seconds=default_termination_grace_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout_exit_code=timeout_exit_code,
        term_signal=term_signal,
        kill_signal=kill_signal,
        sleep_interruption_threshold_seconds=sleep_threshold,
        sleep_interruption_exit_code=sleep_exit_code,
    )


@lru_cache(maxsize=1)
def _mach_clock():
    """Resolve Darwin clocks once; continuous time includes host sleep."""
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    class Timebase(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
    info = Timebase()
    library.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
    library.mach_timebase_info.restype = ctypes.c_int
    if library.mach_timebase_info(ctypes.byref(info)) != 0 or not info.denom:
        raise OSError("Cannot initialize the Darwin continuous clock.")
    for name in ("mach_continuous_time", "mach_absolute_time"):
        function = getattr(library, name)
        function.argtypes = []
        function.restype = ctypes.c_uint64
    return library, info.numer / info.denom / 1_000_000_000


def continuous_time() -> float:
    if sys.platform == "darwin":
        library, factor = _mach_clock()
        return library.mach_continuous_time() * factor
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def awake_time() -> float:
    if sys.platform == "darwin":
        library, factor = _mach_clock()
        return library.mach_absolute_time() * factor
    return time.monotonic()


def sleep_elapsed(started_continuous: float, started_awake: float) -> float:
    return max(0.0, (continuous_time() - started_continuous) - (awake_time() - started_awake))


def resolve_signal(name: str) -> signal.Signals:
    normalized = name.strip().upper()
    if not normalized.startswith("SIG"):
        normalized = f"SIG{normalized}"
    try:
        return signal.Signals[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported signal name: {name}") from exc
