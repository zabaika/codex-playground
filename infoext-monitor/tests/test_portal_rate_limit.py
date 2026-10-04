from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from main import reserve_portal_check
from state import StateError, StateStore


TZ = ZoneInfo("Europe/Madrid")


def test_portal_check_reservation_enforces_the_configured_interval(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    first_start = datetime(2026, 10, 5, 10, 0, tzinfo=TZ)

    assert reserve_portal_check(state, store, 60, started_at=first_start) is True
    assert store.load()["last_portal_check_started_at"] == "2026-10-05T10:00:00+02:00"
    assert reserve_portal_check(state, store, 60, started_at=datetime(2026, 10, 5, 10, 0, 59, tzinfo=TZ)) is False
    assert reserve_portal_check(state, store, 60, started_at=datetime(2026, 10, 5, 10, 1, tzinfo=TZ)) is True


def test_portal_check_reservation_rejects_an_invalid_persisted_timestamp(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["last_portal_check_started_at"] = "not-a-timestamp"

    with pytest.raises(StateError, match="last_portal_check_started_at"):
        reserve_portal_check(state, store, 60, started_at=datetime(2026, 10, 5, 10, 0, tzinfo=TZ))
