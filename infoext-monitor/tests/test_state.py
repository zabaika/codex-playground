import json
from pathlib import Path

from state import StateStore


def test_state_store_round_trip_and_history(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["status"] = "EN TRÁMITE"
    state["pending_notifications"] = [{"kind": "status", "new_status": "EN TRÁMITE"}]

    store.save(state)
    store.append_history({"timestamp": "2026-10-03T15:02:00+02:00", "status": "EN TRÁMITE"})

    assert store.load()["status"] == "EN TRÁMITE"
    assert json.loads(store.history_file.read_text(encoding="utf-8")) == {
        "status": "EN TRÁMITE",
        "timestamp": "2026-10-03T15:02:00+02:00",
    }
    assert not list(store.data_dir.glob("*.tmp"))
