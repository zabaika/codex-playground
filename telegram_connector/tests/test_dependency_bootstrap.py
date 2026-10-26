"""Cold pip resolution uses repo inputs, independent of operator caches."""
import hashlib
from html.parser import HTMLParser
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cold_requirements(tmp_path):
    project = tmp_path / "source with spaces/telegram_connector"
    project.mkdir(parents=True)
    assets = ROOT / "telegram_connector/third_party/pyaes"
    if assets.exists():
        shutil.copytree(assets, project / "third_party/pyaes")
    index = tmp_path / "fixture index"
    index.mkdir()
    # Only other dependencies are metadata-only fixtures. PyAES must be real.
    for name in ("telethon", "urllib3", "pytest"):
        version = importlib.metadata.version(name)
        prefix = f"{name}-{version}.dist-info"
        with zipfile.ZipFile(index / f"{name}-{version}-py3-none-any.whl", "w") as wheel:
            wheel.writestr(prefix + "/METADATA", (
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
                + ("Requires-Dist: pyaes\n" if name == "telethon" else "")
            ))
            wheel.writestr(prefix + "/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            wheel.writestr(prefix + "/RECORD", "")
    requirements = project / "requirements.txt"
    requirements.write_text(
        "--no-index\n--find-links " + index.as_uri() + "\n"
        + (ROOT / "telegram_connector/requirements.txt").read_text(),
    )
    home = tmp_path / "empty-home"
    home.mkdir()
    env = {
        "HOME": str(home), "PATH": os.defpath, "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    return project, requirements, env


def resolve(cold_requirements, tmp_path):
    _, requirements, env = cold_requirements
    report = tmp_path / "report.json"
    result = subprocess.run([
        sys.executable, "-I", "-m", "pip", "install", "--dry-run",
        "--ignore-installed", "--only-binary=:all:", "--report", str(report),
        "-r", str(requirements),
    ], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    return result, report


def test_cold_plan_uses_repo_pyaes_without_manual_find_links(cold_requirements, tmp_path):
    project, _, _ = cold_requirements
    result, report = resolve(cold_requirements, tmp_path)
    assert result.returncode == 0, result.stderr
    package = next(item for item in json.loads(report.read_text())["install"]
                   if item["metadata"]["name"].lower() == "pyaes")
    wheel = next((project / "third_party/pyaes").glob("*.whl"))
    class WheelIndex(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.href = dict(attrs)["href"]

    index = WheelIndex()
    index.feed((project / "third_party/pyaes/index.html").read_text())
    expected = parse_qs(urlsplit(index.href).fragment)["sha256"][0]
    assert package["download_info"]["url"] == wheel.as_uri()
    assert package["download_info"]["archive_info"]["hashes"]["sha256"] == expected
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_unavailable_reviewed_wheel_cannot_enter_cold_plan(cold_requirements, tmp_path, fault):
    project, _, _ = cold_requirements
    wheel = next((project / "third_party/pyaes").glob("*.whl"))
    if fault == "missing":
        wheel.unlink()
    else:
        wheel.write_bytes(wheel.read_bytes() + b"fixture tampering")
    result, _ = resolve(cold_requirements, tmp_path)
    assert result.returncode != 0
    if fault == "corrupt":
        assert "HASHES" in result.stderr.upper() or "HASH" in result.stderr.upper()
