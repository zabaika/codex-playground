import pytest
import logging
from pathlib import Path
from types import SimpleNamespace

import main as monitor

from main import build_parser, validate_args


def test_debug_is_available_with_check_now() -> None:
    parser = build_parser()
    args = parser.parse_args(["--check-now", "--debug"])
    validate_args(parser, args)

    assert args.check_now is True
    assert args.debug is True


def test_debug_cannot_be_combined_with_telegram_test() -> None:
    parser = build_parser()
    args = parser.parse_args(["--test-telegram", "--debug"])

    with pytest.raises(SystemExit):
        validate_args(parser, args)


@pytest.mark.parametrize("arguments", [
    ["--check-now"], ["--check-now", "--debug"], ["--test-telegram"],
])
def test_public_modes_enter_the_hard_timeout_supervisor(monkeypatch, tmp_path, arguments) -> None:
    settings = SimpleNamespace(project_root=tmp_path)
    monkeypatch.setattr(monitor.sys, "argv", ["main.py", *arguments])
    monkeypatch.setattr(monitor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(monitor, "configure_logging", lambda _: logging.getLogger("test"))
    monkeypatch.setattr(monitor, "load_settings", lambda *a, **kw: settings)
    observed = []
    monkeypatch.setattr(monitor, "run_with_timeout", lambda args, config, logger: (
        observed.append((args, config)) or 124
    ))

    assert monitor.main() == 124
    assert observed[0][1] is settings
    assert observed[0][0]._ttl_worker is False


def test_manual_timeout_releases_lock_and_allows_a_fresh_run(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(monitor, "RUNTIME_ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(Path(monitor.__file__).resolve().parent.parent))
    from common import process as common_process
    from common.process import ProcessConfig

    worker = tmp_path / "main.py"
    worker.write_text(
        "import fcntl, time\n"
        "from pathlib import Path\n"
        "root = Path(__file__).parent\n"
        "handle = (root / 'data' / 'monitor.lock').open('w')\n"
        "fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "(root / 'started').touch()\n"
        "time.sleep(30)\n"
    )
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(common_process, "load_process_config", lambda: ProcessConfig(
        30, 0.1, 0.05, 124, "TERM", "KILL"
    ))
    args = build_parser().parse_args(["--check-now"])
    settings = SimpleNamespace(project_root=tmp_path, timeout_seconds=0.5)
    logger = logging.getLogger("manual-timeout-test")
    with caplog.at_level(logging.INFO):
        assert monitor.run_with_timeout(args, settings, logger) == 124
    assert (tmp_path / "started").exists()
    assert "process group terminated" in caplog.text
    lock = monitor.acquire_lock(tmp_path)
    assert lock is not None
    lock.close()

    worker.write_text("raise SystemExit(0)\n")
    assert monitor.run_with_timeout(args, settings, logger) == 0
