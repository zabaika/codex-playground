"""One-time queries must preserve the configured monitor's state."""

from copy import deepcopy
import io
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

import config
import main as monitor
from infoext import InfoExtError, InfoExtResult
from notifier import NotifierError
from state import StateStore


@pytest.fixture
def runtime_config(monkeypatch):
    raw = tomllib.loads((Path(config.__file__).parent / "config/runtime.example.toml").read_text())
    raw["infoext"].update(nie="CONFIG_SUBJECT", fecha_presentacion="01/02/2026", ano_nacimiento="1990")
    monkeypatch.setattr(config, "load_runtime_config", lambda _: deepcopy(raw))
    return raw


def test_query_overrides_and_config_fallback(runtime_config, tmp_path):
    settings = config.load_settings(tmp_path, query_overrides={"nie": "QUERY_SUBJECT", "ano_nacimiento": "1995"})
    assert (settings.nie, settings.fecha_presentacion, settings.ano_nacimiento) == ("QUERY_SUBJECT", "01/02/2026", "1995")
    assert runtime_config["infoext"]["nie"] == "CONFIG_SUBJECT"


@pytest.mark.parametrize("overrides", [
    {"ano_nacimiento": "31/02/1990"}, {"fecha_presentacion": "DD/MM/YYYY"},
    {"nie": ""}, {"unexpected": "value"}, {"ano_nacimiento": "not-a-year"},
    {"ano_nacimiento": "03/04/1995"}, {"ano_nacimiento": "995"},
    {"ano_nacimiento": "19950"}, {"ano_nacimiento": "0000"},
])
def test_query_validation(runtime_config, tmp_path, overrides):
    with pytest.raises(config.ConfigurationError):
        config.load_settings(tmp_path, query_overrides=overrides)


@pytest.mark.parametrize("outcome", ["success", "failure", "delivery_failure"])
def test_query_preserves_primary_state_and_history(monkeypatch, tmp_path, capsys, outcome):
    store = StateStore(tmp_path)
    initial = store.load()
    initial.update(status="PRIMARY", expediente="PRIMARY_CASE", consecutive_failures=2, failure_alerted=True)
    store.save(initial)
    settings = SimpleNamespace(
        project_root=tmp_path, telegram_connector_dir=tmp_path, portal_min_check_interval_seconds=60,
        nie="QUERY_SUBJECT", masked_nie="****", debug_submitted_captcha_responses=True,
    )
    sent, options = [], []
    class Notifier:
        def send(self, message):
            if outcome == "delivery_failure":
                raise NotifierError("Synthetic send failure")
            sent.append(message)
    class Client:
        def __init__(self, *_):
            pass
        def check(self, **kwargs):
            options.append(kwargs)
            if outcome == "failure":
                raise InfoExtError("Synthetic source failure")
            return InfoExtResult("QUERY_STATUS", "QUERY_CASE", None, None, None, 1)
    monkeypatch.setattr(monitor, "TelegramConnectorNotifier", lambda _: Notifier())
    monkeypatch.setattr(monitor, "InfoExtClient", Client)
    monkeypatch.setattr(monitor, "create_captcha_solver", lambda _: None)
    code = monitor.run_check(SimpleNamespace(query_stdin=True), settings, logging.getLogger("query-test"))
    final = store.load()
    assert code == (1 if outcome == "failure" else 0)
    for key in ("status", "expediente", "last_successful_check", "consecutive_failures", "failure_alerted"):
        assert final[key] == initial[key]
    assert not store.history_file.exists()
    assert options[0]["submission_artifact_dir"] is None
    assert bool(final["pending_notifications"]) is (outcome == "delivery_failure")
    output = capsys.readouterr().out
    if outcome != "failure":
        expected = "pending" if outcome == "delivery_failure" else "delivered"
        assert f"Telegram notification: {expected}" in output
    if outcome == "success":
        assert len(sent) == 1 and "разового запроса" in sent[0] and "QUERY_STATUS" in sent[0]
        assert "NIE: QUERY_SUBJECT" in sent[0]
    if outcome == "delivery_failure":
        assert final["pending_notifications"][0]["nie"] == "QUERY_SUBJECT"


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_monitored_check_reports_delivery_and_preserves_retry(monkeypatch, tmp_path, capsys, delivery_fails):
    settings = SimpleNamespace(
        project_root=tmp_path, telegram_connector_dir=tmp_path, portal_min_check_interval_seconds=60,
        nie="CONFIG_SUBJECT", masked_nie="****", debug_submitted_captcha_responses=False, notify_on_unchanged_status=False,
    )
    def send(_):
        assert "NIE: CONFIG_SUBJECT" in _
        if delivery_fails:
            raise NotifierError("Synthetic send failure")
    monkeypatch.setattr(monitor, "TelegramConnectorNotifier", lambda _: SimpleNamespace(send=send))
    monkeypatch.setattr(monitor, "create_captcha_solver", lambda _: None)
    monkeypatch.setattr(monitor, "InfoExtClient", lambda *_: SimpleNamespace(
        check=lambda **_: InfoExtResult("SYNTHETIC_STATUS", None, None, None, None, 1),
    ))
    assert monitor.run_check(SimpleNamespace(query_stdin=False, notify=True), settings, logging.getLogger("test")) == 0
    expected = "pending" if delivery_fails else "delivered"
    assert f"Telegram notification: {expected}" in capsys.readouterr().out
    state = StateStore(tmp_path).load()
    assert state["status"] == "SYNTHETIC_STATUS"
    assert bool(state["pending_notifications"]) is delivery_fails
    if delivery_fails:
        assert state["pending_notifications"][0]["nie"] == "CONFIG_SUBJECT"


