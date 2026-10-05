"""InfoExt command routing without Telegram or portal network calls."""

import importlib.util
from pathlib import Path
import subprocess
import json
import plistlib

import pytest


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "infoext_bridge_test", Path(__file__).resolve().parents[1] / "telegram_bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "main.py").touch()
    interpreter = runtime / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    interpreter.chmod(0o700)
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "config/runtime.local.toml").touch()
    agent = tmp_path / "com.infoext.monitor.plist"
    agent.write_bytes(plistlib.dumps({
        "Label": "com.infoext.monitor", "WorkingDirectory": str(runtime),
        "EnvironmentVariables": {"INFOEXT_PROJECT_ROOT": str(project)},
    }))
    monkeypatch.setattr(module, "INFOEXT_LAUNCH_AGENT", agent)
    config = {"bridge": {
        "allowed_chat_ids": "123", "allowed_user_ids": "456",
    }}
    monkeypatch.setattr(module, "load_runtime_config", lambda: config)
    return module, config


def test_infoext_fixed_command_and_arguments(bridge):
    module, config = bridge
    assert module.normalize_bridge_command_text("/infoext@mybot") == "/infoext"
    argv = module.build_history_command("/infoext")
    runtime, _ = module.resolve_infoext_paths()
    assert argv == [
        str(runtime / ".venv/bin/python"),
        str(runtime / "main.py"),
        "--check-now", "--notify",
    ]
    with pytest.raises(ValueError):
        module.build_history_command("/infoext --debug")
    module.INFOEXT_LAUNCH_AGENT.unlink()
    with pytest.raises(ValueError):
        module.build_history_command("/infoext")


@pytest.mark.parametrize("authorized", [True, False])
@pytest.mark.parametrize("command", ["/infoext", "infoext", "INFOEXT", "/infoext@mybot"])
def test_infoext_authorization_environment_and_safe_response(bridge, monkeypatch, authorized, command):
    module, config = bridge
    messages, calls = [], []
    monkeypatch.setattr(module, "send_text_message", lambda _, __, text: messages.append(text))
    def worker(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "InfoExt check\nTelegram notification: delivered\nPRIVATE RAW OUTPUT", "SECRET")
    monkeypatch.setattr(module, "shared_run_worker_subprocess", worker)
    update = {"message": {"chat": {"id": 123}, "from": {"id": 456 if authorized else 999}, "text": command}}
    module.handle_history_command("unused", config, update, secret_env={"BOT_TOKEN": "secret"})
    assert bool(calls) is authorized
    assert all("PRIVATE" not in message and "SECRET" not in message for message in messages)
    if authorized:
        assert messages == []  # The monitor sends the status; no extra success reply.
        _, kwargs = calls[0]
        assert kwargs["timeout_seconds"] is None  # Monitor owns the hard TTL.
        runtime, project = module.resolve_infoext_paths()
        assert kwargs["env"]["INFOEXT_PROJECT_ROOT"] == str(project)
        assert "BOT_TOKEN" not in kwargs["env"]
        assert kwargs["cwd"] == runtime


@pytest.mark.parametrize("stdout,code,expected", [
    ("InfoExt check skipped: portal minimum interval has not elapsed.", 0, "интервал"),
    ("", 0, "другая проверка"),
    ("PRIVATE", 1, "не удалась"),
    ("InfoExt check\nTelegram notification: pending\n", 0, "доставка уведомления не подтверждена"),
    ("InfoExt check\n", 0, "доставка уведомления не подтверждена"),
])
def test_infoext_terminal_response(bridge, monkeypatch, stdout, code, expected):
    module, config = bridge
    messages = []
    monkeypatch.setattr(module, "send_text_message", lambda _, __, text: messages.append(text))
    monkeypatch.setattr(module, "shared_run_worker_subprocess", lambda argv, **_: subprocess.CompletedProcess(argv, code, stdout, "SECRET"))
    module.handle_history_command("unused", config, {"message": {
        "chat": {"id": 123}, "from": {"id": 456}, "text": "/infoext",
    }})
    assert expected in messages[-1]
    assert len(messages) == 1
    assert "PRIVATE" not in messages[-1] and "SECRET" not in messages[-1]


@pytest.mark.parametrize("command", ["/infoext", "infoext"])
def test_infoext_personal_arguments_use_stdin_and_are_redacted(bridge, monkeypatch, command):
    module, config = bridge
    text = f"{command} SYNTHETIC_NIE 01/02/2026 1990"
    update = {"message": {"chat": {"id": 123}, "from": {"id": 456}, "text": text}}
    observed = []
    def run(argv, **kwargs):
        observed.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "InfoExt check\nTelegram notification: delivered\n", "")
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "send_text_message", lambda *_: pytest.fail("Unexpected success reply"))
    module.handle_history_command("unused", config, update)
    argv, kwargs = observed[0]
    assert argv[-1] == "--query-stdin"
    assert "SYNTHETIC_NIE" not in " ".join(argv)
    assert json.loads(kwargs["input"]) == {
        "nie": "SYNTHETIC_NIE", "fecha_presentacion": "01/02/2026", "ano_nacimiento": "1990",
    }
    stored = module.redact_update_for_storage(update)
    assert stored["command_text"] == "/infoext"
    assert "SYNTHETIC_NIE" not in json.dumps(stored)


def test_infoext_partial_arguments(bridge):
    module, _ = bridge
    assert module.parse_infoext_query(["/infoext", "SYNTHETIC_NIE"]) == {"nie": "SYNTHETIC_NIE"}
    with pytest.raises(ValueError):
        module.build_history_command("/infoext a b c d")


@pytest.mark.parametrize("year", ["03/04/1990", "990", "19900", "abcd", "0000"])
def test_infoext_rejects_non_four_digit_birth_year(bridge, year):
    module, _ = bridge
    with pytest.raises(ValueError, match="4"):
        module.build_history_command(f"/infoext SYNTHETIC_NIE 01/02/2026 {year}")


@pytest.mark.parametrize("invalid", ["corrupt", "xml", "relative", "missing_project", "missing_interpreter"])
def test_infoext_invalid_installation_does_not_launch(bridge, monkeypatch, invalid):
    module, config = bridge
    agent = module.INFOEXT_LAUNCH_AGENT
    payload = plistlib.loads(agent.read_bytes())
    if invalid in {"corrupt", "xml"}:
        agent.write_bytes(b"not a plist" if invalid == "corrupt" else b"<?xml version='1.0'?><plist><dict>")
    else:
        if invalid == "relative":
            payload["WorkingDirectory"] = "relative/runtime"
        elif invalid == "missing_project":
            del payload["EnvironmentVariables"]["INFOEXT_PROJECT_ROOT"]
        else:
            (Path(payload["WorkingDirectory"]) / ".venv/bin/python").unlink()
        agent.write_bytes(plistlib.dumps(payload))
    messages = []
    monkeypatch.setattr(module, "send_text_message", lambda _, __, text: messages.append(text))
    monkeypatch.setattr(module, "shared_run_worker_subprocess", lambda *_a, **_kw: pytest.fail("Must not start"))
    module.handle_history_command("unused", config, {"message": {
        "chat": {"id": 123}, "from": {"id": 456}, "text": "/infoext",
    }})
    assert len(messages) == 1 and "install.sh" in messages[0]
