from pathlib import Path
import json
import subprocess
import sys

import pytest

from common import python_environment as environment
from common.python_runtime import RuntimeSelectionError, probe_python


def test_create_then_reuse_environment_without_reinstall(tmp_path):
    target = tmp_path / "environment with spaces"
    base = probe_python(sys.executable)
    first = environment.prepare_venv(base, target)
    marker = target / "operator-marker"
    marker.write_text("preserved")
    executable_mtime = (target / "pyvenv.cfg").stat().st_mtime_ns
    second = environment.prepare_venv(base, target)
    assert marker.read_text() == "preserved"
    assert (target / "pyvenv.cfg").stat().st_mtime_ns == executable_mtime
    assert first["base_executable"] == second["base_executable"]
    assert Path(second["prefix"]).resolve() == target.resolve()


def test_failed_new_environment_restores_existing_runtime(tmp_path):
    target = tmp_path / "environment"
    base = probe_python(sys.executable)
    environment.prepare_venv(base, target)
    marker = target / "working-marker"
    marker.write_text("previous runtime")
    previous_cfg = (target / "pyvenv.cfg").read_bytes()
    with pytest.raises(RuntimeSelectionError):
        environment.prepare_venv(
            base, target, imports=("fixture_missing_runtime_module",),
        )
    assert marker.read_text() == "previous runtime"
    assert (target / "pyvenv.cfg").read_bytes() == previous_cfg
    check = subprocess.run(
        [str(target / "bin/python"), "-I", "-c", "import ssl,sqlite3"],
        capture_output=True, timeout=15,
    )
    assert check.returncode == 0
    assert not list(tmp_path.glob(".python-previous-*"))


def test_changed_recorded_abi_requires_rebuild(tmp_path):
    target = tmp_path / "environment"
    base = probe_python(sys.executable)
    environment.prepare_venv(base, target)
    marker = target / "old-abi-marker"
    marker.touch()
    record = target / ".python-runtime.json"
    saved = json.loads(record.read_text())
    saved["soabi"] = "fixture-previous-abi"
    record.write_text(json.dumps(saved))
    current = environment.prepare_venv(base, target)
    assert not marker.exists()
    assert current["soabi"] == base["soabi"]


@pytest.fixture
def candidates(tmp_path, monkeypatch):
    """Model an unavailable wheel set, not a real second Python version."""
    monkeypatch.delenv("PYTHON_BIN", raising=False)
    actual_probe = environment.runtime.probe_python
    good = actual_probe(sys.executable)
    bad_path = str(tmp_path / "python without compatible wheels")
    bad = good | {"executable": bad_path}
    monkeypatch.setattr(environment.runtime, "probe_python", lambda executable:
                        bad if executable == bad_path else actual_probe(executable))
    monkeypatch.setattr(environment.runtime, "discover_candidates", lambda: [
        ("homebrew", bad_path), ("path", sys.executable),
    ])
    real_validate = environment.runtime.validate_requirements

    def validate(binding, *args, **kwargs):
        if binding["executable"] == bad_path and not kwargs.get("use_installed"):
            raise RuntimeSelectionError("Fixture dependency wheels incompatible")
        return real_validate(binding, *args, **kwargs)

    monkeypatch.setattr(environment.runtime, "validate_requirements", validate)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# fixture has no dependencies to download\n")
    return good, bad_path, requirements


def test_preparation_selects_next_candidate_when_wheels_are_incompatible(
    tmp_path, monkeypatch, capsys, candidates,
):
    _, _, requirements = candidates
    target = tmp_path / "selected environment"
    monkeypatch.setattr(sys, "argv", [
        "fixture", "--venv", str(target), "--requirements", str(requirements),
    ])
    assert environment.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "path"
    assert Path(result["prefix"]).resolve() == target.resolve()
    assert (target / "pyvenv.cfg").exists()


@pytest.mark.parametrize("override", ["flag", "environment", "local"])
def test_incompatible_requested_candidate_does_not_fall_back(
    tmp_path, monkeypatch, capsys, candidates, override,
):
    _, bad_path, requirements = candidates
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "working-data"
    marker.write_text("preserve")
    argv = ["fixture", "--venv", str(target), "--requirements", str(requirements)]
    if override == "flag":
        argv += ["--python", bad_path]
    elif override == "environment":
        monkeypatch.setenv("PYTHON_BIN", bad_path)
    else:
        config = tmp_path / "runtime.toml"
        config.write_text('[python]\nexecutable = ' + json.dumps(bad_path) + '\n')
        argv += ["--config", str(config)]
    monkeypatch.setattr(sys, "argv", argv)
    assert environment.main() == 1
    assert "Fixture dependency wheels incompatible" in capsys.readouterr().err
    assert marker.read_text() == "preserve"
    assert not (target / "pyvenv.cfg").exists()


def test_all_incompatible_candidates_preserve_runtime_and_binding(
    tmp_path, monkeypatch, capsys, candidates,
):
    _, _, requirements = candidates
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "working-data"
    marker.write_text("preserve")
    record = tmp_path / "binding.json"
    record.write_text('{"previous": true}\n')
    before = record.read_bytes()

    def reject(binding, *args, **kwargs):
        raise RuntimeSelectionError("Fixture dependency wheels incompatible")

    monkeypatch.setattr(environment.runtime, "validate_requirements", reject)
    monkeypatch.setattr(sys, "argv", [
        "fixture", "--venv", str(target), "--requirements", str(requirements),
        "--record", str(record),
    ])
    assert environment.main() == 1
    error = capsys.readouterr().err
    assert "homebrew:" in error and "path:" in error
    assert marker.read_text() == "preserve"
    assert record.read_bytes() == before
    assert not list(tmp_path.glob(".python-previous-*"))


def test_healthy_existing_environment_does_not_require_fresh_wheels(
    tmp_path, monkeypatch, capsys, candidates,
):
    base, _, requirements = candidates
    target = tmp_path / "existing"
    environment.prepare_venv(base, target)
    marker = target / "working-data"
    marker.write_text("preserve")
    before = (target / "pyvenv.cfg").stat().st_mtime_ns
    real_validate = environment.runtime.validate_requirements

    def existing_only(binding, *args, **kwargs):
        if not kwargs.get("use_installed"):
            raise RuntimeSelectionError("Fresh wheels unavailable")
        return real_validate(binding, *args, **kwargs)

    monkeypatch.setattr(environment.runtime, "validate_requirements", existing_only)
    monkeypatch.setattr(sys, "argv", [
        "fixture", "--python", sys.executable, "--venv", str(target),
        "--requirements", str(requirements),
    ])
    assert environment.main() == 0
    capsys.readouterr()
    assert marker.read_text() == "preserve"
    assert (target / "pyvenv.cfg").stat().st_mtime_ns == before


def test_all_destinations_are_checked_before_preparation(
    tmp_path, monkeypatch, capsys, candidates,
):
    base, _, requirements = candidates
    existing = tmp_path / "existing"
    environment.prepare_venv(base, existing)
    before = (existing / "pyvenv.cfg").stat().st_mtime_ns
    new = tmp_path / "new"
    monkeypatch.setattr(sys, "argv", [
        "fixture", "--venv", str(existing), "--venv", str(new),
        "--requirements", str(requirements),
    ])
    assert environment.main() == 0
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 2
    assert all(item["source"] == "path" for item in results)
    assert (existing / "pyvenv.cfg").stat().st_mtime_ns == before
    assert (new / "pyvenv.cfg").exists()
