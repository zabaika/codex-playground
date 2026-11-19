"""Cold resolution uses the declared lock and an explicitly supplied wheel cache."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cold_requirements(tmp_path):
    project = tmp_path / "source with spaces/telegram_connector"
    project.mkdir(parents=True)
    assets = Path(os.environ.get("PIP_FIND_LINKS") or
                  ROOT / "telegram_connector/third_party").expanduser()
    index = project / "wheel cache"
    index.mkdir()
    for source in assets.glob("*.whl"):
        shutil.copy2(source, index / source.name)
    assert list(index.glob("*.whl")), "Supply the reviewed wheels before testing cold installation"
    requirements = project / "requirements.lock"
    selected_lock = Path(os.environ.get('PYTHON_RUNTIME_TEST_LOCK') or
                         ROOT / 'telegram_connector/requirements.lock')
    requirements.write_bytes(selected_lock.read_bytes())
    home = tmp_path / "empty-home"
    home.mkdir()
    env = {
        "HOME": str(home), "PATH": os.defpath, "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    return project, requirements, env


def resolve(cold_requirements, tmp_path):
    project, requirements, env = cold_requirements
    report = tmp_path / "report.json"
    result = subprocess.run([
        sys.executable, "-I", "-m", "pip", "install", "--dry-run",
        "--ignore-installed", "--only-binary=:all:", "--report", str(report),
        "-r", str(requirements), "--require-hashes", "--no-index",
        "--find-links", str(project / "wheel cache"),
    ], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    return result, report


def test_cold_plan_uses_reviewed_local_wheels_and_lock(cold_requirements, tmp_path):
    project, _, _ = cold_requirements
    result, report = resolve(cold_requirements, tmp_path)
    assert result.returncode == 0, result.stderr
    package = next(item for item in json.loads(report.read_text())["install"]
                   if item["metadata"]["name"].lower() == "pyaes")
    wheel = next((project / "wheel cache").glob("pyaes-*.whl"))
    line = next(line for line in (project / "requirements.lock").read_text().splitlines()
                if line.startswith("pyaes=="))
    expected = line.split("--hash=sha256:")[1]
    assert package["download_info"]["url"] == wheel.as_uri()
    assert package["download_info"]["archive_info"]["hashes"]["sha256"] == expected
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_unavailable_reviewed_wheel_cannot_enter_cold_plan(cold_requirements, tmp_path, fault):
    project, _, _ = cold_requirements
    wheel = next((project / "wheel cache").glob("pyaes-*.whl"))
    if fault == "missing":
        wheel.unlink()
    else:
        wheel.write_bytes(wheel.read_bytes() + b"fixture tampering")
    result, _ = resolve(cold_requirements, tmp_path)
    assert result.returncode != 0
    if fault == "corrupt":
        assert "HASHES" in result.stderr.upper() or "HASH" in result.stderr.upper()


def test_cold_requirements_use_selected_candidate_lock(tmp_path, monkeypatch, request):
    source = Path(os.environ.get('PYTHON_RUNTIME_TEST_LOCK') or
                  ROOT / 'telegram_connector/requirements.lock')
    candidate = tmp_path / 'selected candidate.lock'
    candidate.write_bytes(source.read_bytes() + b'\n# selected candidate fixture\n')
    monkeypatch.setenv('PYTHON_RUNTIME_TEST_LOCK', str(candidate))
    _, requirements, _ = request.getfixturevalue('cold_requirements')
    assert requirements.read_bytes() == candidate.read_bytes()
