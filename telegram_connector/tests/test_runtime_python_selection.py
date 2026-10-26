from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tomllib

import pytest
from common.python_runtime import probe_python


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("project_name", ["telegram_connector", "telegram_agent_bot"])
def test_below_policy_floor_is_rejected_before_runtime_is_changed(tmp_path, project_name):
    project = tmp_path / project_name
    (project / "scripts").mkdir(parents=True)
    (project / "config").mkdir()
    (project / "config/runtime.local.toml").write_text('[digest]\ntime="08:00"\n')
    shutil.copytree(REPO_ROOT / "common", tmp_path / "common")
    installer = project / "scripts/install_launch_agent.sh"
    shutil.copy2(REPO_ROOT / project_name / "scripts/install_launch_agent.sh", installer)
    with (REPO_ROOT / "common/config/python.toml").open("rb") as handle:
        floor = tomllib.load(handle)["python"]["minimum_version"]
    descriptor = probe_python(sys.executable)
    descriptor["version"] = [floor[0], floor[1] - 1, 0]
    old_python = tmp_path / "below-floor-python"
    old_python.write_text(
        f"#!{sys.executable}\n"
        f"print({json.dumps(descriptor)!r})\n"
    )
    old_python.chmod(0o755)
    staged = tmp_path / "staged"
    result = subprocess.run(
        ["/bin/bash", str(installer), "--staging-root", str(staged)],
        env={"PATH": os.defpath, "HOME": str(Path.home()), "PYTHON_BIN": str(old_python)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Python runtime:" in result.stderr
    assert not staged.exists()
