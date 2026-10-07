"""Atomic local state and append-only successful-check history."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Source runs use sibling common/; installed runtimes carry their own copy.
RUNTIME_ROOT = Path(__file__).resolve().parent
SHARED_ROOT = RUNTIME_ROOT if (RUNTIME_ROOT / "common").is_dir() else RUNTIME_ROOT.parent
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from common.json_io import write_json_atomic


class StateError(RuntimeError):
    """Raised when monitor state cannot be read or committed safely."""


DEFAULT_STATE: dict[str, Any] = {
    "request_identity": None,
    "failure_request_identity": None,
    "last_portal_check_started_at": None,
    "last_successful_check": None,
    "status": None,
    "expediente": None,
    "fecha_resolucion": None,
    "consecutive_failures": 0,
    "failure_alerted": False,
    "pending_notifications": [],
}


def default_state() -> dict[str, Any]:
    state = DEFAULT_STATE.copy()
    state["pending_notifications"] = []
    return state


class StateStore:
    def __init__(self, project_root: Path) -> None:
        self.data_dir = project_root / "data"
        self.state_file = self.data_dir / "state.json"
        self.history_file = self.data_dir / "history.jsonl"

    def load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return default_state()
        try:
            loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError("Cannot read data/state.json safely.") from exc
        if not isinstance(loaded, dict):
            raise StateError("data/state.json must contain a JSON object.")
        state = default_state()
        state.update(loaded)
        if not isinstance(state["pending_notifications"], list):
            raise StateError("data/state.json pending_notifications must be a list.")
        return state

    def save(self, state: dict[str, Any]) -> None:
        try:
            write_json_atomic(self.state_file, state, sort_keys=True)
        except OSError as exc:
            raise StateError("Cannot write data/state.json safely.") from exc

    def append_history(self, record: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.history_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise StateError("Cannot append data/history.jsonl safely.") from exc