@pytest.mark.parametrize("portal_fails", [False, True])
def test_sleep_interruption_preserves_status_failures_and_history(monkeypatch, tmp_path, portal_fails):
    store = StateStore(tmp_path)
    initial = store.load()
    initial.update(status="PRIMARY", consecutive_failures=2)
    store.save(initial)
    settings = SimpleNamespace(
        project_root=tmp_path, telegram_connector_dir=tmp_path, portal_min_check_interval_seconds=60,
        nie="SYNTHETIC_SUBJECT", masked_nie="****", debug_submitted_captcha_responses=False,
    )
    def check(**_):
        if portal_fails:
            raise InfoExtError("Synthetic parsing failure after resume")
        return InfoExtResult("NEW_STATUS", None, None, None, None, 1)
    monkeypatch.setattr(monitor, "TelegramConnectorNotifier", lambda _: SimpleNamespace(
        send=lambda _: pytest.fail("Sleep must not trigger an alert or status notification"),
    ))
    monkeypatch.setattr(monitor, "InfoExtClient", lambda *_: SimpleNamespace(check=check))
    monkeypatch.setattr(monitor, "create_captcha_solver", lambda _: None)
    monkeypatch.setattr(monitor.common_process, "sleep_elapsed", lambda *_: 600)
    assert monitor.run_check(SimpleNamespace(query_stdin=False), settings, logging.getLogger("test")) == 125
    final = store.load()
    for key in ("status", "consecutive_failures", "failure_alerted", "pending_notifications", "last_successful_check"):
        assert final[key] == initial[key]
    assert not store.history_file.exists()


def test_supervised_worker_reads_overrides_from_stdin(monkeypatch, tmp_path):
    query = {"nie": "QUERY_SUBJECT"}
    observed = []
    monkeypatch.setattr(monitor.sys, "argv", ["main.py", "--check-now", "--query-stdin", "--_ttl-worker"])
    monkeypatch.setattr(monitor.sys, "stdin", io.StringIO(json.dumps(query)))
    monkeypatch.setattr(monitor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(monitor, "load_settings", lambda *_, **kwargs: observed.append(kwargs) or object())
    monkeypatch.setattr(monitor, "configure_logging", lambda _: logging.getLogger("query-test"))
    monkeypatch.setattr(monitor, "acquire_lock", lambda _: io.StringIO())
    monkeypatch.setattr(monitor, "run_check", lambda *_: 0)
    assert monitor.main() == 0
    assert observed == [{"require_infoext": True, "query_overrides": query}]


def test_hard_timeout_supervisor_preserves_query_stdin(tmp_path):
    worker = tmp_path / "main.py"
    worker.write_text(
        "import json, logging, sys\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "if '--_ttl-worker' in sys.argv:\n"
        "    assert '--query-stdin' in sys.argv\n"
        "    print(json.dumps(json.load(sys.stdin)))\n"
        "    raise SystemExit(0)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import main\n"
        "sys.path.insert(0, str(Path(main.__file__).parent.parent))\n"
        "main.RUNTIME_ROOT = Path(__file__).parent\n"
        "args = main.build_parser().parse_args(['--check-now', '--query-stdin'])\n"
        "raise SystemExit(main.run_with_timeout(args, SimpleNamespace(timeout_seconds=5), logging.getLogger('test')))\n"
    )
    query = {"nie": "SYNTHETIC_SUBJECT", "fecha_presentacion": "01/02/2026"}
    completed = subprocess.run(
        [sys.executable, str(worker), str(Path(monitor.__file__).parent)],
        input=json.dumps(query), text=True, capture_output=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == query
