"""Exercise installer lifecycle blocks without touching the real LaunchAgent."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import shutil

from config import load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def isolated_launchctl(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    calls = tmp_path / "calls.txt"
    stub = commands / "launchctl"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS_FILE"\n')
    stub.chmod(0o700)
    return {**os.environ, "PATH": str(commands) + os.pathsep + os.environ.get("PATH", ""),
            "CALLS_FILE": str(calls)}, calls


def test_installer_renders_config_calendar_and_reloads_agent(tmp_path) -> None:
    root = tmp_path / "project & source"
    service = tmp_path / "service runtime"
    (root / "config").mkdir(parents=True)
    example = (PROJECT_ROOT / "config/runtime.example.toml").read_text()
    local = example.replace('nie = ""', 'nie = "SYNTHETIC-NIE"').replace(
        'fecha_presentacion = ""', 'fecha_presentacion = "01/02/2026"'
    ).replace('first_run_time = "10:00"', 'first_run_time = "09:30"').replace(
        'last_run_time = "20:00"', 'last_run_time = "19:30"'
    ).replace('weekdays = [1, 2, 3, 4, 5]', 'weekdays = [2, 4]')
    (root / "config/runtime.local.toml").write_text(local)
    script = (PROJECT_ROOT / "scripts/reload_launch_agent.sh").read_text()
    # Execute the production renderer with temporary output and the real loader.
    renderer = script.split("'\nplutil -lint", 1)[0].rsplit("-c '\n", 1)[1]
    target = tmp_path / "monitor.plist"
    env, calls = isolated_launchctl(tmp_path)
    env.update(PROJECT_ROOT=str(root), SERVICE_ROOT=str(service), PLIST_TARGET=str(target),
               PLIST_SOURCE=str(PROJECT_ROOT / "launchd/com.infoext.monitor.plist"),
               PYTHONPATH=str(PROJECT_ROOT))
    subprocess.run([sys.executable, "-c", renderer], env=env, check=True, capture_output=True)
    payload = plistlib.loads(target.read_bytes())
    settings = load_settings(root)
    assert payload["StartCalendarInterval"] == [
        {"Weekday": day, "Hour": time.hour, "Minute": time.minute}
        for day in settings.launchd.weekdays for time in settings.launchd.calendar_times
    ]
    assert payload["ProgramArguments"] == [str(service / "launchd/infoext-monitor-launcher")]
    assert payload["WorkingDirectory"] == str(service)
    assert payload["EnvironmentVariables"]["INFOEXT_PROJECT_ROOT"] == str(root)
    assert "RunAtLoad" not in payload
    assert "__PROJECT_ROOT__" not in target.read_text()
    assert "__SERVICE_ROOT__" not in target.read_text()

    registration = script.split('USER_ID="$(id -u)"', 1)[1].split(
        '\necho ', 1
    )[0]
    env.update(USER_ID="test-user", LABEL="com.infoext.monitor")
    subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + registration],
                   env=env, check=True, capture_output=True)
    assert calls.read_text().splitlines() == [
        f"bootout gui/test-user {target}", f"bootstrap gui/test-user {target}",
        "print gui/test-user/com.infoext.monitor",
    ]


def test_reload_uses_installed_python_without_installing_dependencies(tmp_path) -> None:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "scripts", root / "scripts")
    shutil.copytree(PROJECT_ROOT / "launchd", root / "launchd")
    shutil.copy2(PROJECT_ROOT / "config.py", root / "config.py")
    (root / "config").mkdir()
    local = (PROJECT_ROOT / "config/runtime.example.toml").read_text().replace(
        'nie = ""', 'nie = "SYNTHETIC-NIE"'
    ).replace('fecha_presentacion = ""', 'fecha_presentacion = "01/02/2026"').replace(
        'last_run_time = "20:00"', 'last_run_time = "10:00"'
    )
    config = root / "config/runtime.local.toml"
    config.write_text(local)
    home = tmp_path / "home"
    service = home / "Library/Application Support/infoext_monitor_service"
    (service / ".venv/bin").mkdir(parents=True)
    (service / ".venv/bin/python").symlink_to(sys.executable)
    (service / "launchd").mkdir()
    launcher = service / "launchd/infoext-monitor-launcher"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o700)
    env, calls = isolated_launchctl(tmp_path)
    env["HOME"] = str(home)
    script = root / "scripts/reload_launch_agent.sh"
    completed = subprocess.run(["/bin/bash", str(script)], cwd=tmp_path,
                               env=env, check=True, capture_output=True, text=True)
    assert "No check was triggered" in completed.stdout
    target = home / "Library/LaunchAgents/com.infoext.monitor.plist"
    payload = plistlib.loads(target.read_bytes())
    assert all(slot["Hour"] == 10 and slot["Minute"] == 0
               for slot in payload["StartCalendarInterval"])
    assert len(payload["StartCalendarInterval"]) == 5
    assert len(calls.read_text().splitlines()) == 3
    assert not (root / ".venv").exists()
    assert config.stat().st_mode & 0o777 == 0o600

    calls.unlink()
    config.write_text(local.replace('fecha_presentacion = "01/02/2026"',
                                    'fecha_presentacion = "invalid"'))
    failed = subprocess.run(["/bin/bash", str(script)], env=env,
                            capture_output=True, text=True)
    assert failed.returncode != 0
    assert not calls.exists()


def test_runtime_sync_keeps_project_config_and_installs_shared_assets(tmp_path) -> None:
    root = tmp_path / "project"
    service = tmp_path / "service"
    root.mkdir()
    for name in ("main.py", "config.py", "infoext.py", "captcha_solver.py", "state.py",
                 "notifier.py", "requirements.txt"):
        shutil.copy2(PROJECT_ROOT / name, root / name)
    shutil.copytree(PROJECT_ROOT / "launchd", root / "launchd")
    (root / "bin").mkdir()
    (root / "bin/infoext-vision-ocr").write_text("synthetic helper")
    shutil.copytree(PROJECT_ROOT.parent / "common", tmp_path / "common",
                    ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (root / "config").mkdir()
    shutil.copy2(PROJECT_ROOT / "config/runtime.example.toml", root / "config/runtime.local.toml")
    (service / "common").mkdir(parents=True)
    (service / "common/obsolete.py").touch()
    script = (PROJECT_ROOT / "scripts/install.sh").read_text()
    sync = script.split("# Keep executable code outside Documents", 1)[1].split(
        '\n"$PROJECT_ROOT/.venv/bin/python" -m venv', 1
    )[0]
    env = {**os.environ, "PROJECT_ROOT": str(root), "SERVICE_ROOT": str(service)}
    subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n# Keep executable code outside Documents" + sync],
                   env=env, check=True, capture_output=True)
    assert (service / "common/config/process.toml").is_file()
    assert not (service / "common/obsolete.py").exists()
    assert not (service / "config/runtime.local.toml").exists()
    assert (service / "bin/infoext-vision-ocr").is_file()
    env.update(INFOEXT_PROJECT_ROOT=str(root), PYTHONPATH=str(service))
    probe = (
        "from config import PROJECT_ROOT, RUNTIME_ROOT, load_settings; "
        "from pathlib import Path; import os; "
        "assert PROJECT_ROOT == Path(os.environ['INFOEXT_PROJECT_ROOT']); "
        "assert RUNTIME_ROOT == Path(os.environ['SERVICE_ROOT']); "
        "assert load_settings(require_infoext=False).project_root == PROJECT_ROOT"
    )
    subprocess.run([sys.executable, "-c", probe], cwd=service, env=env,
                   check=True, capture_output=True)


def test_uninstall_removes_only_agent_and_preserves_runtime_data(tmp_path) -> None:
    env, calls = isolated_launchctl(tmp_path)
    target = tmp_path / "monitor.plist"
    target.write_text("test plist")
    for name in ("state.json", "history.jsonl", "runtime.local.toml"):
        (tmp_path / name).write_text("preserved")
    body = (PROJECT_ROOT / "scripts/uninstall.sh").read_text().split('if [[ -f', 1)[1]
    env.update(PLIST_TARGET=str(target), USER_ID="test-user")
    subprocess.run(["/bin/bash", "-c", "set -euo pipefail\nif [[ -f" + body],
                   env=env, check=True, capture_output=True)
    assert not target.exists()
    assert calls.read_text().splitlines() == [f"bootout gui/test-user {target}"]
    assert all((tmp_path / name).read_text() == "preserved"
               for name in ("state.json", "history.jsonl", "runtime.local.toml"))
