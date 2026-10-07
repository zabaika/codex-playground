import json
from pathlib import Path

import pytest

from infoext import InfoExtError, InfoExtResult
from main import handle_check_failure, notification_text, record_failure, record_success, request_identity
from notifier import Notifier
from state import StateStore


class RecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class RecordingLogger:
    def error(self, *_: object) -> None:
        pass

    def info(self, *_: object) -> None:
        pass

    def warning(self, *_: object) -> None:
        pass


def test_changed_status_is_persisted_as_pending_before_delivery(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["status"] = "EN TRÁMITE"
    state["request_identity"] = request_identity("SYNTHETIC_SUBJECT", "01/02/2026")
    store.save(state)
    result = InfoExtResult(
        status="RESUELTO - FAVORABLE",
        expediente=None,
        tipo_autorizacion=None,
        fecha_presentacion=None,
        fecha_resolucion="03/10/2026",
        captcha_attempts=1,
    )

    changed = record_success(
        state,
        store,
        result,
        "2026-10-03T15:02:00+02:00",
        notify_on_unchanged_status=True,
        nie="SYNTHETIC_SUBJECT", fecha_presentacion="01/02/2026",
    )

    persisted = store.load()
    assert changed is True
    assert persisted["status"] == "RESUELTO - FAVORABLE"
    assert persisted["pending_notifications"] == [
        {
            "kind": "status",
            "nie": "SYNTHETIC_SUBJECT",
            "old_status": "EN TRÁMITE",
            "new_status": "RESUELTO - FAVORABLE",
            "fecha_resolucion": "03/10/2026",
            "detected_at": "2026-10-03T15:02:00+02:00",
        }
    ]


def test_first_success_creates_a_current_status_notification_when_enabled(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    result = InfoExtResult("EN TRÁMITE", None, None, None, None, 1)

    changed = record_success(
        state,
        store,
        result,
        "2026-10-03T15:02:00+02:00",
        notify_on_unchanged_status=True,
        nie="SYNTHETIC_SUBJECT", fecha_presentacion="01/02/2026",
    )

    assert changed is False
    assert store.load()["pending_notifications"] == [
        {
            "kind": "current",
            "nie": "SYNTHETIC_SUBJECT",
            "status": "EN TRÁMITE",
            "fecha_resolucion": "",
            "detected_at": "2026-10-03T15:02:00+02:00",
        }
    ]


@pytest.mark.parametrize("previous_identity", [
    None,
    request_identity("OTHER_SUBJECT", "01/02/2026"),
    request_identity("SYNTHETIC_SUBJECT", "02/02/2026"),
])
def test_different_or_legacy_request_starts_a_new_baseline(tmp_path, previous_identity):
    store = StateStore(tmp_path)
    state = store.load()
    pending = {"kind": "current", "nie": "OTHER_SUBJECT", "status": "OLD_STATUS"}
    state.update(status="OLD_STATUS", request_identity=previous_identity,
                 consecutive_failures=3, failure_alerted=True, pending_notifications=[pending])
    changed = record_success(
        state, store, InfoExtResult("NEW_STATUS", None, None, None, None, 1),
        "2026-10-03T15:02:00+02:00", notify_on_unchanged_status=True,
        nie="SYNTHETIC_SUBJECT", fecha_presentacion="01/02/2026",
    )
    persisted = store.load()
    assert changed is False
    assert persisted["request_identity"] == request_identity("SYNTHETIC_SUBJECT", "01/02/2026")
    assert persisted["pending_notifications"][0] == pending
    assert persisted["pending_notifications"][1]["kind"] == "current"
    assert "old_status" not in persisted["pending_notifications"][1]
    assert persisted["pending_notifications"][1]["nie"] == "SYNTHETIC_SUBJECT"
    record = json.loads(store.history_file.read_text())
    assert record["request_identity"] == persisted["request_identity"]


def test_unchanged_status_does_not_create_notification_when_disabled(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["status"] = "EN TRÁMITE"
    state["request_identity"] = request_identity("SYNTHETIC_SUBJECT", "01/02/2026")
    store.save(state)
    result = InfoExtResult("EN TRÁMITE", None, None, None, None, 1)

    changed = record_success(
        state,
        store,
        result,
        "2026-10-03T15:02:00+02:00",
        notify_on_unchanged_status=False,
        nie="SYNTHETIC_SUBJECT", fecha_presentacion="01/02/2026",
    )

    assert changed is False
    assert store.load()["pending_notifications"] == []


def test_current_status_notification_includes_status_and_check_time() -> None:
    text = notification_text(
        {
            "kind": "current",
            "status": "EN TRÁMITE",
            "fecha_resolucion": "",
            "detected_at": "2026-10-03T15:02:00+02:00",
        }
    )

    assert "текущий статус expediente" in text
    assert "EN TRÁMITE" in text
    assert "Проверено: 03.10.2026 15:02" in text


@pytest.mark.parametrize("notify_unchanged", [True, False])
@pytest.mark.parametrize("status_changed", [True, False])
def test_recovery_combines_status_into_one_persisted_notification(
    tmp_path: Path, notify_unchanged: bool, status_changed: bool
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state.update(status="EN TRÁMITE", consecutive_failures=3, failure_alerted=True,
                 request_identity=request_identity("SYNTHETIC_SUBJECT", "01/02/2026"))
    status = "RESUELTO - FAVORABLE" if status_changed else "EN TRÁMITE"
    result = InfoExtResult(status, None, None, None, "03/10/2026", 1)

    changed = record_success(
        state, store, result, "2026-10-03T15:02:00+02:00",
        notify_on_unchanged_status=notify_unchanged,
        nie="SYNTHETIC_SUBJECT", fecha_presentacion="01/02/2026",
    )

    persisted = store.load()
    assert changed is status_changed
    assert persisted["consecutive_failures"] == 0
    assert persisted["failure_alerted"] is False
    assert len(persisted["pending_notifications"]) == 1
    event = persisted["pending_notifications"][0]
    assert event["kind"] == "health"
    assert event["event"] == "recovery"
    text = notification_text(event)
    assert "NIE: SYNTHETIC_SUBJECT" in text
    assert "проверка снова работает" in text
    assert "Проверено: 03.10.2026 15:02" in text
    assert "Дата решения: 03/10/2026" in text
    if status_changed:
        assert "EN TRÁMITE → RESUELTO - FAVORABLE" in text
    else:
        assert "Текущий статус: EN TRÁMITE" in text


def test_legacy_pending_recovery_is_still_rendered() -> None:
    assert notification_text({
        "kind": "health", "event": "recovery", "status": "EN TRÁMITE",
        "detected_at": "2026-10-03T15:02:00+02:00",
    }).startswith("InfoExt monitor: проверка снова работает.\nТекущий статус: EN TRÁMITE")


def test_failure_alert_is_delivered_on_the_threshold_run(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["consecutive_failures"] = 2
    state["request_identity"] = request_identity("SYNTHETIC_SUBJECT", "01/02/2026")
    store.save(state)
    notifier = RecordingNotifier()

    handle_check_failure(
        state,
        store,
        notifier,
        InfoExtError("offline"),
        RecordingLogger(),
        failure_alert_threshold=3,
        identity=request_identity("SYNTHETIC_SUBJECT", "01/02/2026"),
    )

    assert notifier.messages == [
        "InfoExt monitor: не удалось проверить expediente 3 раза подряд.\nNIE: SYNTHETIC_SUBJECT"
    ]
    assert store.load()["pending_notifications"] == []


@pytest.mark.parametrize("old_failures, old_alerted", [(2, False), (3, True)])
@pytest.mark.parametrize("identity", [
    request_identity("NEW_SUBJECT", "01/02/2026"),
    request_identity("OLD_SUBJECT", "02/02/2026"),
])
def test_failure_tracking_changes_identity_without_replacing_status(
    tmp_path, old_failures, old_alerted, identity,
):
    store = StateStore(tmp_path)
    state = store.load()
    old_identity = request_identity("OLD_SUBJECT", "01/02/2026")
    pending = {"kind": "current", "nie": "OLD_SUBJECT", "status": "OLD_STATUS"}
    state.update(request_identity=old_identity, status="OLD_STATUS",
                 consecutive_failures=old_failures, failure_alerted=old_alerted,
                 pending_notifications=[pending])
    for attempt in range(1, 5):
        record_failure(state, store, InfoExtError("offline"), RecordingLogger(), 3, identity=identity)
        state = store.load()
        assert state["request_identity"] == old_identity
        assert state["status"] == "OLD_STATUS"
        assert state["failure_request_identity"] == identity
        assert state["consecutive_failures"] == attempt
        assert state["failure_alerted"] is (attempt >= 3)
        assert state["pending_notifications"][0] == pending
        assert len(state["pending_notifications"]) == (2 if attempt >= 3 else 1)
    assert state["pending_notifications"][1]["nie"] == identity["nie"]

    changed = record_success(
        state, store, InfoExtResult("NEW_STATUS", None, None, None, None, 1),
        "2026-10-03T15:02:00+02:00", notify_on_unchanged_status=True,
        nie=identity["nie"], fecha_presentacion=identity["fecha_presentacion"],
    )
    assert changed is False
    final = store.load()
    assert final["consecutive_failures"] == 0
    assert final["failure_alerted"] is False
    assert final["request_identity"] == identity
    recovery = final["pending_notifications"][-1]
    assert recovery["event"] == "recovery" and recovery["nie"] == identity["nie"]
    assert "old_status" not in recovery
